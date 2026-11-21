"""Run a case end to end with the single agent (M2; the E1 baseline).

For each case the runner builds a fresh desk: its own scope and notifier stores, a simulated clock starting
on the day the request was received, fresh random secrets (tokens never leave the process), and the five MCP
servers in-process (decision 0010). It runs the agent until the case pauses (waiting for the requester, for a
person's approval, with a person, rejected or partial), answers follow-up questions with the simulated
requester, and writes everything to the run directory:

    case.json        the case file: state, history, every handoff
    pack.json        the pack whose hash an approval would sign (when one was assembled)
    letter.md        the letter, for reading
    transcript.json  the full conversation (personal data: never telemetry; runs/ is git-ignored)
    tool_log.jsonl   every tool call with its arguments and result
    events.jsonl     the trace events (no contents)
    outcome.json     the summary, with a fingerprint that a replay must reproduce exactly
    runtime/         the case's scope and notifier stores (follow-up questions and replies)
"""

from __future__ import annotations

import json
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from article15.case.models import Case, CaseState
from article15.company.models import CaseTruth, Person, RequestCase
from article15.company.register import Register, load_register
from article15.company.store import DataPaths
from article15.core.agent import Agent, Budget, Caps, StopReason
from article15.core.events import EventSink, JsonlSink
from article15.core.mcp_tools import TokenHolder, connect
from article15.core.model import Message, Thinking, canonical_json, sha256_hex
from article15.core.provider import Provider
from article15.core.tools import ToolManager
from article15.desk import prompts, requester
from article15.desk.case_tools import build_case_tools
from article15.desk.clock import REQUESTER_REPLY_DELAY, SimClock
from article15.desk.pack import pack_bytes
from article15.desk.session import CaseSession, Pause
from article15.mcp_servers.common import ServerContext
from article15.mcp_servers.registry import SERVER_NAMES, build_named
from article15.notifier.store import NotifierStore
from article15.policy.models import Policy, load_policy
from article15.schemas import AgentName
from article15.scope.authority import ScopeAuthority, ScopeResolver
from article15.scope.store import ScopeStore
from article15.settings import DEFAULT_GRANT_TTL, DEFAULT_TOOL_TIMEOUT_S

M2_CASES = ("A15-004", "A15-017", "A15-122", "A15-045", "A15-107")
MAX_REPLY_ROUNDS = 1  # the generator labels the identity decision after one answered follow-up
# Only code writes to the requester (the follow-up question comes from the identity decision), so no model can
# send anything to anyone (PLAN.md 5.2).
WITHHELD_TOOLS = frozenset({"notify_post_message"})


@dataclass(frozen=True)
class RunConfig:
    data: DataPaths
    out_dir: Path
    model: str
    thinking: Thinking = Thinking.OFF
    caps: Caps = field(default_factory=Caps)
    max_output_tokens: int | None = 8192
    tool_timeout_s: float = DEFAULT_TOOL_TIMEOUT_S
    grant_ttl: timedelta = DEFAULT_GRANT_TTL


class CaseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    final_state: CaseState
    pause: Pause | None
    stop_reason: StopReason
    error: str | None
    states: list[CaseState]
    pack_hash: str | None
    follow_ups: int
    tool_calls: list[str]
    totals: dict[str, float | int]
    policy_version: str
    prompt_hash: str
    model: str
    thinking: Thinking
    max_output_tokens: int | None
    elapsed_s: float

    def fingerprint(self) -> str:
        """What a replay must reproduce: everything but the wall time."""
        return sha256_hex(canonical_json(self.model_dump(mode="json", exclude={"elapsed_s"})))


