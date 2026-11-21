"""Resilience (PLAN.md 10): retries, circuit breakers, provider fallback and the degradation tiers.

- **Retries** only for transient errors (timeouts, server errors, rate limits), never for invalid input or
  authentication. Exponential backoff with full jitter, capped at 30 s, at most 5 tries.
- **Circuit breakers** per model provider, per MCP server and for the advisor. A breaker opens when at least
  half of the last 10 calls failed, refuses calls while open, and lets one trial call through after 60 s.
- **Provider fallback:** DeepSeek, then Mistral, then pause: when every provider is down or open, the run
stops
  with a provider error and the case goes to a person (the agent loop handles that), never half-done.
- **Tiers:** T1 everything runs; T2 the advisor is down or switched off, so edge cases go straight to a
  person; T3 a company system is down, so the case is marked PARTIAL naming it; T4 the spend cap is reached
  or automation is switched off, so the demo shows recorded runs.

Clocks and sleeps are injected, so the tests run in no time and are deterministic.
"""

from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import anyio

from article15.core.events import EventSink, NullSink
from article15.core.model import ModelRequest, ModelResponse
from article15.core.provider import Provider, ProviderError
from article15.core.tools import Tool, ToolResult, error_result

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    tries: int = 5
    base_s: float = 0.5
    cap_s: float = 30.0

    def delay(self, attempt: int, rng: random.Random) -> float:
        """Full jitter: uniform in [0, min(cap, base * 2^attempt)]."""
        return rng.uniform(0, min(self.cap_s, self.base_s * 2**attempt))


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    window: int = 10
    failure_ratio: float = 0.5
    cooldown_s: float = 60.0
    clock: Clock = time.monotonic
    sink: EventSink = field(default_factory=NullSink)
    _results: deque[bool] = field(default_factory=deque)
    _opened_at: float | None = None
    _trial: bool = False
    openings: int = 0

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self.clock() - self._opened_at >= self.cooldown_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allow(self) -> bool:
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.HALF_OPEN and not self._trial:
            self._trial = True
            return True
        return False

    def record(self, ok: bool) -> None:
        if self._opened_at is not None:  # the trial call decides
            self._trial = False
            if ok:
                self._opened_at = None
                self._results.clear()
            else:
                self._opened_at = self.clock()
            return
        self._results.append(ok)
        while len(self._results) > self.window:
            self._results.popleft()
        failures = self._results.count(False)
        if len(self._results) >= self.window and failures / len(self._results) >= self.failure_ratio:
            self._opened_at = self.clock()
            self.openings += 1
            self.sink.emit("error.occurred", error_type="circuit_open", breaker=self.name)


async def _sleep(seconds: float) -> None:
    await anyio.sleep(seconds)


