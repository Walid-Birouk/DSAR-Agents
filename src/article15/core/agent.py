"""The bounded agent loop.

An agent calls the model, runs the tools it asks for, and repeats until a tool ends the run (`stop=True`),
a cap is reached, or the provider fails. Caps cover model calls, input tokens, wall time and cost (PLAN.md 7),
checked before every model call against a `Budget` that can be shared: a case's agents charge the case's
budget, and a sub-agent run as a tool charges both its own budget and its caller's.

The loop is resumable: `run` works on a conversation the caller owns, so the caller can append a message (for
example "the requester replied") and run again.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from article15.core.events import EventSink, NullSink
from article15.core.model import Message, ModelRequest, ModelResponse, Thinking, Usage, sha256_hex
from article15.core.provider import Provider, ProviderError
from article15.core.tools import ToolManager, ToolResult, error_result

DEFAULT_NUDGE = (
    "Continue by calling one of your tools. The task ends only when you call the tool that completes it."
)
MAX_CONSECUTIVE_NUDGES = 2


TRACE_CONTENTS = False  # process-wide switch for Agent.trace_contents (debugging only; see below)


class StopReason(StrEnum):
    COMPLETED = "completed"
    CAP_CALLS = "cap_calls"
    CAP_INPUT_TOKENS = "cap_input_tokens"
    CAP_TIME = "cap_time"
    CAP_COST = "cap_cost"
    NO_PROGRESS = "no_progress"
    PROVIDER_ERROR = "provider_error"

    @property
    def is_cap(self) -> bool:
        return self.value.startswith("cap_")


@dataclass(frozen=True)
class Caps:
    """Per-case defaults (decision 0010): 60 model calls, 1M input tokens, 15 minutes, USD 0.30."""

    max_calls: int = 60
    max_input_tokens: int = 1_000_000
    max_seconds: float = 900.0
    max_cost_usd: float = 0.30


@dataclass
class Budget:
    caps: Caps = field(default_factory=Caps)
    parent: Budget | None = None
    clock: Callable[[], float] = time.monotonic
    calls: int = 0
    input_tokens: int = 0
    cache_hit_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    started: float | None = None

    def start(self) -> None:
        if self.started is None:
            self.started = self.clock()
        if self.parent is not None:
            self.parent.start()

    @property
    def elapsed_s(self) -> float:
        return 0.0 if self.started is None else self.clock() - self.started

    def exceeded(self) -> StopReason | None:
        if self.calls >= self.caps.max_calls:
            return StopReason.CAP_CALLS
        if self.input_tokens >= self.caps.max_input_tokens:
            return StopReason.CAP_INPUT_TOKENS
        if self.cost_usd >= self.caps.max_cost_usd:
            return StopReason.CAP_COST
        if self.elapsed_s >= self.caps.max_seconds:
            return StopReason.CAP_TIME
        return self.parent.exceeded() if self.parent is not None else None

    def charge(self, usage: Usage, cost: float) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.cache_hit_tokens += usage.cache_hit_tokens
        self.output_tokens += usage.output_tokens
        self.reasoning_tokens += usage.reasoning_tokens
        self.cost_usd += cost
        if self.parent is not None:
            self.parent.charge(usage, cost)

    def totals(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class AgentRun:
    agent: str
    stop_reason: StopReason
    final: Any = None  # the `data` of the tool result that ended the run
    calls: int = 0
    error: str | None = None


@dataclass
class Agent:
    name: str
    provider: Provider
    tools: ToolManager
    model: str
    thinking: Thinking = Thinking.OFF
    max_tokens: int | None = 8192
    sink: EventSink = field(default_factory=NullSink)
    nudge: str = DEFAULT_NUDGE
    # Debugging only, never in production: put the last message's text on llm.request. Telemetry must not
    # hold contents (PLAN.md 9); the broken version `raw_prompts_to_telemetry` turns this on.
    trace_contents: bool = False

    def _tool_choice(self) -> str:
        # DeepSeek refuses "required" in thinking mode; without thinking it forces a tool call every turn.
        return "required" if self.thinking is Thinking.OFF else "auto"

    def _request(self, messages: list[Message]) -> ModelRequest:
        return ModelRequest(
            model=self.model,
            messages=list(messages),
            tools=[s.to_openai() for s in self.tools.specs()],
            tool_choice=self._tool_choice(),
            max_tokens=self.max_tokens,
            thinking=self.thinking,
        )

    async def run(self, messages: list[Message], budget: Budget) -> AgentRun:
        """Run on `messages` (system prompt first), appending to it in place."""
        budget.start()
        system = messages[0].get("content", "") if messages else ""
        self.sink.emit(
            "agent.start", agent=self.name, prompt_hash=sha256_hex(str(system))[:16], model=self.model
        )
        calls = 0
        nudges = 0
        while True:
            if (cap := budget.exceeded()) is not None:
                return self._finish(AgentRun(self.name, cap, calls=calls), budget)
            request = self._request(messages)
            self.sink.emit(
                "llm.request",
                agent=self.name,
                provider=self.provider.name,
                model=self.model,
                messages=len(messages),
                digest=request.digest()[:16],
                **(
                    {"prompt": str(messages[-1].get("content", ""))[:4000]}
                    if self.trace_contents or TRACE_CONTENTS
                    else {}
                ),
            )
            try:
                response = await self.provider.complete(request)
            except ProviderError as exc:
                self.sink.emit(
                    "error.occurred", agent=self.name, error_type=type(exc).__name__, transient=exc.transient
                )
                run = AgentRun(self.name, StopReason.PROVIDER_ERROR, calls=calls, error=str(exc))
                return self._finish(run, budget)
            calls += 1
            budget.charge(response.usage, response.cost_usd)
            self._emit_response(response)
            messages.append(response.assistant_message())
            if not response.tool_calls:
                nudges += 1
                if nudges > MAX_CONSECUTIVE_NUDGES:
                    return self._finish(AgentRun(self.name, StopReason.NO_PROGRESS, calls=calls), budget)
                messages.append({"role": "user", "content": self.nudge})
                continue
            nudges = 0
            stopping: ToolResult | None = None
            for call in response.tool_calls:
                if stopping is not None:
                    result = error_result("refused", "the task has already been completed by an earlier call")
                else:
                    result = await self.tools.call(call, agent=self.name)
                    if result.stop:
                        stopping = result
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result.text})
            if stopping is not None:
                run = AgentRun(self.name, StopReason.COMPLETED, final=stopping.data, calls=calls)
                return self._finish(run, budget)

    def _emit_response(self, r: ModelResponse) -> None:
        self.sink.emit(
            "llm.response",
            agent=self.name,
            provider=r.provider,
            model=r.model,
            finish_reason=r.finish_reason,
            tool_calls=len(r.tool_calls),
            input_tokens=r.usage.input_tokens,
            cache_hit_tokens=r.usage.cache_hit_tokens,
            output_tokens=r.usage.output_tokens,
            reasoning_tokens=r.usage.reasoning_tokens,
            cost_usd=r.cost_usd,
            latency_s=r.latency_s,
        )

    def _finish(self, run: AgentRun, budget: Budget) -> AgentRun:
        self.sink.emit(
            "agent.complete",
            agent=self.name,
            stop_reason=run.stop_reason.value,
            calls=run.calls,
            case_cost_usd=round(budget.cost_usd, 6),
        )
        return run
