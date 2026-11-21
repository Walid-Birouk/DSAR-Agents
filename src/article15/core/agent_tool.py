"""Agents as tools: a coordinator calls a specialist like any other tool (PLAN.md 5.2).

Each call starts the specialist fresh (PLAN.md 5.5): its conversation is its system prompt and one message
built from the typed input, so the caller must pass the context explicitly. The specialist ends by calling its
`SubmitTool`; the typed result goes back to the caller as JSON, and as `data` for code. A specialist that
stops for any other reason returns an error result naming the reason, never a half-finished answer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from article15.core.agent import Agent, Budget, Caps, StopReason
from article15.core.tools import (
    DEFAULT_TOOL_TIMEOUT_S,
    Tool,
    ToolResult,
    error_result,
    flatten_schema,
    format_validation_error,
)


class AgentTool[I: BaseModel](Tool):
    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[I],
        make_agent: Callable[[], Agent],
        system_prompt: str,
        render_input: Callable[[I], str],
        parent_budget: Budget,
        caps: Caps | None = None,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S * 60,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self.make_agent = make_agent
        self.system_prompt = system_prompt
        self.render_input = render_input
        self.parent_budget = parent_budget
        self.caps = caps or Caps()
        self.timeout_s = timeout_s
        self._parameters = flatten_schema(input_model.model_json_schema())
        self.runs: list[Any] = []  # every run, for the trajectory checks

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            value = self.input_model.model_validate(args)
        except ValidationError as exc:
            return error_result("invalid", format_validation_error(exc))
        agent = self.make_agent()
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.render_input(value)},
        ]
        budget = Budget(caps=self.caps, parent=self.parent_budget)
        run = await agent.run(messages, budget)
        self.runs.append(run)
        if run.stop_reason is not StopReason.COMPLETED:
            detail = f": {run.error}" if run.error else ""
            return error_result("error", f"{self.name} stopped without a result ({run.stop_reason}){detail}")
        final = run.final
        text = final.model_dump_json() if isinstance(final, BaseModel) else str(final)
        return ToolResult(text=text, data=final)
