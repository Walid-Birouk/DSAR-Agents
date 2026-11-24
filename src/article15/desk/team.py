"""The multi-agent desk (PLAN.md 5.2, M4): a coordinator and six specialists, called as tools.

Each specialist has its own role prompt (roles/<name>.md), the policy rules that apply to it (rendered from
policy.yaml), and only its own tools: the MCP tools of its stage and its stage-submit tool. A successful
submission ends the specialist's run, so its answer goes straight back to the coordinator. Specialists start
fresh on every call and see only the slice of the case that code builds for them (PLAN.md 5.5): Redaction sees
the tickets to read but not the history; Letter sees counts and categories, never records.

The coordinator sees the request, the specialists' answers and a short status line written by code after
each one. It can call the specialists, read the status and escalate; it cannot submit a stage, read a record
or write to anyone. When a stage pauses the case (waiting for the requester or a person), the coordinator's
run stops too, and the runner resumes it when something new happens.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from article15.advisor.models import Question
from article15.case.models import CaseState
from article15.company.register import Register
from article15.core.agent import Agent, Budget, Caps
from article15.core.agent_tool import AgentTool
from article15.core.events import EventSink
from article15.core.mcp_tools import escape_untrusted
from article15.core.model import Thinking, sha256_hex
from article15.core.provider import Provider
from article15.core.tools import Tool, ToolLogEntry, ToolManager, ToolResult
from article15.desk.session import CaseSession
from article15.policy.models import Policy
from article15.policy.render import render_policy_section
from article15.schemas import AgentName, Issue, Strict, System

A = AgentName
S = CaseState
ROLES_DIR = Path(__file__).with_name("roles")

READ_TOOLS = frozenset(
    {
        "crm_get_customer",
        "crm_read_record",
        "orders_search",
        "orders_read_record",
        "helpdesk_search_tickets",
        "helpdesk_read_ticket",
        "weblogs_search_events",
        "weblogs_read_record",
    }
)
TOOLS: dict[AgentName, frozenset[str]] = {
    A.INTAKE: frozenset({"case_submit_intake"}),
    A.IDENTITY: frozenset(
        {"crm_check_identity", "orders_check_reference", "notify_read_replies", "case_submit_identity"}
    ),
    A.DISCOVERY: READ_TOOLS | {"case_request_address_link", "case_submit_discovery"},
    A.REDACTION: frozenset({"helpdesk_read_ticket", "case_submit_redaction"}),
    A.LETTER: frozenset({"case_submit_letter"}),
    A.VALIDATOR: frozenset({"case_read_pack", "case_submit_validation"}),
}
SPECIALISTS = tuple(TOOLS)
COORDINATOR_TOOLS = frozenset({"case_get_status", "case_escalate"}) | {f"advisor_{q.value}" for q in Question}
DESCRIPTIONS: dict[AgentName, str] = {
    A.INTAKE: "Stage 1: the intake specialist reads the request and records what it asks.",
    A.IDENTITY: "Stage 2: the identity specialist checks who is asking (it may ask the requester).",
    A.DISCOVERY: "Stage 3: the discovery specialist finds the verified customer's records.",
    A.REDACTION: "Stage 4: the redaction specialist hides other people's data in the records found.",
    A.LETTER: "Stage 5: the letter specialist drafts the reply; the system assembles the pack.",
    A.VALIDATOR: "Stage 6: the validator checks the assembled pack and gives its verdict.",
}
SPECIALIST_CAPS = Caps(max_calls=16, max_input_tokens=400_000, max_seconds=300.0, max_cost_usd=0.12)


def tool_name(agent: AgentName) -> str:
    return f"{agent.value}_agent"


def role(agent: AgentName) -> str:
    return (ROLES_DIR / f"{agent.value}.md").read_text(encoding="utf-8").strip()


def system_prompt(agent: AgentName, policy: Policy, register: Register) -> str:
    """Role, then the rules that apply to this agent, then (for the letter only) the company register: stable
    parts first, so the provider's prefix cache works (PLAN.md 5.5)."""
    parts = [role(agent), render_policy_section(policy, agent)]
    if agent is A.LETTER:
        parts.append(register.render())
    return "\n\n".join(parts)


