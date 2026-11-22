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

import contextlib
import json
import os
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, ConfigDict

from article15.advisor.service import AdvisorClient, PayloadLog, rules_advice
from article15.advisor.service import build_app as build_advisor_app
from article15.case.models import Case, CaseState
from article15.company.models import CaseTruth, Person, RequestCase
from article15.company.register import Register, load_register
from article15.company.store import DataPaths
from article15.core.agent import Agent, Budget, Caps, StopReason
from article15.core.events import EventSink, JsonlSink
from article15.core.mcp_tools import TokenHolder, connect
from article15.core.model import Message, Thinking, canonical_json, sha256_hex
from article15.core.provider import Provider
from article15.core.resilience import CircuitBreaker, with_breakers
from article15.core.tools import Tool, ToolManager
from article15.desk import prompts, requester
from article15.desk.advice import build_advisor_tools
from article15.desk.case_tools import build_case_tools
from article15.desk.checkpoints import CheckpointStore, Snapshot
from article15.desk.clock import LINK_REPLY_DELAY, REQUESTER_REPLY_DELAY, SimClock
from article15.desk.pack import fetch_records, pack_bytes
from article15.desk.release import ReviewRefused, release
from article15.desk.session import CaseSession, Pause
from article15.desk.team import REPLY_ARRIVED as team_reply_message
from article15.desk.team import build_team, coordinator_message
from article15.desk.team import links_result as team_links_result
from article15.desk.team import prompts_hash as team_prompts_hash
from article15.mcp_servers.common import ServerContext
from article15.mcp_servers.registry import SERVER_NAMES, build_named
from article15.notifier.store import NotifierStore
from article15.policy.models import Policy, load_policy
from article15.schemas import AgentName, IdentityOutcome, System
from article15.scope.authority import ScopeAuthority, ScopeRefused, ScopeResolver, keyed_link_code
from article15.scope.store import ScopeStore
from article15.settings import DEFAULT_GRANT_TTL, DEFAULT_TOOL_TIMEOUT_S
from article15.telemetry.otel import OtelSink, Scrubber, TeeSink, case_tracing, write_spans