class ResilientProvider:
    """Retries transient errors on one provider, behind its breaker."""

    def __init__(
        self,
        inner: Provider,
        breaker: CircuitBreaker | None = None,
        policy: RetryPolicy | None = None,
        sleep: Sleep = _sleep,
        rng: random.Random | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.breaker = breaker or CircuitBreaker(f"provider:{inner.name}")
        self.policy = policy or RetryPolicy()
        self.sleep = sleep
        self.rng = rng or random.Random(0)
        self.sink = sink or NullSink()
        self.retries = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        last: ProviderError | None = None
        for attempt in range(self.policy.tries):
            if not self.breaker.allow():
                raise ProviderError(f"{self.name}: circuit open", transient=True)
            try:
                response = await self.inner.complete(request)
            except ProviderError as exc:
                self.breaker.record(False)
                if not exc.transient:
                    raise
                last = exc
                if attempt + 1 < self.policy.tries:
                    self.retries += 1
                    self.sink.emit(
                        "error.occurred", error_type="retry", provider=self.name, attempt=attempt + 1
                    )
                    await self.sleep(self.policy.delay(attempt, self.rng))
                continue
            self.breaker.record(True)
            return response
        assert last is not None
        raise last


class FallbackProvider:
    """The first provider that answers, in order (DeepSeek, then Mistral). When all fail, the error of the
    last
    one goes back to the agent loop, which stops the run cleanly: the "pause" of PLAN.md 10."""

    def __init__(
        self,
        providers: Sequence[Provider],
        sink: EventSink | None = None,
        models: Sequence[str | None] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("a fallback needs at least one provider")
        self.providers = list(providers)
        # The model to ask each provider for (None: the request's own); a fallback provider has its own
        # models.
        self.models = list(models) if models is not None else [None] * len(self.providers)
        self.name = "+".join(p.name for p in providers)
        self.sink = sink or NullSink()
        self.fallbacks = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        errors: list[str] = []
        for i, provider in enumerate(self.providers):
            model = self.models[i]
            try:
                return await provider.complete(
                    request if model is None else request.model_copy(update={"model": model})
                )
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                if i + 1 < len(self.providers):
                    self.fallbacks += 1
                    self.sink.emit("error.occurred", error_type="provider_fallback", provider=provider.name)
                    continue
                raise ProviderError(
                    "every provider failed; paused (" + "; ".join(errors) + ")", False
                ) from exc
        raise AssertionError("unreachable")


class BreakerTool(Tool):
    """A company-system tool behind its server's breaker: while the breaker is open, calls fail fast with an
    `unavailable:` result the agent can act on (record the system as failed: the case goes to PARTIAL)."""

    def __init__(self, inner: Tool, breaker: CircuitBreaker) -> None:
        self.inner = inner
        self.breaker = breaker
        self.name = inner.name
        self.description = inner.description
        self.timeout_s = inner.timeout_s

    @property
    def parameters(self) -> dict[str, Any]:
        return self.inner.parameters

    async def run(self, args: dict[str, Any]) -> ToolResult:
        if not self.breaker.allow():
            return error_result(
                "unavailable",
                f"{self.breaker.name} is not answering (circuit open); retry later or record "
                "it "
                "in systems_failed",
            )
        try:
            result = await self.inner.run(args)
        except Exception:
            self.breaker.record(False)
            raise
        transient = result.is_error and result.error_kind in ("timeout", "unavailable", "error")
        self.breaker.record(not transient)
        return result


def with_breakers(tools: Sequence[Tool], breakers: dict[str, CircuitBreaker]) -> list[Tool]:
    """Wrap each MCP tool in the breaker of its server (the tool name's prefix: crm_, orders_, ...)."""
    out: list[Tool] = []
    for t in tools:
        server = t.name.split("_", 1)[0]
        breaker = breakers.setdefault(server, CircuitBreaker(f"mcp:{server}"))
        out.append(BreakerTool(t, breaker))
    return out


class Tier(StrEnum):
    T1 = "T1"  # everything runs
    T2 = "T2"  # advisor down: edge cases go straight to a person
    T3 = "T3"  # a company system is down: cases are marked PARTIAL
    T4 = "T4"  # spend cap reached or automation off: recorded runs only


@dataclass(frozen=True)
class Switches:
    """Kill switches, from settings (A15_AUTOMATION, A15_ADVISOR)."""

    automation: bool = True
    advisor: bool = True


def current_tier(
    switches: Switches,
    breakers: dict[str, CircuitBreaker],
    spent_today_eur: float = 0.0,
    daily_cap_eur: float | None = None,
) -> Tier:
    if not switches.automation or (daily_cap_eur is not None and spent_today_eur >= daily_cap_eur):
        return Tier.T4
    if any(
        b.state is BreakerState.OPEN
        for n, b in breakers.items()
        if n in ("crm", "orders", "helpdesk", "weblogs")
    ):
        return Tier.T3
    advisor = breakers.get("advisor")
    if not switches.advisor or (advisor is not None and advisor.state is BreakerState.OPEN):
        return Tier.T2
    return Tier.T1