def prompts_hash(policy: Policy, register: Register) -> str:
    every = [system_prompt(a, policy, register) for a in (A.COORDINATOR, *SPECIALISTS)]
    return sha256_hex("\n\x00\n".join(every))[:16]


class Brief(Strict):
    note: str = Field(
        default="",
        max_length=2000,
        description="What the specialist needs beyond its slice of the case, e.g. the validator's issues.",
    )


class StageTool(Tool):
    """A specialist's stage-submit tool: a successful submission ends the specialist's run."""

    def __init__(self, inner: Tool) -> None:
        self.inner = inner
        self.name = inner.name
        self.description = inner.description
        self.timeout_s = inner.timeout_s

    @property
    def parameters(self) -> dict[str, Any]:
        return self.inner.parameters

    async def run(self, args: dict[str, Any]) -> ToolResult:
        result = await self.inner.run(args)
        if result.is_error:
            return result
        return ToolResult(text=result.text, stop=True, data=result.text)


class SpecialistTool(Tool):
    """A specialist as the coordinator sees it. After the specialist answers, code adds a status line; when
    the case is paused, the coordinator's run stops too."""

    def __init__(self, inner: AgentTool[Brief], session: CaseSession) -> None:
        self.inner = inner
        self.session = session
        self.name = inner.name
        self.description = inner.description
        self.timeout_s = inner.timeout_s

    @property
    def parameters(self) -> dict[str, Any]:
        return self.inner.parameters

    async def run(self, args: dict[str, Any]) -> ToolResult:
        result = await self.inner.run(args)
        text = f"{result.text}\n{status_line(self.session)}"
        if result.is_error:
            return ToolResult(text=text, is_error=True)
        return ToolResult(text=text, stop=self.session.pause is not None, data=result.data)


def issues_text(issues: list[Issue]) -> str:
    return "; ".join(f"{i.code}: {i.detail}" for i in issues) or "none listed"


def status_line(session: CaseSession) -> str:
    from article15.desk.case_tools import NEXT_STEP

    c = session.case
    parts = [f"STATUS: the case is at {c.state}; next: {NEXT_STEP.get(c.state, 'none')}"]
    if c.intake is not None and c.state is S.IDENTITY_CHECK:
        i = c.intake
        parts.append(
            f"intake: language {i.language.value}; asks for {', '.join(t.value for t in i.request_types)}"
            + ("; written for someone else" if i.on_behalf_of_someone_else else "")
            + ("; contains instructions to the desk" if i.embedded_instructions_detected else "")
            + (f"; note: {i.scope_note}" if i.scope_note else "")
        )
    if c.discovery is not None and c.state is S.DISCOVERY_DONE:
        parts.append(f"discovery: {len(c.discovery.records)} records")
    if c.validation is not None and c.state is S.REDACTION_DONE and c.validation.issues:
        parts.append("validator's issues: " + issues_text(c.validation.issues))
    if session.pause is not None:
        parts.append(f"paused: {session.pause.value}")
    return ". ".join(parts) + "."


# --- the case slices ---------------------------------------------------------------------------------------


def _with_note(lines: list[str], brief: Brief) -> str:
    if brief.note:
        lines += ["Note from the coordinator:", brief.note]
    return "\n".join(lines)


def _request_block(session: CaseSession) -> list[str]:
    r = session.request
    return [
        "<untrusted_request>",
        escape_untrusted(f"Subject: {r.subject_line}"),
        escape_untrusted(r.text),
        "</untrusted_request>",
    ]


