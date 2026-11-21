"""Tools and the tool manager.

Every tool call goes through `ToolManager.call`, which:
* rejects unknown tools and arguments that are not a JSON object;
* runs the guard hooks first (a refusal is returned, the tool never runs);
* runs the tool with a timeout;
* turns every failure into a one-line result the model can act on (`invalid:`, `refused:`, `timeout:`,
  `error:`), so no tool error ever crashes a run (decision 0002);
* logs the call, for the claims cross-check and the trajectory checks.

Tool schemas are flattened (no `$ref`, no titles) so every OpenAI-compatible provider reads them the same way.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
from pydantic import BaseModel, ValidationError

from article15.core.events import EventSink, NullSink
from article15.core.model import ToolCall, ToolSpec

DEFAULT_TOOL_TIMEOUT_S = 10.0
MAX_ERROR_CHARS = 600

GuardHook = Callable[[str, dict[str, Any]], str | None]
"""(tool name, arguments) -> None to allow the call, or the reason it is refused."""


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False
    stop: bool = False  # ends the agent run after this batch of tool calls
    data: Any = None  # a structured value for the caller (never shown to the model)

    @property
    def error_kind(self) -> str | None:
        if not self.is_error:
            return None
        head = self.text.split(":", 1)[0]
        return head if head in {"invalid", "refused", "timeout", "unavailable", "error"} else "error"


def error_result(kind: str, message: str) -> ToolResult:
    text = f"{kind}: {message}"
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 3] + "..."
    return ToolResult(text=text, is_error=True)


def flatten_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline `$ref`s into their definitions and drop schema titles (but not properties named "title")."""
    defs: dict[str, Any] = schema.get("$defs", {})

    def walk(node: Any, properties: bool = False) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if properties:
            return {name: walk(sub) for name, sub in node.items()}
        if "$ref" in node:
            target = walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            rest = {k: walk(v) for k, v in node.items() if k not in ("$ref", "title")}
            return {**target, **rest}
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("$defs", "title"):
                continue
            out[key] = walk(value, properties=key == "properties")
        return out

    flat: dict[str, Any] = walk(schema)
    return flat


def format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "arguments"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)


class Tool(ABC):
    name: str
    description: str
    timeout_s: float = DEFAULT_TOOL_TIMEOUT_S

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]: ...

    @abstractmethod
    async def run(self, args: dict[str, Any]) -> ToolResult: ...

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)


class FunctionTool[M: BaseModel](Tool):
    """A tool whose arguments are a pydantic model (extra fields forbidden) and whose body is a coroutine."""

    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[M],
        fn: Callable[[M], Awaitable[ToolResult]],
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self.fn = fn
        self.timeout_s = timeout_s
        self._parameters = flatten_schema(input_model.model_json_schema())

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            value = self.input_model.model_validate(args)
        except ValidationError as exc:
            return error_result("invalid", format_validation_error(exc))
        return await self.fn(value)


class SubmitTool[M: BaseModel](FunctionTool[M]):
    """The final-output tool: a valid call ends the run and hands the typed value to the caller."""

    def __init__(self, output_model: type[M], name: str = "submit_result", description: str = "") -> None:
        async def accept(value: M) -> ToolResult:
            return ToolResult(text="accepted", stop=True, data=value)

        super().__init__(
            name,
            description or f"Finish your task by returning your {output_model.__name__}.",
            output_model,
            accept,
        )


@dataclass(frozen=True)
class ToolLogEntry:
    index: int
    agent: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    error_kind: str | None
    text: str
    duration_s: float


@dataclass
class ToolManager:
    tools: Sequence[Tool]
    guards: Sequence[GuardHook] = ()
    sink: EventSink = field(default_factory=NullSink)
    log: list[ToolLogEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        names = [t.name for t in self.tools]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate tool names: {', '.join(sorted(duplicates))}")
        self._by_name = {t.name: t for t in self.tools}

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools]

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    async def call(self, call: ToolCall, agent: str = "") -> ToolResult:
        started = time.monotonic()
        self.sink.emit("tool.invoke", agent=agent, tool=call.name)
        args: dict[str, Any] = {}
        result = await self._call(call, args)
        duration = time.monotonic() - started
        self.log.append(
            ToolLogEntry(
                index=len(self.log),
                agent=agent,
                tool=call.name,
                arguments=args,
                ok=not result.is_error,
                error_kind=result.error_kind,
                text=result.text,
                duration_s=round(duration, 4),
            )
        )
        self.sink.emit(
            "tool.complete",
            agent=agent,
            tool=call.name,
            status="ok" if not result.is_error else result.error_kind,
            duration_s=round(duration, 4),
        )
        return result

    async def _call(self, call: ToolCall, args: dict[str, Any]) -> ToolResult:
        tool = self._by_name.get(call.name)
        if tool is None:
            available = ", ".join(sorted(self._by_name))
            return error_result("invalid", f"unknown tool {call.name}; available tools: {available}")
        try:
            parsed = json.loads(call.arguments) if call.arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return error_result("invalid", f"arguments for {call.name} are not valid JSON ({exc.msg})")
        if not isinstance(parsed, dict):
            return error_result("invalid", f"arguments for {call.name} must be a JSON object")
        args.update(parsed)
        for guard in self.guards:
            reason = guard(call.name, parsed)
            if reason is not None:
                return error_result("refused", reason)
        try:
            with anyio.fail_after(tool.timeout_s):
                return await tool.run(parsed)
        except TimeoutError:
            return error_result("timeout", f"{call.name} did not finish within {tool.timeout_s:g} s")
        except Exception as exc:  # a tool bug must not crash the run; it is logged and shown as an error
            self.sink.emit("error.occurred", tool=call.name, error_type=type(exc).__name__)
            return error_result("error", f"{call.name} failed ({type(exc).__name__}: {exc})")