M2_CASES = ("A15-004", "A15-017", "A15-122", "A15-045", "A15-107")
MAX_REPLY_ROUNDS = 1  # the generator labels the identity decision after one answered follow-up
MAX_LINK_ROUNDS = 2  # proof-of-control round trips during discovery (decision 0008)
CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
# Only code writes to the requester (the follow-up question comes from the identity decision), so no model can
# send anything to anyone (PLAN.md 5.2).
WITHHELD_TOOLS = frozenset({"notify_post_message"})
# Keyed hashes in traces: from A15_TELEMETRY_KEY when set (stable across a deployment), else per process.
TELEMETRY_KEY = secrets.token_bytes(32)


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
    # Experiment E1: "single" (one agent with every tool) or "team" (seven agents).
    arm: str = "single"
    # Experiment E2: the state machine's guards log what they would block, and let it pass.
    shadow: bool = False
    # Evals: once a pack waits for approval, try to release it without one, as an injected "human review is
    # disabled" or a bug would. The release guard must refuse; if it does not, the eval's release gate fails.
    release_probe: bool = True
    # The DPO advisor, in-process over real A2A: "rules" (deterministic, the default: free and replayable) or
    # "off" (tier T2: edge cases go straight to a person).
    advisor: str = "rules"
    # OpenTelemetry: every case is a trace, written to spans.jsonl (and to OTLP when A15_OTLP_ENDPOINT is
    # set).
    telemetry: bool = True
    # Proof-of-control codes: an HMAC of the case and the address under this key. The fixed default makes
    # recorded runs replay; the public web app passes a fresh secret per run (`keyed_link_code`).
    link_code_key: bytes = b"a15-recorded-runs"


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
    arm: str = "single"
    shadow: bool = False
    # Shadow mode: "<rule ID> <guard>" for every guard that let a violation through.
    guard_would_block: list[str] = []
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
    hub_tools: Sequence[Tool] = ()
    case_tools: list[Tool] = field(default_factory=list)
    reply_message: str = prompts.REPLY_ARRIVED
    links_message: Callable[[int, int], str] = prompts.links_result

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
        self.messages.append({"role": "user", "content": self.reply_message})

    def codes_for(self, mailboxes: list[str]) -> list[str]:
        """The latest code mailed to each pending address the requester can read (the simulation's side)."""
        own = {m.lower() for m in mailboxes}
        codes = []
        for address in self.session.pending_links:
            if address in own:
                mail = [n for n in self.notifier.mailbox(address) if n.kind == "link_code"]
                if mail and (m := CODE.search(mail[-1].body)):
                    codes.append(m.group(1))
        return codes

    def requester_sends_codes(self, text: str) -> None:
        """The requester answers the proof-of-control question. Code (never the agent) tries each code they
        wrote against each pending address; confirmed addresses join the case's scope. The case goes back to
        IDENTITY_VERIFIED with the same subject grant, and the agent is told what was linked."""
        case_id = self.request.case_id
        self.clock.advance(LINK_REPLY_DELAY)
        self.notifier.receive(case_id, self.request.sender, text, self.clock())
        codes = CODE.findall(text)
        linked, not_linked = [], []
        for address in self.session.pending_links:
            for code in codes:
                try:
                    self.authority.complete_link(self.holder.token, address, code)
                except ScopeRefused:
                    continue
                linked.append(address)
                break
            else:
                not_linked.append(address)
        self.session.linked += linked
        self.session.pending_links = []
        self.session.scope = None  # the scope grew: re-index before the next discovery submission
        self.session.pause = None
        self.session.advance(CaseState.IDENTITY_VERIFIED, f"proof of control: {len(linked)} linked")
        self.messages.append({"role": "user", "content": self.links_message(len(linked), len(not_linked))})


async def restore(d: OpenDesk, snap: Snapshot) -> None:
    """Put a fresh desk back where the checkpoint left the case. Tokens are issued anew: the identity grant
    from opening the desk, and (once identity was verified) a subject grant whose evidence code re-checks, as
    at first. Confirmed addresses are in the scope store; fetched records are read again."""
    s = d.session
    d.clock.now = snap.clock
    # A fresh identity-stage grant at the checkpoint's time (the one from opening the desk predates it).
    d.authority.revoke_case(s.case.case_id)
    d.holder.token = s.identity_token = d.authority.open_case(s.case.case_id, s.request.sender)
    s.case = snap.case
    s.follow_ups_sent, s.validation_rounds = snap.follow_ups_sent, snap.validation_rounds
    s.pending_links, s.linked, s.pause, s.pack = (
        list(snap.pending_links),
        list(snap.linked),
        snap.pause,
        snap.pack,
    )
    identity = s.case.identity
    if identity is not None and identity.outcome is IdentityOutcome.VERIFIED and identity.subject_id:
        token = d.authority.grant_subject(
            s.identity_token,
            identity.subject_id,
            s.request_text,
            order_reference=identity.order_reference,
            postcode=identity.postcode,
        )
        d.holder.token = token
    keys = [(System(a), b, c) for a, b, c in snap.record_keys]
    if keys:
        s.records = await fetch_records(s.hub, keys)


@dataclass
class CaseTracing:
    sink: OtelSink
    provider: TracerProvider
    memory: InMemorySpanExporter

    def finish(self, path: Path) -> None:
        self.sink.close()
        self.provider.force_flush()
        write_spans(path, self.memory.get_finished_spans())
        self.provider.shutdown()


