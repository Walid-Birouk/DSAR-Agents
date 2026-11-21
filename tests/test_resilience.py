"""M5: failure injection (PLAN.md 8.5, 10). Breakers, retries, fallback, a system that is down, a crash and a
resume, two workers on one case, sends that must not repeat, and secrets that must not leak."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest

from article15.case.models import CaseState
from article15.company.models import World
from article15.company.store import DataPaths
from article15.core.model import ModelRequest, ModelResponse, Usage
from article15.core.provider import ProviderError
from article15.core.resilience import (
    BreakerState,
    BreakerTool,
    CircuitBreaker,
    FallbackProvider,
    ResilientProvider,
    RetryPolicy,
    Switches,
    Tier,
    current_tier,
)
from article15.core.tools import Tool, ToolResult, error_result
from article15.desk.checkpoints import CaseLock, CheckpointStore
from article15.desk.runner import Desk, RunConfig, run_case
from article15.evals.oracle import ScriptedProvider, oracle
from article15.notifier.store import NotifierStore

S = CaseState
NOW = datetime(2026, 11, 21, 9, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def no_sleep(_: float) -> None:
    return None


def response(provider: str = "p") -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model="m",
        content="ok",
        tool_calls=[],
        finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=1),
        cost_usd=0.0,
    )


class Flaky:
    def __init__(self, failures: list[ProviderError | None], name: str = "flaky") -> None:
        self.failures = failures
        self.name = name
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        err = self.failures.pop(0) if self.failures else None
        if err is not None:
            raise err
        return response(self.name)


REQ = ModelRequest(model="deepseek-flash", messages=[{"role": "user", "content": "hi"}], tools=[])


# --- breakers, retries, fallback
# ------------------------------------------------------------------------------


def test_breaker_opens_at_half_failures_over_ten_calls_and_tries_again_after_the_cooldown() -> None:
    clock = Clock()
    b = CircuitBreaker("x", clock=clock)
    for ok in [True] * 5 + [False] * 4:
        b.record(ok)
    assert b.state is BreakerState.CLOSED  # 9 calls: the window is not full yet
    b.record(False)
    assert b.state is BreakerState.OPEN and not b.allow() and b.openings == 1
    clock.t = 59.9
    assert not b.allow()
    clock.t = 60.0
    assert b.state is BreakerState.HALF_OPEN and b.allow() and not b.allow()  # one trial call only
    b.record(False)
    assert b.state is BreakerState.OPEN
    clock.t = 120.0
    assert b.allow()
    b.record(True)
    assert b.state is BreakerState.CLOSED


def test_only_transient_errors_are_retried_with_capped_jittered_backoff() -> None:
    transient = ProviderError("timeout", transient=True)
    flaky = Flaky([transient, transient])
    p = ResilientProvider(flaky, sleep=no_sleep)
    assert anyio.run(p.complete, REQ).provider == "flaky" and flaky.calls == 3 and p.retries == 2
    auth = Flaky([ProviderError("401 invalid key", transient=False)])
    with pytest.raises(ProviderError, match="401"):
        anyio.run(ResilientProvider(auth, sleep=no_sleep).complete, REQ)
    assert auth.calls == 1  # never retried
    always = Flaky([transient] * 10)
    with pytest.raises(ProviderError):
        anyio.run(
            ResilientProvider(always, sleep=no_sleep, breaker=CircuitBreaker("b", window=100)).complete, REQ
        )
    assert always.calls == 5
    policy, rng = RetryPolicy(), random.Random(1)
    assert all(0 <= policy.delay(a, rng) <= 30.0 for a in range(20))
    assert (
        max(policy.delay(10, random.Random(i)) for i in range(200)) > 20
    )  # the cap is reached, not exceeded


def test_fallback_uses_the_next_provider_with_its_own_model_then_pauses() -> None:
    down = Flaky([ProviderError("down", transient=True)] * 3, name="deepseek")
    up = Flaky([], name="mistral")
    seen: list[str] = []

    class Spy(Flaky):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            seen.append(request.model)
            return await super().complete(request)

    spy = Spy([], name="mistral")
    f = FallbackProvider([down, spy], models=[None, "mistral-small-2603"])
    assert anyio.run(f.complete, REQ).provider == "mistral" and f.fallbacks == 1
    assert seen == ["mistral-small-2603"]
    both = FallbackProvider([Flaky([ProviderError("a", True)]), Flaky([ProviderError("b", True)])])
    with pytest.raises(ProviderError, match="paused") as exc:
        anyio.run(both.complete, REQ)
    assert not exc.value.transient
    del up


class Down(Tool):
    name = "weblogs_search_events"
    description = "d"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        return error_result("timeout", "weblogs did not answer")


def test_a_system_that_is_down_trips_its_breaker_and_fails_fast() -> None:
    inner = Down()
    breaker = CircuitBreaker("mcp:weblogs", clock=Clock())
    tool = BreakerTool(inner, breaker)
    results = [anyio.run(tool.run, {}) for _ in range(12)]
    assert inner.calls == 10  # after 10 failures the breaker is open and the server is spared
    assert results[-1].text.startswith("unavailable:") and "systems_failed" in results[-1].text
    assert current_tier(Switches(), {"weblogs": breaker}) is Tier.T3


def test_tiers() -> None:
    assert current_tier(Switches(), {}) is Tier.T1
    assert current_tier(Switches(advisor=False), {}) is Tier.T2
    assert current_tier(Switches(automation=False), {}) is Tier.T4
    assert current_tier(Switches(), {}, spent_today_eur=2.0, daily_cap_eur=2.0) is Tier.T4


# --- the desk under failure
# -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def desk(world: World) -> Desk:
    return Desk.load(world.people)


def _case(world: World, case_id: str) -> Any:
    request = next(c for c in world.cases if c.case_id == case_id)
    truth = next(t for t in world.truths if t.case_id == case_id)
    name = next(p.name for p in world.people if p.person_id == truth.requester.person_id)
    return request, truth, name


class Killed(Exception):
    """The process dies (not a provider error: nothing handles it)."""


class KillAfterDiscovery:
    """Plays the oracle until discovery has been recorded, then the process dies."""

    def __init__(self, inner: ScriptedProvider) -> None:
        self.inner, self.name = inner, "oracle"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if any(str(m.get("content") or "").startswith("Discovery recorded") for m in request.messages):
            raise Killed
        return await self.inner.complete(request)


def test_a_crash_resumes_from_the_checkpoint_without_redoing_finished_stages(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    request, truth, name = _case(world, "A15-046")  # a follow-up question first: a send that must not repeat
    cfg = RunConfig(data=data, out_dir=tmp_path, model="m")
    script = ScriptedProvider(oracle(truth, request, desk.register, name))

    async def until_crash() -> None:
        # stop just after discovery has been submitted
        provider = KillAfterDiscovery(script)
        await run_case(request, truth, desk, cfg, provider)  # type: ignore[arg-type]

    with pytest.raises(BaseException) as exc:
        anyio.run(until_crash)
    leaf: BaseException = exc.value
    while isinstance(leaf, BaseExceptionGroup):
        leaf = leaf.exceptions[0]
    assert isinstance(leaf, Killed)
    store = CheckpointStore(tmp_path / request.case_id / "runtime" / "checkpoints.sqlite")
    snap = store.latest(request.case_id)
    assert snap is not None and snap.case.state is S.DISCOVERY_DONE
    notifier = NotifierStore(tmp_path / request.case_id / "runtime" / "notifier.sqlite")
    sent_before = notifier.sent(request.case_id)

    resumed = ScriptedProvider(oracle(truth, request, desk.register, name, resume_at="DISCOVERY_DONE"))
    outcome = anyio.run(lambda: run_case(request, truth, desk, cfg, resumed, resume=True))
    assert outcome.final_state is S.AWAITING_APPROVAL
    log = [json.loads(x) for x in (tmp_path / request.case_id / "tool_log.jsonl").read_text().splitlines()]
    redone = [
        e["tool"]
        for e in log
        if e["tool"] in ("case_submit_intake", "case_submit_identity", "case_submit_discovery")
    ]
    assert redone == []  # finished stages were not redone
    assert [n.message_id for n in notifier.sent(request.case_id)][: len(sent_before)] == [
        n.message_id for n in sent_before
    ]
    follow_ups = [n for n in notifier.sent(request.case_id) if n.op_id and n.op_id.startswith("follow_up")]
    assert len(follow_ups) == 1  # the follow-up question was sent once
    pack = json.loads((tmp_path / request.case_id / "pack.json").read_text(encoding="utf-8"))
    assert {(r["system"], r["table"], r["record_id"]) for r in pack["records"]} == {
        (k.system.value, k.table, k.record_id) for k in truth.records
    }
    assert store.states(request.case_id)[-1] == "AWAITING_APPROVAL"


def test_a_provider_outage_pauses_the_case_cleanly(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    request, truth, _ = _case(world, "A15-004")
    cfg = RunConfig(data=data, out_dir=tmp_path, model="m")
    dead = FallbackProvider(
        [Flaky([ProviderError("down", True)] * 99), Flaky([ProviderError("down", True)] * 99)]
    )
    outcome = anyio.run(lambda: run_case(request, truth, desk, cfg, dead))
    assert outcome.stop_reason == "provider_error" and "paused" in (outcome.error or "")
    assert (
        outcome.final_state is S.RECEIVED
    )  # nothing half-done; the case can be resumed when a provider is back


def test_one_worker_per_case(tmp_path: Path) -> None:
    lock = CaseLock(tmp_path / "locks.sqlite")
    assert lock.acquire("A15-004", "worker-1", NOW)
    assert not lock.acquire("A15-004", "worker-2", NOW + timedelta(minutes=5))
    assert lock.acquire("A15-004", "worker-1", NOW + timedelta(minutes=5))  # its own lease is renewed
    assert lock.acquire("A15-004", "worker-2", NOW + timedelta(hours=1))  # the crashed worker's lease ran out
    assert lock.holder("A15-004") == "worker-2"
    lock.release("A15-004", "worker-1")  # not the holder: nothing happens
    assert lock.holder("A15-004") == "worker-2"
    lock.release("A15-004", "worker-2")
    assert lock.holder("A15-004") is None


def test_an_operation_is_sent_once(tmp_path: Path) -> None:
    n = NotifierStore(tmp_path / "n.sqlite")
    a = n.send("C1", "x@ferlane.example", "s", "b", NOW, op_id="release:abc")
    b = n.send("C1", "x@ferlane.example", "s", "b", NOW, op_id="release:abc")
    assert a.message_id == b.message_id and len(n.sent("C1")) == 1
    n.send("C1", "x@ferlane.example", "s", "b", NOW)
    n.send("C1", "x@ferlane.example", "s", "b", NOW)
    assert len(n.sent("C1")) == 3  # messages without an operation ID are not deduplicated


def test_secrets_never_reach_logs_or_telemetry(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    request, truth, name = _case(world, "A15-107")
    cfg = RunConfig(data=data, out_dir=tmp_path, model="m")
    anyio.run(
        lambda: run_case(
            request, truth, desk, cfg, ScriptedProvider(oracle(truth, request, desk.register, name))
        )
    )
    for f in ("events.jsonl", "spans.jsonl", "tool_log.jsonl", "transcript.json", "advisor_payloads.jsonl"):
        path = tmp_path / request.case_id / f
        if path.exists():
            text = path.read_text(encoding="utf-8")
            assert "a15g." not in text, f  # case tokens
            assert "Bearer " not in text, f  # the advisor's key
