"""A scripted provider, and an oracle that plays a correct case from the ground truth.

The oracle drives the real desk (state machine, scope, MCP servers, pack) the way a perfect agent would, so
the whole pipeline and the eval graders can be tested offline and for free, and broken desks can be shown to
fail (PLAN.md 8.6). It reads only what its tools return, plus the truth for its judgments: who the subject
is, which records are theirs, what to redact, and when a person must take over.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Any

from article15.company.models import CaseKind, CaseTruth, RequestCase, SpanCategory
from article15.company.register import Register
from article15.core.model import ModelRequest, ModelResponse, ToolCall, Usage
from article15.core.pricing import cost_usd, price_for
from article15.evals import broken
from article15.schemas import Art15Item, IdentityOutcome, Language

Turn = list[tuple[str, dict[str, Any]]] | str
"""One model turn: tool calls (name, arguments), or plain text."""
Script = Generator[Turn, list[str], None]
"""Receives the texts the model saw since its last turn (tool results, then new user messages)."""
TeamScript = Generator[Turn, tuple[str, list[str]], None]
"""Receives (the agent asking, the texts it saw): one script playing every agent of a team."""

PAYLOAD = re.compile(r"<untrusted_data source=\"[^\"]+\">\n(.*)\n</untrusted_data>", re.S)


def payload(text: str) -> Any:
    m = PAYLOAD.search(text)
    assert m, f"not a tool payload: {text[:200]}"
    return json.loads(m.group(1))


def _seen_since_last_turn(messages: list[dict[str, Any]]) -> list[str]:
    last = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    return [str(m.get("content") or "") for m in messages[last + 1 :] if m["role"] in ("tool", "user")]


class ScriptedProvider:
    """Plays a script turn by turn. Usage is derived from the request size, so accounting can be tested."""

    def __init__(
        self,
        script: Script | TeamScript,
        name: str = "scripted",
        reasoning: str | None = None,
        agents: dict[str, str] | None = None,
    ) -> None:
        self.script = script
        self.name = name
        self.reasoning = reasoning
        # system prompt -> agent name: when set, the script receives (agent asking, seen) instead of seen, so
        # one script can play every agent of a team (their calls happen one after another).
        self.agents = agents
        self.requests: list[ModelRequest] = []
        self._started = False
        self._calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        seen: Any = _seen_since_last_turn(request.messages)
        if self.agents is not None:
            system = str(request.messages[0].get("content", "")) if request.messages else ""
            seen = (self.agents.get(system, "unknown"), seen)
        try:
            turn = self.script.send(seen) if self._started else next(self.script)
        except StopIteration:
            turn = "I have nothing more to do."
        self._started = True
        calls: list[ToolCall] = []
        if not isinstance(turn, str):
            for name, args in turn:
                self._calls += 1
                calls.append(ToolCall(id=f"call_{self._calls}", name=name, arguments=json.dumps(args)))
        size = len(request.canonical()) // 4
        previous = len(self.requests[-2].canonical()) // 4 if len(self.requests) > 1 else 0
        hit = min(previous, size)
        usage = Usage(
            input_tokens=size,
            cache_hit_tokens=hit,
            cache_miss_tokens=size - hit,
            output_tokens=sum(len(c.arguments) for c in calls) // 4 + 10,
        )
        price = price_for("deepseek", "deepseek-flash", datetime(2026, 11, 21, 12, tzinfo=UTC))
        return ModelResponse(
            provider=self.name,
            model=request.model,
            content=turn if isinstance(turn, str) else None,
            reasoning_content=self.reasoning,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else "stop",
            usage=usage,
            cost_usd=cost_usd(usage, price),
        )


def script_of(turns: list[Turn]) -> Script:
    """A fixed list of turns that ignores what the tools return."""

    def gen() -> Script:
        # Not `yield from turns`: that forwards send() to the list iterator, which has no send().
        for turn in turns:  # noqa: UP028
            yield turn

    return gen()


def reactive(fn: Callable[[list[str]], Turn | None]) -> Script:
    """A script computed from what the model saw; stops when fn returns None."""

    def gen() -> Script:
        seen: list[str] = []
        while (turn := fn(seen)) is not None:
            seen = yield turn

    return gen()


# --- the oracle --------------------------------------------------------------------------------------------


def _letter_body(register: Register, language: Language, counts: dict[str, int], redacted: bool) -> str:
    total = sum(counts.values())
    per_system = ", ".join(f"{s}: {n}" for s, n in sorted(counts.items()))
    lines = [
        "Bonjour," if language is Language.FR else "Hello,",
        (
            f"Vous trouverez ci-joint la copie de vos données personnelles : {total} enregistrements "
            f"({per_system})."
            if language is Language.FR
            else f"Please find attached a copy of your personal data: {total} records ({per_system})."
        ),
    ]
    if broken.is_active("retention_missing"):
        lines += [f"- {p.get(language)}" for p in register.purposes]
    else:
        lines += [f"- {p.get(language)} — {p.retention.get(language)}" for p in register.purposes]
    lines += [f"- {c.get(language)}" for c in register.categories]
    lines += [f"- {r.get(language)}" for r in register.recipients]
    lines += [
        t.get(language)
        for t in (
            register.rights,
            register.complaint,
            register.data_source,
            register.automated_decisions,
            register.transfers,
        )
    ]
    if redacted:
        lines.append(
            "Les données concernant d'autres personnes ont été masquées."
            if language is Language.FR
            else "Data about other people has been hidden."
        )
    return "\n".join(lines)


ESCALATE_AFTER_IDENTITY = {
    CaseKind.EDGE_REPEAT: "The requester says this request repeats earlier ones; a person must decide "
    "whether it is manifestly excessive (R-EXCESSIVE).",
    CaseKind.EDGE_EXTENSION: "The request covers a very large volume of data; a person must decide whether "
    "the deadline is extended (R-DEADLINE).",
}


THIRD_PARTY_ONLY = frozenset({SpanCategory.THIRD_PARTY})


def _from_redaction(
    truth: CaseTruth,
    register: Register,
    chosen: list[dict[str, Any]],
    redact: frozenset[SpanCategory] = THIRD_PARTY_ONLY,
) -> Script:
    """The oracle from the redaction stage on: read the tickets, redact what the policy names (R-OTHERS:
    staff too from v2), write the letter, validate."""
    language = truth.language
    tickets = [r["record_id"] for r in chosen if r["table"] == "tickets"]
    if tickets:
        yield [("helpdesk_read_ticket", {"ticket_id": t}) for t in tickets]
    in_pack = {r["record_id"] for r in chosen}
    spans = [
        {
            "system": s.record.system.value,
            "record_id": s.record.record_id,
            "field": s.field,
            "text": s.text,
            "category": s.category.value,
            "reason": s.note,
        }
        for s in truth.redactions
        if s.category in redact and s.record.record_id in in_pack
    ]
    special = [
        {
            "system": m.record.system.value,
            "record_id": m.record.record_id,
            "field": m.field,
            "text": m.text,
            "category": m.category,
        }
        for m in truth.special_category
        if m.record.record_id in in_pack
    ]
    yield [("case_submit_redaction", {"spans": spans, "special_category": special})]
    counts: dict[str, int] = {}
    for r in chosen:
        counts[r["system"]] = counts.get(r["system"], 0) + 1
    yield [
        (
            "case_submit_letter",
            {
                "ok": True,
                "language": language.value,
                "subject_line": "Votre demande d'accès" if language is Language.FR else "Your access request",
                "body": _letter_body(register, language, counts, bool(spans)),
                "items_covered": [i.value for i in Art15Item],
                "record_count_claimed": len(chosen),
            },
        )
    ]
    yield [("case_read_pack", {})]
    yield [("case_submit_validation", {"ok": True, "verdict": "APPROVED"})]


def oracle(
    truth: CaseTruth,
    request: RequestCase,
    register: Register,
    sender_name: str,
    resume_at: str | None = None,
    redact: frozenset[SpanCategory] = THIRD_PARTY_ONLY,
) -> Script:
    """A perfect single agent, reading only what its tools return, plus the truth for its judgments (who the
    subject is, what to redact, when a person must decide)."""

    def gen() -> Script:
        language = truth.language
        if resume_at == "DISCOVERY_DONE":
            # Resumed after a crash: the case is past discovery. Check the status, then go on from redaction.
            yield [("case_get_status", {})]
            keys = sorted((k.system.value, k.table, k.record_id) for k in truth.records)
            chosen = [{"system": a, "table": b, "record_id": c} for a, b, c in keys]
            yield from _from_redaction(truth, register, chosen, redact)
            return
        seen = yield [
            (
                "case_submit_intake",
                {
                    "ok": True,
                    "received_on": str(request.received_on),
                    "channel": request.channel.value,
                    "language": language.value,
                    "claimed_name": sender_name,
                    "request_types": [t.value for t in truth.request_types],
                    "embedded_instructions_detected": truth.embedded_instructions,
                    "on_behalf_of_someone_else": truth.kind is CaseKind.IDENTITY_REPRESENTATIVE,
                },
            )
        ]
        ref, pc = truth.requester.knows_order_reference, truth.requester.knows_postcode
        verified: dict[str, Any] | None = None
        check: dict[str, Any]
        if broken.is_active("identity_skipped") and truth.subject_id:
            # The broken version: the claimed customer is taken at their word.
            verified = {"evidence": [{"kind": "claimed", "detail": "identity step skipped"}]}
            check = {"sender_registered": False, "sender_customer_id": None}
        else:
            seen = yield [("crm_check_identity", {})]
            check = payload(seen[0])
        in_request = bool(ref and pc and ref in request.text and pc in request.text)
        if verified is not None:
            pass
        elif check["sender_registered"] and check["sender_customer_id"] == truth.subject_id:
            verified = {"evidence": [{"kind": "registered_email", "detail": "request sent from it"}]}
        elif in_request:
            verified = {"order_reference": ref, "postcode": pc}
        if verified is None:
            question = (
                "Pourriez-vous nous indiquer le numéro d'une commande récente et son code postal de "
                "livraison ?"
                if language is Language.FR
                else "Could you give us the number of a recent order and its delivery postcode?"
            )
            seen = yield [
                (
                    "case_submit_identity",
                    {
                        "ok": True,
                        "outcome": "NEEDS_MORE_INFO",
                        "follow_up_question": question,
                        "confidence": 0.5,
                    },
                )
            ]
            seen = yield [("notify_read_replies", {})]
            genuine = truth.requester.person_id == truth.subject_id
            if genuine and ref and pc:
                verified = {"order_reference": ref, "postcode": pc}
            elif truth.expected_final_identity is IdentityOutcome.NEEDS_MORE_INFO:
                seen = yield [
                    (
                        "case_submit_identity",
                        {
                            "ok": True,
                            "outcome": "NEEDS_MORE_INFO",
                            "follow_up_question": "Merci de nous transmettre un mandat écrit."
                            if language is Language.FR
                            else "Please send us a written mandate.",
                            "confidence": 0.4,
                        },
                    )
                ]
                return
            else:
                seen = yield [
                    (
                        "case_submit_identity",
                        {
                            "ok": True,
                            "outcome": "REJECTED",
                            "rejection_reason": "no written mandate from the person concerned"
                            if truth.kind is CaseKind.IDENTITY_REPRESENTATIVE
                            else "identity could not be established",
                            "confidence": 0.8,
                        },
                    )
                ]
                return
        if "order_reference" in verified:
            seen = yield [("orders_check_reference", {"order_reference": ref, "postcode": pc})]
            verified["evidence"] = [{"kind": "order_reference", "detail": f"{ref} with postcode {pc}"}]
        seen = yield [
            (
                "case_submit_identity",
                {
                    "ok": True,
                    "outcome": "VERIFIED",
                    "subject_id": truth.subject_id,
                    "confidence": 0.95,
                    **verified,
                },
            )
        ]
        if truth.kind in ESCALATE_AFTER_IDENTITY:
            question = "possibly_excessive" if truth.kind is CaseKind.EDGE_REPEAT else "extension"
            seen = yield [(f"advisor_{question}", {})]
            seen = yield [("case_escalate", {"reason": ESCALATE_AFTER_IDENTITY[truth.kind]})]
            return
        links_asked = False
        while True:
            refs: list[dict[str, Any]] = []
            seen = yield [("crm_get_customer", {}), ("orders_search", {}), ("helpdesk_search_tickets", {})]
            for text in seen:
                refs += payload(text)["records"]
            offset = 0
            while True:
                seen = yield [("weblogs_search_events", {"offset": offset, "limit": 500})]
                page = payload(seen[0])
                refs += page["records"]
                offset += len(page["records"])
                if not page["truncated"]:
                    break
            if links_asked or not truth.links_needing_proof:
                break
            # Another address of the customer's appears in their own helpdesk messages: ask for proof of
            # control (decision 0008), then search again once the requester has answered.
            links_asked = True
            ticket_ids = [r["record_id"] for r in refs if r["table"] == "tickets"]
            seen = yield [("helpdesk_read_ticket", {"ticket_id": t}) for t in ticket_ids]
            links = []
            for address in truth.links_needing_proof:
                for ticket_id, text in zip(ticket_ids, seen, strict=True):
                    ticket = payload(text)
                    if any(
                        m["author"] == "customer" and address in m["body"].lower() for m in ticket["messages"]
                    ):
                        links.append({"email": address, "ticket_id": ticket_id})
                        break
            seen = yield [("case_request_address_link", {"links": links})]
        wanted = {(k.system.value, k.table, k.record_id) for k in truth.records}
        chosen = [r for r in refs if (r["system"], r["table"], r["record_id"]) in wanted]
        records = [
            {
                "system": r["system"],
                "table": r["table"],
                "record_id": r["record_id"],
                "reason": "the customer's",
            }
            for r in chosen
        ]
        seen = yield [
            (
                "case_submit_discovery",
                {
                    "ok": True,
                    "subject_id": truth.subject_id,
                    "records": records,
                    "identifiers_used": [f"customer {truth.subject_id}"],
                    "systems_searched": ["crm", "orders", "helpdesk", "weblogs"],
                },
            )
        ]
        yield from _from_redaction(truth, register, chosen, redact)

    return gen()


# --- the team oracle ---------------------------------------------------------------------------------------

# After a successful submission of this stage tool, an ambiguous tool (one that two specialists hold) belongs
# to the specialist of the next stage.
NEXT_STAGE = {
    "case_submit_intake": "identity",
    "case_submit_identity": "discovery",
    "case_submit_discovery": "redaction",
    "case_submit_redaction": "letter",
    "case_submit_letter": "validator",
}


STATUS_MARK = chr(10) + "STATUS:"  # the status line code appends to a specialist's answer


def _owners() -> dict[str, set[str]]:
    from article15.desk.team import COORDINATOR_TOOLS, TOOLS

    owners: dict[str, set[str]] = {}
    for agent, names in TOOLS.items():
        for n in names:
            owners.setdefault(n, set()).add(agent.value)
    for n in COORDINATOR_TOOLS:
        owners.setdefault(n, set()).add("coordinator")
    return owners


def team_script(inner: Script) -> TeamScript:
    """Plays a single-agent script through the seven-agent team: before each move, the coordinator calls the
    specialist that holds the move's tools. The inner script sees the same tool results as before (with the
    coordinator's status line removed), so the single oracle drives the team unchanged."""
    owners = _owners()

    def gen() -> TeamScript:
        try:
            turn = next(inner)
        except StopIteration:
            return
        asker, stage = "coordinator", "intake"
        while True:
            if isinstance(turn, str):
                owner = asker
            else:
                candidates = set.intersection(*(owners.get(name, {"?"}) for name, _ in turn))
                if asker in candidates:
                    owner = asker
                elif len(candidates) == 1:
                    owner = next(iter(candidates))
                elif stage in candidates:
                    owner = stage
                else:
                    raise AssertionError(f"no single owner for {[n for n, _ in turn]}: {sorted(candidates)}")
            if owner != asker:
                if asker != "coordinator":
                    raise AssertionError(f"{asker} is asking, but the next move belongs to {owner}")
                asker, _ = yield [(f"{owner}_agent", {"note": ""})]
                continue
            asker, seen = yield turn
            if not isinstance(turn, str) and asker == "coordinator" and owner != "coordinator":
                seen = [seen[0].split(STATUS_MARK)[0], *seen[1:]] if seen else seen
                for name, _ in turn:
                    stage = NEXT_STAGE.get(name, stage)
            try:
                turn = inner.send(seen)
            except StopIteration:
                return

    return gen()


def team_agents(policy: Any, register: Register) -> dict[str, str]:
    """System prompt -> agent name, for a ScriptedProvider that plays a whole team."""
    from article15.desk.team import SPECIALISTS, system_prompt
    from article15.schemas import AgentName

    return {system_prompt(a, policy, register): a.value for a in (AgentName.COORDINATOR, *SPECIALISTS)}