def open_tracing(case_id: str, desk: Desk) -> CaseTracing:
    extra = []
    endpoint = os.environ.get("A15_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        extra.append(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
    provider, memory = case_tracing(extra)
    key = os.environ.get("A15_TELEMETRY_KEY", "").encode() or TELEMETRY_KEY
    known = [p.name for p in desk.people.values()] + [m for p in desk.people.values() for m in p.mailboxes]
    sink = OtelSink(provider.get_tracer("article15"), case_id, key, Scrubber.with_values(known))
    return CaseTracing(sink, provider, memory)


def release_probe(session: CaseSession) -> None:
    """Try to approve and send the pack with no approval at all. Both guards must refuse; the state stays."""
    if session.advance(CaseState.APPROVED, "release probe: no approval"):
        return
    with contextlib.suppress(ReviewRefused):
        release(session)


@asynccontextmanager
async def open_desk(
    request: RequestCase, desk: Desk, cfg: RunConfig, run_dir: Path, reopen: bool = False
) -> AsyncIterator[OpenDesk]:
    """Set up a desk for one case. `reopen` keeps the case's earlier events and writes this session's spans
    to a numbered file, for a resumed run or a reviewer's action on a paused case."""
    runtime = run_dir / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    clock = SimClock.for_case(request.received_on)
    scope_secret, approval_secret = secrets.token_bytes(32), secrets.token_bytes(32)
    store = ScopeStore(runtime / "scope.sqlite")
    notifier = NotifierStore(runtime / "notifier.sqlite")
    authority = ScopeAuthority(
        cfg.data,
        store,
        notifier,
        scope_secret,
        clock,
        cfg.grant_ttl,
        link_code=keyed_link_code(cfg.link_code_key),
    )
    ctx = ServerContext(
        paths=cfg.data,
        resolver=ScopeResolver(ScopeStore(store.path, read_only=True), scope_secret, clock),
        tool_timeout_s=cfg.tool_timeout_s,
    )
    servers = {name: build_named(name, ctx, notifier) for name in SERVER_NAMES}
    events = JsonlSink(run_dir / "events.jsonl", base={"case_id": request.case_id}, append=reopen)
    tracing = open_tracing(request.case_id, desk) if cfg.telemetry else None
    sink: EventSink = TeeSink([events, tracing.sink]) if tracing else events
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
            shadow=cfg.shadow,
        )
        # Each company system's tools sit behind that system's circuit breaker (PLAN.md 10).
        hub_tools = with_breakers([t for t in hub.tools if t.name not in WITHHELD_TOOLS], {})
        advisor_key = secrets.token_hex(16)
        advisor = AdvisorClient(
            "http://advisor.internal",
            advisor_key,
            transport=httpx.ASGITransport(app=build_advisor_app(rules_advice, advisor_key)),
            log=PayloadLog(path=run_dir / "advisor_payloads.jsonl"),
        )
        case_tools = build_case_tools(session) + build_advisor_tools(
            session, advisor if cfg.advisor != "off" else None, CircuitBreaker("advisor")
        )
        tools = hub_tools + case_tools
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
            hub_tools=hub_tools,
            case_tools=case_tools,
        )
    if tracing is not None:
        spans = run_dir / "spans.jsonl"
        n = 1
        while reopen and spans.exists():
            n += 1
            spans = run_dir / f"spans-{n}.jsonl"
        tracing.finish(spans)