@dataclass
class Desk:
    """What a run needs beyond the case: the policy, the register and (for the simulation) the people."""

    policy: Policy
    register: Register
    people: dict[str, Person]

    @classmethod
    def load(cls, people: list[Person]) -> Desk:
        return cls(load_policy(), load_register(), {p.person_id: p for p in people})


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _letter_md(case: Case) -> str:
    letter = case.letter
    assert letter is not None
    items = ", ".join(i.value for i in letter.items_covered)
    return f"# {letter.subject_line}\n\n{letter.body}\n\n---\nitems covered: {items}\n"


@dataclass
class OpenDesk:
    """A desk set up for one case: everything an agent run needs, for the runner and for tests."""

    request: RequestCase
    session: CaseSession
    manager: ToolManager
    holder: TokenHolder
    authority: ScopeAuthority
    notifier: NotifierStore
    clock: SimClock
    system: str
    messages: list[Message]
    sink: EventSink

    def requester_replies(self, text: str) -> None:
        """The requester answers a day later. The case resumes with a fresh identity-stage grant (the old
        one is revoked), goes back to IDENTITY_CHECK, and the agent is told."""
        case_id, sender = self.request.case_id, self.request.sender
        self.clock.advance(REQUESTER_REPLY_DELAY)
        self.notifier.receive(case_id, sender, text, self.clock())
        self.authority.revoke_case(case_id)
        self.holder.token = self.authority.open_case(case_id, sender)
        self.session.identity_token = self.holder.token
        self.session.pause = None
        self.session.advance(CaseState.IDENTITY_CHECK, "the requester replied")
        self.messages.append({"role": "user", "content": prompts.REPLY_ARRIVED})


@asynccontextmanager
async def open_desk(
    request: RequestCase, desk: Desk, cfg: RunConfig, run_dir: Path
) -> AsyncIterator[OpenDesk]:
    runtime = run_dir / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    clock = SimClock.for_case(request.received_on)
    scope_secret, approval_secret = secrets.token_bytes(32), secrets.token_bytes(32)
    store = ScopeStore(runtime / "scope.sqlite")
    notifier = NotifierStore(runtime / "notifier.sqlite")
    authority = ScopeAuthority(cfg.data, store, notifier, scope_secret, clock, cfg.grant_ttl)
    ctx = ServerContext(
        paths=cfg.data,
        resolver=ScopeResolver(ScopeStore(store.path, read_only=True), scope_secret, clock),
        tool_timeout_s=cfg.tool_timeout_s,
    )
    servers = {name: build_named(name, ctx, notifier) for name in SERVER_NAMES}
    sink = JsonlSink(run_dir / "events.jsonl", base={"case_id": request.case_id})
    system = prompts.system_prompt(desk.policy, desk.register)
    holder = TokenHolder(authority.open_case(request.case_id, request.sender))
    case = Case.open(request.case_id, request.received_on)
    messages: list[Message] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": prompts.case_message(
                request,
                due=str(case.deadlines.due),
                extended_due=str(case.deadlines.extended_due),
                notice_by=str(case.deadlines.extension_notice_by),
            ),
        },
    ]
    async with connect(servers, holder, cfg.tool_timeout_s + 1) as hub:
        session = CaseSession(
            request=request,
            case=case,
            policy=desk.policy,
            register=desk.register,
            authority=authority,
            notifier=notifier,
            hub=hub,
            clock=clock,
            approval_secret=approval_secret,
            identity_token=holder.token,
            sink=sink,
        )
        tools = [t for t in hub.tools if t.name not in WITHHELD_TOOLS] + build_case_tools(session)
        yield OpenDesk(
            request=request,
            session=session,
            manager=ToolManager(tools, sink=sink),
            holder=holder,
            authority=authority,
            notifier=notifier,
            clock=clock,
            system=system,
            messages=messages,
            sink=sink,
        )