def slice_for(agent: AgentName, session: CaseSession, brief: Brief) -> str:
    c, r = session.case, session.request
    head = [f"CASE {c.case_id}. Received on {r.received_on} by {r.channel.value} from {r.sender}."]
    if agent is A.INTAKE:
        d = c.deadlines
        lines = [*head, f"Reply due {d.due}.", "The request:", *_request_block(session)]
    elif agent is A.IDENTITY:
        i = c.intake
        lines = [*head]
        if i is not None:
            lines.append(
                f"Intake: claimed name {i.claimed_name or 'none'}; claimed emails "
                f"{', '.join(i.claimed_emails) or 'none'}; order numbers "
                f"{', '.join(i.account_references) or 'none'}"
                + ("; written for someone else" if i.on_behalf_of_someone_else else "")
                + ("; mandate provided" if i.mandate_provided else "")
                + "."
            )
        lines += ["The request:", *_request_block(session)]
        if session.follow_ups_sent:
            lines.append(
                f"{session.follow_ups_sent} follow-up question(s) were sent; the requester has replied: read "
                "the reply with notify_read_replies before deciding."
            )
    elif agent is A.DISCOVERY:
        subject = c.identity.subject_id if c.identity else None
        lines = [*head, f"The verified customer is {subject}."]
        if session.linked:
            lines.append(f"Addresses confirmed by the requester and now searchable: {len(session.linked)}.")
        if c.state is S.IDENTITY_VERIFIED and any(h.to_state is S.AWAITING_REQUESTER for h in c.history[-2:]):
            lines.append(
                "The requester has answered the address confirmation: search all four systems again."
            )
    elif agent is A.REDACTION:
        found = c.discovery.records if c.discovery else []
        tickets = [x.record_id for x in found if x.table == "tickets"]
        own_orders = sorted(x.record_id for x in found if x.system is System.ORDERS and x.table == "orders")
        lines = [
            *head,
            f"Tickets in the copy: {', '.join(tickets) or 'none'}. Read each one. Only their messages hold "
            "free text, and each message is a record of its own (its message ID as record_id, field 'body'); "
            "leave "
            "the records outside the helpdesk alone.",
            f"The customer's own orders: {', '.join(own_orders) or 'none'}. Their own order numbers, name "
            "and "
            "addresses stay readable; any other order number points to another customer.",
        ]
    elif agent is A.LETTER:
        counts: dict[str, int] = {}
        for x in c.discovery.records if c.discovery else []:
            counts[x.system.value] = counts.get(x.system.value, 0) + 1
        language = c.intake.language.value if c.intake else "fr"
        spans = len(c.redaction.spans) if c.redaction else 0
        special = len(c.redaction.special_category) if c.redaction else 0
        lines = [
            *head,
            f"Language: {language}. Records in the copy: {sum(counts.values())} "
            f"({', '.join(f'{s} {n}' for s, n in sorted(counts.items()))}).",
            f"Other people's data hidden: {'yes' if spans else 'no'} ({spans} passage(s)). Special-category "
            f"mentions flagged for the reviewer: {special}.",
        ]
        if c.validation is not None and c.validation.verdict.value != "APPROVED":
            lines.append(
                "Issues the validator found in the previous draft: " + issues_text(c.validation.issues)
            )
        if c.claims_problems:
            lines.append("The system's claims check of the previous draft: " + "; ".join(c.claims_problems))
    elif agent is A.VALIDATOR:
        # The validator starts fresh, so it is told what code itself has checked on this exact pack; without
        # it, a pack whose structured records it cannot see looks unverifiable (tuned on the dev smoke run).
        records = (session.pack or {}).get("records", [])
        by_system: dict[str, list[str]] = {}
        for rec in records:
            by_system.setdefault(rec["system"], []).append(rec["record_id"])
        listed = "; ".join(
            f"{s} {len(ids)} ({', '.join(ids[:12])}{', ...' if len(ids) > 12 else ''})"
            for s, ids in sorted(by_system.items())
        )
        spans = len(c.redaction.spans) if c.redaction else 0
        special = len(c.redaction.special_category) if c.redaction else 0
        lines = [
            *head,
            "The pack is assembled. Read it with case_read_pack, then give your verdict.",
            "What the system itself has checked on this pack (code, not a model):",
            f"- the copy holds {len(records)} records, exactly the ones discovery submitted, each returned "
            "by "
            f"the scoped servers for this customer: {listed or 'none'}. Records outside the helpdesk hold no "
            "free text, so case_read_pack shows the helpdesk text only.",
            f"- redaction verifier: {'passed' if c.redaction_check_passed else 'NOT passed'}; {spans} "
            "passage(s) hidden, nothing hidden is still readable, and no other customer's order number, "
            "email "
            "address or phone number is left readable.",
            "- claims cross-check of the letter against the copy: "
            + ("no problem found." if not c.claims_problems else "; ".join(c.claims_problems) + "."),
            f"- special-category mentions flagged: {special}. They stay in the copy and the reviewer "
            "acknowledges them when approving; flagged correctly, they are not a defect of the pack.",
        ]
    else:  # pragma: no cover
        raise ValueError(agent)
    return _with_note(lines, brief)


