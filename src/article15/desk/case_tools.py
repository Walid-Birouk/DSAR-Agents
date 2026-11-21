"""Stage tools: how agents hand their typed results to code (decision 0010).

Each `case_submit_*` tool takes one typed handoff (schemas.py), checks it against what code knows, and moves
the case through the state machine. Anything wrong comes back to the model as one line it can act on:
`invalid:` (fix the submission), `refused:` (a rule stops it, named by ID). Code, not the model, does the
consequential parts: issuing the subject grant, sending the follow-up question, fetching records, applying
redactions, assembling and hashing the pack.

The single-agent baseline gets every stage tool. In M4 each specialist gets only its own.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable

from pydantic import Field

from article15.case.models import CaseState
from article15.core.mcp_tools import McpCallError
from article15.core.tools import FunctionTool, Tool, ToolResult, error_result
from article15.desk import pack as packing
from article15.desk.claims import COPY_FORMAT, check_claims
from article15.desk.redaction import RedactionSubmission, masked_fields, resolve, verify
from article15.desk.session import MAX_FOLLOW_UPS, MAX_VALIDATION_ROUNDS, CaseSession, Pause, refusal
from article15.schemas import (
    DiscoveryResult,
    IdentityDecision,
    IdentityOutcome,
    IntakeResult,
    Language,
    LetterDraft,
    RequestType,
    Strict,
    ValidationReport,
    Verdict,
)
from article15.scope.authority import ScopeRefused

S = CaseState
STAGE_TOOL_TIMEOUT_S = 60.0

FOLLOW_UP_SUBJECT = {
    Language.FR: "Votre demande d'accès {case_id} : vérification de votre identité",
    Language.EN: "Your access request {case_id}: checking your identity",
}

NEXT_STEP: dict[CaseState, str] = {
    S.RECEIVED: "intake: read the request and call case_submit_intake",
    S.INTAKE_DONE: "identity check",
    S.IDENTITY_CHECK: "identity: check the requester and call case_submit_identity",
    S.AWAITING_REQUESTER: "wait for the requester's reply",
    S.IDENTITY_VERIFIED: "discovery: search the four systems and call case_submit_discovery",
    S.DISCOVERY_DONE: "redaction: read the helpdesk tickets and call case_submit_redaction",
    S.REDACTION_DONE: "letter: call case_submit_letter",
    S.DRAFT_READY: "validation: read the pack with case_read_pack and call case_submit_validation",
    S.VALIDATED: "waiting for a person's approval",
    S.AWAITING_APPROVAL: "nothing: a person reviews the pack",
}


LINK_SUBJECT = {
    Language.FR: "Votre demande d'accès {case_id} : confirmation d'une autre adresse",
    Language.EN: "Your access request {case_id}: confirming another address",
}
LINK_BODY = {
    Language.FR: (
        "Bonjour,\nVos messages au service client mentionnent aussi l'adresse {addresses}. Pour inclure les "
        "données liées à cette adresse, nous y avons envoyé un code de confirmation. Si cette adresse est "
        "bien la vôtre, répondez à ce message en indiquant "
        "le code.\nLe service protection des données de Ferlane"
    ),
    Language.EN: (
        "Hello,\nYour messages to customer care also mention the address {addresses}. To include the data "
        "linked to it, we have sent a confirmation code there. If the address is yours, reply to this "
        "message with the code.\nFerlane's data protection desk"
    ),
}


class NoArguments(Strict):
    pass


class LinkRequest(Strict):
    email: str = Field(description="The other email address, exactly as the customer wrote it.")
    ticket_id: str = Field(description="The ticket whose customer message contains it.")


class AddressLinks(Strict):
    links: list[LinkRequest] = Field(min_length=1, max_length=3)


class Escalation(Strict):
    reason: str = Field(min_length=1, description="Why a person must take over, in one or two sentences.")


def _stop(text: str, session: CaseSession, pause: Pause) -> ToolResult:
    session.pause = pause
    return ToolResult(text=text, stop=True, data=session.case.state)


def _wrong_stage(session: CaseSession, tool: str, expected: CaseState) -> ToolResult:
    return error_result(
        "refused",
        f"R-STAGE-ORDER: {tool} is used at {expected}, but the case is at {session.state}; "
        f"next step: {NEXT_STEP.get(session.state, 'none')}",
    )


def _to_human(session: CaseSession, reason: str) -> ToolResult:
    failures = session.advance(S.NEEDS_HUMAN, reason)
    if failures:
        return error_result("refused", refusal(failures))
    return _stop(
        "The case is now with a person (NEEDS_HUMAN). Your work on it is finished.",
        session,
        Pause.WITH_A_PERSON,
    )


def build_case_tools(session: CaseSession) -> list[Tool]:
    def tool[M: Strict](
        name: str, description: str, model: type[M], fn: Callable[[M], Awaitable[ToolResult]]
    ) -> Tool:
        return FunctionTool(name, description, model, fn, timeout_s=STAGE_TOOL_TIMEOUT_S)

    async def get_status(_: NoArguments) -> ToolResult:
        d = session.case.deadlines
        return ToolResult(
            text=(
                f"case {session.case.case_id} is at {session.state}; next: "
                f"{NEXT_STEP.get(session.state, 'none')}. Received {d.received}, reply due {d.due} "
                f"(extended deadline {d.extended_due}, an extension must be announced by "
                f"{d.extension_notice_by}). Policy {session.policy.policy_id} v{session.policy.version}."
            )
        )

    # --- intake ---------------------------------------------------------------------------------------

    async def submit_intake(r: IntakeResult) -> ToolResult:
        if session.state is not S.RECEIVED:
            return _wrong_stage(session, "case_submit_intake", S.RECEIVED)
        req = session.request
        if r.received_on != req.received_on or r.channel != req.channel:
            return error_result(
                "invalid",
                f"received_on must be {req.received_on} and channel {req.channel.value} (from the case "
                "details)",
            )
        if not r.ok:
            return _to_human(session, f"intake failed: {r.error}")
        other_rights = any(t is not RequestType.ACCESS for t in r.request_types)
        flags = session.case.flags.model_copy(update={"other_rights_requested": other_rights})
        session.update(intake=r, flags=flags)
        session.advance(S.INTAKE_DONE, "intake recorded")
        failures = session.advance(S.IDENTITY_CHECK)
        if failures:
            return _to_human(session, refusal(failures))
        return ToolResult(
            text="Intake recorded. The case is at IDENTITY_CHECK. Next: check the requester's identity, then "
            "call case_submit_identity."
        )

    # --- identity -------------------------------------------------------------------------------------

    async def submit_identity(d: IdentityDecision) -> ToolResult:
        if session.state is not S.IDENTITY_CHECK:
            return _wrong_stage(session, "case_submit_identity", S.IDENTITY_CHECK)
        if not d.ok:
            return _to_human(session, f"identity check failed: {d.error}")
        if d.outcome is IdentityOutcome.VERIFIED:
            assert d.subject_id is not None
            try:
                token = session.authority.grant_subject(
                    session.identity_token,
                    d.subject_id,
                    session.request_text,
                    order_reference=d.order_reference,
                    postcode=d.postcode,
                )
            except ScopeRefused as exc:
                return error_result("refused", f"{exc.rule_id}: {exc}")
            session.hub.holder.token = token
            session.update(identity=d)
            failures = session.advance(S.IDENTITY_VERIFIED, f"verified: {d.subject_id}")
            if failures:
                return error_result("refused", refusal(failures))
            return ToolResult(
                text=f"Identity verified: customer {d.subject_id}. The record tools now return this "
                "customer's records only. Next: discovery in all four systems, then case_submit_discovery."
            )
        if d.outcome is IdentityOutcome.NEEDS_MORE_INFO:
            if session.follow_ups_sent >= MAX_FOLLOW_UPS:
                return error_result(
                    "refused",
                    f"R-IDENTITY: {MAX_FOLLOW_UPS} follow-up questions were already sent; decide VERIFIED or "
                    "REJECTED, or escalate with case_escalate",
                )
            assert d.follow_up_question is not None
            language = session.case.intake.language if session.case.intake else Language.FR
            session.notifier.send(
                session.case.case_id,
                to=session.request.sender,
                subject=FOLLOW_UP_SUBJECT[language].format(case_id=session.case.case_id),
                body=d.follow_up_question,
                at=session.clock(),
            )
            session.follow_ups_sent += 1
            session.update(identity=d)
            failures = session.advance(S.AWAITING_REQUESTER, "follow-up question sent")
            if failures:
                return error_result("refused", refusal(failures))
            return _stop(
                "Question sent to the requester. The case waits for their reply; you will be told when it "
                "arrives.",
                session,
                Pause.AWAITING_REQUESTER,
            )
        session.update(identity=d, rejection_reason=d.rejection_reason)
        failures = session.advance(S.REJECTED, "identity not established")
        if failures:
            return error_result("refused", refusal(failures))
        return _stop(
            "Request rejected; the reason is recorded. Your work on it is finished.", session, Pause.REJECTED
        )

    # --- discovery ------------------------------------------------------------------------------------

    async def submit_discovery(r: DiscoveryResult) -> ToolResult:
        if session.state is not S.IDENTITY_VERIFIED:
            return _wrong_stage(session, "case_submit_discovery", S.IDENTITY_VERIFIED)
        if not r.ok:
            return _to_human(session, f"discovery failed: {r.error}")
        verified = session.case.identity.subject_id if session.case.identity else None
        if r.subject_id != verified:
            return error_result("invalid", f"subject_id must be the verified customer, {verified}")
        if r.systems_failed:
            session.update(discovery=r, partial_systems=r.systems_failed)
            failures = session.advance(S.PARTIAL, f"systems failed: {', '.join(r.systems_failed)}")
            if failures:
                return error_result("refused", refusal(failures))
            return _stop(
                "The case is marked PARTIAL with the failed systems named; it will not be sent incomplete.",
                session,
                Pause.PARTIAL,
            )
        try:
            if session.scope is None:
                session.scope = await packing.scope_index(session.hub)
            keys = [(ref.system, ref.table, ref.record_id) for ref in r.records]
            unknown = [k for k in dict.fromkeys(keys) if k not in session.scope]
            if unknown:
                shown = ", ".join(f"{s.value}/{t}/{i}" for s, t, i in unknown[:10])
                more = f" (and {len(unknown) - 10} more)" if len(unknown) > 10 else ""
                return error_result(
                    "invalid",
                    f"{len(unknown)} record(s) were not returned by any search for this customer: "
                    f"{shown}{more}",
                )
            duplicates = [k for k, n in Counter(keys).items() if n > 1]
            if duplicates:
                return error_result("invalid", f"{len(duplicates)} record(s) are listed more than once")
            session.records = await packing.fetch_records(session.hub, keys)
        except McpCallError as exc:
            return error_result("unavailable", f"records could not be fetched for the pack ({exc})")
        session.update(discovery=r)
        failures = session.advance(S.DISCOVERY_DONE, f"{len(keys)} records")
        if failures:
            return error_result("refused", refusal(failures))
        counts = Counter(k[0].value for k in keys)
        summary = ", ".join(f"{s} {n}" for s, n in sorted(counts.items()))
        return ToolResult(
            text=f"Discovery recorded: {len(keys)} records ({summary}). Code fetched them for the pack. "
            "Next: redaction. Read every helpdesk ticket, then call case_submit_redaction."
        )

    async def request_links(r: AddressLinks) -> ToolResult:
        if session.state is not S.IDENTITY_VERIFIED:
            return _wrong_stage(session, "case_request_address_link", S.IDENTITY_VERIFIED)
        started: list[str] = []
        problems: list[str] = []
        for link in r.links:
            try:
                session.authority.start_link(session.hub.holder.token, link.email, link.ticket_id)
                started.append(link.email.strip().lower())
            except ScopeRefused as exc:
                problems.append(f"{link.email}: {exc}")
        if not started:
            return error_result("refused", "R-NO-FOREIGN-DATA: " + "; ".join(problems))
        language = session.case.intake.language if session.case.intake else Language.FR
        session.notifier.send(
            session.case.case_id,
            to=session.request.sender,
            subject=LINK_SUBJECT[language].format(case_id=session.case.case_id),
            body=LINK_BODY[language].format(addresses=", ".join(started)),
            at=session.clock(),
        )
        session.pending_links = started
        failures = session.advance(
            S.AWAITING_REQUESTER, f"proof of control asked for {len(started)} address(es)"
        )
        if failures:
            return error_result("refused", refusal(failures))
        skipped = f" Not sent: {'; '.join(problems)}." if problems else ""
        return _stop(
            f"A confirmation code was sent to {len(started)} address(es), and the requester was asked to "
            f"send it back.{skipped} The case waits for their reply; you will be told when it arrives.",
            session,
            Pause.AWAITING_REQUESTER,
        )

    # --- redaction ------------------------------------------------------------------------------------

    async def submit_redaction(sub: RedactionSubmission) -> ToolResult:
        if session.state is not S.DISCOVERY_DONE:
            return _wrong_stage(session, "case_submit_redaction", S.DISCOVERY_DONE)
        fields = packing.text_fields(session.records.values())
        plan, errors = resolve(sub, fields)
        if plan is None:
            return error_result("invalid", "; ".join(errors))
        language = session.case.intake.language if session.case.intake else Language.FR
        problems = verify(fields, plan, language)
        if problems:
            return error_result(
                "refused",
                "R-OTHERS: the redaction check failed: "
                + "; ".join(problems)
                + ". Add a span for each place (leave occurrence empty to hide every occurrence in a field).",
            )
        flags = session.case.flags.model_copy(update={"special_category_found": bool(plan.special_category)})
        session.update(redaction=plan, redaction_check_passed=True, flags=flags)
        failures = session.advance(S.REDACTION_DONE, f"{len(plan.spans)} spans")
        if failures:
            session.update(redaction_check_passed=False)
            return error_result("refused", refusal(failures))
        special = (
            f" {len(plan.special_category)} special-category mention(s) flagged for the reviewer."
            if plan.special_category
            else ""
        )
        return ToolResult(
            text=f"Redaction applied and verified: {len(plan.spans)} span(s).{special} Next: the letter, "
            "case_submit_letter."
        )

    # --- letter and pack ------------------------------------------------------------------------------

    async def submit_letter(d: LetterDraft) -> ToolResult:
        if session.state is not S.REDACTION_DONE:
            return _wrong_stage(session, "case_submit_letter", S.REDACTION_DONE)
        if not d.ok:
            return _to_human(session, f"letter failed: {d.error}")
        intake, identity, discovery, plan = (
            session.case.intake,
            session.case.identity,
            session.case.discovery,
            session.case.redaction,
        )
        assert intake and identity and identity.subject_id and discovery and plan
        if d.language is not intake.language:
            return error_result(
                "invalid", f"the letter must be in the requester's language ({intake.language})"
            )
        pack = packing.build_pack(
            case_id=session.case.case_id,
            policy_id=session.policy.policy_id,
            policy_version=session.policy.version,
            generated_at=session.clock(),
            subject_id=identity.subject_id,
            language=intake.language,
            letter=d,
            refs=discovery.records,
            records=session.records,
            plan=plan,
        )
        session.pack = pack
        session.update(
            letter=d,
            pack_hash=packing.hash_pack(pack),
            copy_format=COPY_FORMAT,
            claims_problems=check_claims(d, pack),
        )
        failures = session.advance(S.DRAFT_READY, "pack assembled")
        if failures:
            return error_result("refused", refusal(failures))
        return ToolResult(
            text=f"Letter recorded and pack assembled: {len(pack['records'])} records, "
            f"{len(pack['redactions'])} redaction(s). Next: check it with case_read_pack, then "
            "case_submit_validation."
        )

    async def read_pack(_: NoArguments) -> ToolResult:
        if session.pack is None or session.case.letter is None or session.case.redaction is None:
            return error_result(
                "refused", "there is no pack yet; it is assembled when the letter is submitted"
            )
        language = Language(session.pack["language"])
        fields = packing.text_fields(session.records.values())
        masked = masked_fields(fields, session.case.redaction, language)
        free_text = [
            f"- {s.value} {rid} {f}: {text}"
            for (s, rid, f), text in sorted(
                masked.items(), key=lambda kv: (kv[0][0].value, kv[0][1], kv[0][2])
            )
            if f in ("body", "subject")
        ]
        letter = session.case.letter
        text = "\n".join(
            [
                f"PACK for case {session.case.case_id} (hash {session.case.pack_hash})",
                f"Language: {language.value}. Records: {len(session.pack['records'])} "
                f"{session.pack['record_counts']}.",
                f"Letter subject: {letter.subject_line}",
                f"Letter items_covered: {', '.join(i.value for i in letter.items_covered)}; "
                f"record_count_claimed: {letter.record_count_claimed}",
                "Letter body:",
                letter.body,
                f"Redactions: {len(session.pack['redactions'])}; special-category flags: "
                f"{len(session.pack['special_category'])}.",
                "System checks of the letter's claims: "
                + (
                    "all passed."
                    if not session.case.claims_problems
                    else "; ".join(session.case.claims_problems)
                ),
                "Free text as it appears in the pack:",
                *free_text,
            ]
        )
        return ToolResult(text=f'<untrusted_data source="case_read_pack">\n{text}\n</untrusted_data>')

    # --- validation -----------------------------------------------------------------------------------

    async def submit_validation(v: ValidationReport) -> ToolResult:
        if session.state is not S.DRAFT_READY:
            return _wrong_stage(session, "case_submit_validation", S.DRAFT_READY)
        session.update(validation=v)
        if v.ok and v.verdict is Verdict.APPROVED:
            failures = session.advance(S.VALIDATED, "validator approved")
            if failures:
                return error_result("refused", refusal(failures))
            session.advance(S.AWAITING_APPROVAL, "presented for approval")
            return _stop(
                "The pack is validated and waits for a person's approval. Your work on this case is "
                "finished.",
                session,
                Pause.AWAITING_APPROVAL,
            )
        session.validation_rounds += 1
        if session.validation_rounds >= MAX_VALIDATION_ROUNDS:
            return _to_human(session, f"validation not passed after {session.validation_rounds} rounds")
        failures = session.advance(S.REDACTION_DONE, "validation found issues; redraft")
        if failures:
            return error_result("refused", refusal(failures))
        return ToolResult(
            text="Validation found issues. The case is back at REDACTION_DONE: correct the letter and submit "
            "it again with case_submit_letter."
        )

    async def escalate(e: Escalation) -> ToolResult:
        return _to_human(session, e.reason)

    return [
        tool(
            "case_get_status",
            "Show the case's stage, its deadlines and the next step.",
            NoArguments,
            get_status,
        ),
        tool(
            "case_submit_intake",
            "Stage 1. Record what the request says. Set ok to true.",
            IntakeResult,
            submit_intake,
        ),
        tool(
            "case_submit_identity",
            "Stage 2. Record the identity decision: VERIFIED (with evidence; order_reference and postcode "
            "exactly as the requester wrote them, when they are the evidence), NEEDS_MORE_INFO (with "
            "follow_up_question, which the system sends) or REJECTED (with rejection_reason). Set ok to "
            "true.",
            IdentityDecision,
            submit_identity,
        ),
        tool(
            "case_request_address_link",
            "Stage 3, when a helpdesk message the customer wrote gives another email address of theirs: ask "
            "the requester to prove it is theirs. The system mails a one-time code to that address and asks "
            "the requester to send it back; the address joins the search only if they do. Up to 3 addresses.",
            AddressLinks,
            request_links,
        ),
        tool(
            "case_submit_discovery",
            "Stage 3. List every record of the verified customer, with the reason each belongs to them, and "
            "the candidates you left out. Set ok to true.",
            DiscoveryResult,
            submit_discovery,
        ),
        tool(
            "case_submit_redaction",
            "Stage 4. Quote each piece of other people's personal data to hide, exactly as written, and flag "
            "special-category data about the customer.",
            RedactionSubmission,
            submit_redaction,
        ),
        tool(
            "case_submit_letter",
            "Stage 5. Submit the reply letter in the requester's language. The system attaches the copy of "
            "the data and assembles the pack. Set ok to true.",
            LetterDraft,
            submit_letter,
        ),
        tool("case_read_pack", "Show the assembled pack, for validation.", NoArguments, read_pack),
        tool(
            "case_submit_validation",
            "Stage 6. Give the verdict on the pack: APPROVED, CONDITIONAL or REJECTED, with the issues "
            "found. Set ok to true.",
            ValidationReport,
            submit_validation,
        ),
        tool(
            "case_escalate",
            "Hand the case to a person: other rights requested, a possibly excessive or repeated request, or "
            "anything you cannot resolve.",
            Escalation,
            escalate,
        ),
    ]