async def run_case(
    request: RequestCase,
    truth: CaseTruth | None,
    desk: Desk,
    cfg: RunConfig,
    provider: Provider,
) -> CaseOutcome:
    started = time.monotonic()
    run_dir = cfg.out_dir / request.case_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    budget = Budget(caps=cfg.caps)
    async with open_desk(request, desk, cfg, run_dir) as d:
        agent = Agent(
            name=AgentName.SINGLE.value,
            provider=provider,
            tools=d.manager,
            model=cfg.model,
            thinking=cfg.thinking,
            max_tokens=cfg.max_output_tokens,
            sink=d.sink,
        )
        session = d.session
        run = await agent.run(d.messages, budget)
        rounds = 0
        while (
            run.stop_reason is StopReason.COMPLETED
            and session.pause is Pause.AWAITING_REQUESTER
            and truth is not None
            and rounds < MAX_REPLY_ROUNDS
        ):
            rounds += 1
            d.requester_replies(requester.reply(truth, desk.people, rounds))
            run = await agent.run(d.messages, budget)
        stuck = run.stop_reason.is_cap or run.stop_reason is StopReason.NO_PROGRESS
        if stuck and session.can_reach(CaseState.NEEDS_HUMAN):
            session.advance(CaseState.NEEDS_HUMAN, f"agent run stopped: {run.stop_reason}")
            session.pause = Pause.WITH_A_PERSON
        final, pack, log, messages = session.case, session.pack, list(d.manager.log), d.messages

    _write_json(run_dir / "case.json", final.model_dump(mode="json"))
    if pack is not None and final.letter is not None:
        (run_dir / "pack.json").write_bytes(pack_bytes(pack))
        (run_dir / "letter.md").write_text(_letter_md(final), encoding="utf-8", newline="\n")
    _write_json(run_dir / "transcript.json", messages)
    with (run_dir / "tool_log.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for entry in log:
            f.write(json.dumps(entry.__dict__, ensure_ascii=False, default=str) + "\n")
    outcome = CaseOutcome(
        case_id=request.case_id,
        final_state=final.state,
        pause=session.pause,
        stop_reason=run.stop_reason,
        error=run.error,
        states=[t.to_state for t in final.history],
        pack_hash=final.pack_hash,
        follow_ups=session.follow_ups_sent,
        tool_calls=[f"{e.tool}:{'ok' if e.ok else e.error_kind}" for e in log],
        totals=budget.totals(),
        policy_version=desk.policy.version,
        prompt_hash=prompts.prompt_hash(d.system),
        model=cfg.model,
        thinking=cfg.thinking,
        max_output_tokens=cfg.max_output_tokens,
        elapsed_s=round(time.monotonic() - started, 2),
    )
    _write_json(
        run_dir / "outcome.json", {**outcome.model_dump(mode="json"), "fingerprint": outcome.fingerprint()}
    )
    return outcome


ProviderFactory = Callable[[str], Provider]
"""case ID -> the provider for that case (a recorder or a replayer has one cassette per case)."""


async def run_many(
    case_ids: list[str],
    cfg: RunConfig,
    provider_for: ProviderFactory,
    jobs: int = 1,
) -> list[CaseOutcome]:
    """Run several cases, `jobs` at a time. Truth is read only to drive the simulated requester."""
    import anyio

    from article15.company.store import load_cases, load_people, load_truths

    requests = {c.case_id: c for c in load_cases(cfg.data)}
    truths = {t.case_id: t for t in load_truths(cfg.data)}
    unknown = [c for c in case_ids if c not in requests]
    if unknown:
        raise KeyError(f"unknown case(s): {', '.join(unknown)}")
    desk = Desk.load(load_people(cfg.data))
    limiter = anyio.CapacityLimiter(max(1, jobs))
    results: dict[str, CaseOutcome] = {}

    async def one(case_id: str) -> None:
        async with limiter:
            results[case_id] = await run_case(
                requests[case_id], truths.get(case_id), desk, cfg, provider_for(case_id)
            )

    async with anyio.create_task_group() as tg:
        for case_id in case_ids:
            tg.start_soon(one, case_id)
    return [results[c] for c in case_ids]