def coordinator_message(session: CaseSession) -> str:
    c, r = session.case, session.request
    d = c.deadlines
    return "\n".join(
        [
            f"CASE {c.case_id}",
            f"Received on {r.received_on} by {r.channel.value} from {r.sender}.",
            f"Deadlines computed by the system: reply due {d.due}; an extension, if one is needed, must be "
            f"announced by {d.extension_notice_by} and moves the deadline to {d.extended_due}.",
            "The request (for your judgment only; the specialists get their own slice):",
            *_request_block(session),
            "Start with intake_agent.",
        ]
    )


REPLY_ARRIVED = (
    "The requester has replied to the identity question. The case is back at IDENTITY_CHECK: call "
    "identity_agent."
)


def links_result(linked: int, not_linked: int) -> str:
    text = f"The requester has answered the address confirmation: {linked} address(es) confirmed"
    if not_linked:
        text += f", {not_linked} not confirmed"
    return text + ". The case is back at IDENTITY_VERIFIED: call discovery_agent to search again."


# --- building the team -------------------------------------------------------------------------------------


def _slicer(agent: AgentName, session: CaseSession) -> Callable[[Brief], str]:
    def render(brief: Brief) -> str:
        return slice_for(agent, session, brief)

    return render


@dataclass
class Team:
    coordinator: Agent
    manager: ToolManager
    system: str
    specialists: dict[AgentName, AgentTool[Brief]] = field(default_factory=dict)


def build_team(
    *,
    session: CaseSession,
    hub_tools: Sequence[Tool],
    case_tools: list[Tool],
    policy: Policy,
    register: Register,
    provider: Provider,
    model: str,
    thinking: Thinking,
    max_tokens: int | None,
    budget: Budget,
    sink: EventSink,
    log: list[ToolLogEntry],
    shared_provider_for: Callable[[AgentName], Provider] | None = None,
) -> Team:
    available = {t.name: t for t in [*hub_tools, *case_tools]}
    specialists: dict[AgentName, AgentTool[Brief]] = {}
    for agent in SPECIALISTS:
        own: list[Tool] = []
        for name in sorted(TOOLS[agent]):
            t = available[name]
            own.append(StageTool(t) if name.startswith("case_submit_") else t)
        manager = ToolManager(own, sink=sink, log=log)
        agent_provider = shared_provider_for(agent) if shared_provider_for else provider

        def make(a: AgentName = agent, m: ToolManager = manager, p: Provider = agent_provider) -> Agent:
            return Agent(
                name=a.value,
                provider=p,
                tools=m,
                model=model,
                thinking=thinking,
                max_tokens=max_tokens,
                sink=sink,
            )

        specialists[agent] = AgentTool(
            tool_name(agent),
            DESCRIPTIONS[agent],
            Brief,
            make,
            system_prompt(agent, policy, register),
            _slicer(agent, session),
            parent_budget=budget,
            caps=SPECIALIST_CAPS,
        )
    coordinator_tools: list[Tool] = [SpecialistTool(t, session) for t in specialists.values()]
    coordinator_tools += [available[n] for n in sorted(COORDINATOR_TOOLS)]
    manager = ToolManager(coordinator_tools, sink=sink, log=log)
    system = system_prompt(A.COORDINATOR, policy, register)
    coordinator = Agent(
        name=A.COORDINATOR.value,
        provider=provider,
        tools=manager,
        model=model,
        thinking=thinking,
        max_tokens=max_tokens,
        sink=sink,
    )
    return Team(coordinator=coordinator, manager=manager, system=system, specialists=specialists)