async def run_case(
    request: RequestCase,
    truth: CaseTruth | None,
    desk: Desk,
    cfg: RunConfig,
    provider: Provider,
    resume: bool = False,
) -> CaseOutcome:
    """Run a case. With `resume`, continue it from its last checkpoint (after a crash) instead of starting
    over: finished stages are not redone."""
    started = time.monotonic()
    run_dir = cfg.out_dir / request.case_id
    if run_dir.exists() and not resume:
        shutil.rmtree(run_dir)
    checkpoints = CheckpointStore(run_dir / "runtime" / "checkpoints.sqlite")
    snapshot = checkpoints.latest(request.case_id) if resume else None
    if resume and snapshot is None:
        raise ValueError(f"no checkpoint to resume {request.case_id} from in {run_dir}")
    budget = Budget(caps=cfg.caps)
    async with open_desk(request, desk, cfg, run_dir, reopen=resume) as d:
        session = d.session
        if snapshot is not None:
            await restore(d, snapshot)
        session.on_change = checkpoints.save
        if cfg.arm == "team":
            team = build_team(
                session=session,
                hub_tools=d.hub_tools,
                case_tools=d.case_tools,
                policy=desk.policy,
                register=desk.register,
                provider=provider,
                model=cfg.model,
                thinking=cfg.thinking,
                max_tokens=cfg.max_output_tokens,
                budget=budget,
                sink=d.sink,
                log=d.manager.log,
            )
            agent = team.coordinator
            d.system = team.system
            prompt_hash = team_prompts_hash(desk.policy, desk.register)
            d.messages[:] = [
                {"role": "system", "content": team.system},
                {"role": "user", "content": coordinator_message(session)},
            ]
            d.reply_message = team_reply_message
            d.links_message = team_links_result
        elif cfg.arm == "single":
            agent = Agent(
                name=AgentName.SINGLE.value,
                provider=provider,
                tools=d.manager,
                model=cfg.model,
                thinking=cfg.thinking,
                max_tokens=cfg.max_output_tokens,
                sink=d.sink,
            )
            prompt_hash = prompts.prompt_hash(d.system)
        else:
            raise ValueError(f"unknown arm {cfg.arm!r}")
        if snapshot is not None:
            d.messages.append({"role": "user", "content": prompts.resumed(session.state.value)})
        run = await agent.run(d.messages, budget)
        rounds = link_rounds = 0
        while run.stop_reason is StopReason.COMPLETED and session.pause is Pause.AWAITING_REQUESTER and truth:
            if session.pending_links:
                if link_rounds >= MAX_LINK_ROUNDS:
                    break
                link_rounds += 1
                codes = d.codes_for(truth.requester.mailboxes)
                d.requester_sends_codes(requester.link_reply(truth, desk.people, codes))
            else:
                if rounds >= MAX_REPLY_ROUNDS:
                    break
                rounds += 1
                d.requester_replies(requester.reply(truth, desk.people, rounds))
            run = await agent.run(d.messages, budget)
        # Not in shadow mode: there the guards only log by design, and E2 counts what the agents attempt.
        if cfg.release_probe and not cfg.shadow and session.state is CaseState.AWAITING_APPROVAL:
            release_probe(session)
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
        prompt_hash=prompt_hash,
        model=cfg.model,
        thinking=cfg.thinking,
        max_output_tokens=cfg.max_output_tokens,
        arm=cfg.arm,
        shadow=cfg.shadow,
        guard_would_block=[f"{f.rule_id} {f.guard}" for f in session.shadow_log],
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


@asynccontextmanager
async def reopen_desk(request: RequestCase, desk: Desk, cfg: RunConfig) -> AsyncIterator[OpenDesk]:
    """A paused case (waiting for approval, say) opened again from its last checkpoint, for a person's action.
    Every change is checkpointed; the case file and pack on disk are rewritten when the block ends."""
    run_dir = cfg.out_dir / request.case_id
    checkpoints = CheckpointStore(run_dir / "runtime" / "checkpoints.sqlite")
    snapshot = checkpoints.latest(request.case_id)
    if snapshot is None:
        raise ValueError(f"no checkpoint for {request.case_id} in {run_dir}")
    async with open_desk(request, desk, cfg, run_dir, reopen=True) as d:
        await restore(d, snapshot)
        d.session.on_change = checkpoints.save
        yield d
        _write_json(run_dir / "case.json", d.session.case.model_dump(mode="json"))
        if d.session.pack is not None:
            (run_dir / "pack.json").write_bytes(pack_bytes(d.session.pack))
