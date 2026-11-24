"""Consulting the DPO advisor from the desk (PLAN.md 4 and 5.4).

One tool per advisor skill (`advisor_possibly_excessive`, `advisor_extension`, ...). The agent chooses the
question; code builds the minimised summary from the case file (earlier requests are counted from the CRM
through the scoped tools, never typed by a model), the egress filter checks it, and the advice comes back to
the agent and into the case file. The advice is advice: R-EXCESSIVE and the extension still need a person.

When the advisor is switched off or its breaker is open (tier T2), the tool says so, and the agent hands the
case to a person directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, timedelta

from article15.advisor.egress import EgressRefused
from article15.advisor.models import Advice, CaseSummary, Question
from article15.advisor.service import AdvisorClient, AdvisorError
from article15.core.mcp_tools import McpCallError
from article15.core.resilience import CircuitBreaker
from article15.core.tools import FunctionTool, Tool, ToolResult, error_result
from article15.desk import pack as packing
from article15.desk.session import CaseSession
from article15.schemas import RequestType, Strict, System

DESCRIPTIONS = {
    Question.EXCESSIVE: "Ask the DPO advisor whether the request may be manifestly unfounded or excessive "
    "(for example repeated). The system sends a summary without personal data. A person still decides.",
    Question.EXTENSION: "Ask the DPO advisor whether the deadline may be extended (a complex or very large "
    "request). The system sends a summary without personal data. A person still decides.",
    Question.SPECIAL_CATEGORY: "Ask the DPO advisor about special-category data found about the customer.",
    Question.OTHER_RIGHTS: "Ask the DPO advisor about a request that also asks for another right.",
}


class NoArguments(Strict):
    pass


async def earlier_requests(session: CaseSession) -> tuple[int, int | None]:
    """Earlier data-protection requests in the 12 months before this one, and days since the latest. Needs
    the subject grant (identity verified); before that, nothing is known."""
    if session.case.identity is None or session.case.identity.subject_id is None:
        return 0, None
    try:
        listing = await session.hub.call("crm_get_customer")
        dates: list[date] = []
        for r in listing["records"]:
            if r["table"] == "prior_requests":
                rec = await session.hub.call(
                    "crm_read_record", {"table": "prior_requests", "record_id": r["record_id"]}
                )
                dates.append(date.fromisoformat(str(rec["fields"]["received_on"])[:10]))
    except McpCallError:
        return 0, None
    received = session.request.received_on
    recent = [d for d in dates if received - timedelta(days=365) <= d < received]
    last = max((d for d in dates if d < received), default=None)
    return len(recent), (received - last).days if last else None


async def build_summary(session: CaseSession, question: Question) -> CaseSummary:
    c = session.case
    intake = c.intake
    count, since = await earlier_requests(session)
    if c.discovery is not None:
        keys = [(r.system, r.table, r.record_id) for r in c.discovery.records]
    elif c.identity is not None and c.identity.subject_id is not None:
        # Not cached on the session: proof of control can add records before discovery is submitted.
        keys = list(session.scope if session.scope is not None else await packing.scope_index(session.hub))
    else:
        keys = []
    systems = {k[0] for k in keys}
    return CaseSummary(
        question=question,
        received_on=c.deadlines.received,
        due=c.deadlines.due,
        request_types=intake.request_types if intake else [RequestType.ACCESS],
        earlier_requests_last_12_months=count,
        days_since_last_request=since,
        records_found=len(keys),
        systems_with_records=len(systems & set(System)),
        special_category_mentions=len(c.redaction.special_category) if c.redaction else 0,
        on_behalf_of_someone_else=bool(intake and intake.on_behalf_of_someone_else),
    )


def never_values(session: CaseSession) -> list[str]:
    """Values that must never leave: the sender, the names and addresses the requester gave, and the fetched
    customer record's contact fields."""
    values = [session.request.sender]
    if session.case.intake is not None:
        i = session.case.intake
        values += [v for v in (i.claimed_name, i.claimed_phone) if v] + list(i.claimed_emails)
    for rec in session.records.values():
        if rec.table == "customers":
            values += [
                str(rec.fields.get(k, "")) for k in ("first_name", "last_name", "email", "phone", "street")
            ]
    return [v for v in values if len(v) >= 3]


class Unavailable(Exception):
    """The advisor could not be consulted (switched off, breaker open or failing: tier T2)."""


async def consult(
    session: CaseSession, client: AdvisorClient | None, breaker: CircuitBreaker, question: Question
) -> Advice:
    """Ask the advisor one question about the case; the advice also goes into the case file. Raises
    `EgressRefused` when the summary would carry personal data, `Unavailable` when there is no advisor."""
    if client is None or not breaker.allow():
        raise Unavailable("the DPO advisor is not available (tier T2)")
    try:
        summary = await build_summary(session, question)
        advice: Advice = await client.consult(summary, never_values(session))
    except AdvisorError as exc:
        breaker.record(False)
        raise Unavailable(str(exc)) from exc
    breaker.record(True)
    session.update(advice=[*session.case.advice, advice])
    return advice


def build_advisor_tools(
    session: CaseSession, client: AdvisorClient | None, breaker: CircuitBreaker
) -> list[Tool]:
    def make(question: Question) -> Callable[[NoArguments], Awaitable[ToolResult]]:
        async def consult_tool(_: NoArguments) -> ToolResult:
            try:
                advice = await consult(session, client, breaker, question)
            except EgressRefused as exc:
                return error_result("refused", str(exc))
            except Unavailable as exc:
                return error_result("unavailable", f"{exc}; hand the case to a person with case_escalate")
            return ToolResult(
                text=f"Advice ({advice.recommendation.value}): "
                + "; ".join(advice.reasons)
                + f". Sources: {', '.join(advice.sources)}. A person decides: give this advice as the reason "
                "when you call case_escalate."
            )

        return consult_tool

    # Code consults too: when its own checks send a case to a person (case_tools.triage), the person gets
    # the advice with the reason.
    session.consult = lambda question: consult(session, client, breaker, question)
    return [
        FunctionTool(f"advisor_{q.value}", DESCRIPTIONS[q], NoArguments, make(q), timeout_s=30.0)
        for q in Question
    ]
