"""What every Ferlane MCP server shares.

* Tool names carry the server name (`crm_get_customer`), so tools from different servers never collide.
* Schemas are strict: `additionalProperties: false`, and a middleware rejects unknown arguments with a
  readable error instead of silently dropping them (the SDK's default).
* Every tool call runs with a timeout. Errors come back as one short line the model can act on:
  `refused: ...` (outside the case's scope), `timeout: ...`, `unavailable: ...`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

import anyio
import mcp.types as mt
from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools.base import Tool
from pydantic import Field

from article15.company.store import DataPaths
from article15.schemas import Strict, System
from article15.scope.authority import ScopeRefused, ScopeResolver
from article15.scope.store import ScopeStore
from article15.settings import DEFAULT_TOOL_TIMEOUT_S, Settings

CASE_TOKEN_HELP = "The case token issued for this request (injected by the desk; never invent one)."
CaseToken = Annotated[str, Field(description=CASE_TOKEN_HELP)]


class RecordSummary(Strict):
    """A reference to one record and a summary without free text or contact details."""

    system: System
    table: str
    record_id: str
    summary: str


class SearchResult(Strict):
    records: list[RecordSummary]
    total: int
    truncated: bool = False


class RecordDetail(Strict):
    system: System
    table: str
    record_id: str
    fields: dict[str, str | int | bool | None]


@dataclass(frozen=True)
class ServerContext:
    paths: DataPaths
    resolver: ScopeResolver
    tool_timeout_s: float = DEFAULT_TOOL_TIMEOUT_S

    @classmethod
    def from_settings(cls, settings: Settings) -> ServerContext:
        return cls(
            paths=DataPaths(settings.data_dir),
            resolver=ScopeResolver(ScopeStore(settings.scope_db, read_only=True), settings.scope_secret),
            tool_timeout_s=settings.tool_timeout_s,
        )


async def run_tool[T](ctx: ServerContext, system: str, fn: Callable[[], T]) -> T:
    """Run a blocking service call in a worker thread, with the timeout and readable errors.

    `system` names the server in error messages (a `System`, or "notifier")."""
    try:
        with anyio.fail_after(ctx.tool_timeout_s):
            return await anyio.to_thread.run_sync(fn, abandon_on_cancel=True)
    except ScopeRefused as exc:
        raise ToolError(f"refused: {exc}") from None
    except ValueError as exc:
        raise ToolError(f"invalid: {exc}") from None
    except TimeoutError:
        raise ToolError(
            f"timeout: {system} did not answer within {ctx.tool_timeout_s:g} s; try once more, then report "
            f"{system} as failed"
        ) from None
    except (sqlite3.Error, FileNotFoundError):
        raise ToolError(f"unavailable: {system} cannot be reached; report {system} as failed") from None


class RejectUnknownArguments:
    """Middleware: a tool call with an argument the schema does not list is rejected, not silently trimmed."""

    def __init__(self, allowed: Mapping[str, frozenset[str]]) -> None:
        self.allowed = allowed

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        params = ctx.params
        if ctx.method == "tools/call" and isinstance(params, dict):
            name, args = params.get("name"), params.get("arguments") or {}
            if isinstance(name, str) and name in self.allowed and isinstance(args, dict):
                unknown = sorted(set(args) - self.allowed[name])
                if unknown:
                    allowed = ", ".join(sorted(self.allowed[name]))
                    text = f"invalid: unknown argument(s) {', '.join(unknown)} for {name}; allowed: {allowed}"
                    return mt.CallToolResult(content=[mt.TextContent(type="text", text=text)], is_error=True)
        return await call_next(ctx)


def build_server(
    name: str, instructions: str, functions: Sequence[Callable[..., Any]], read_only: bool
) -> MCPServer:
    tools: list[Tool] = []
    allowed: dict[str, frozenset[str]] = {}
    for fn in functions:
        tool = Tool.from_function(
            fn,
            annotations=mt.ToolAnnotations(
                read_only_hint=read_only,
                destructive_hint=False,
                idempotent_hint=read_only,
                open_world_hint=False,
            ),
        )
        tool.parameters["additionalProperties"] = False
        allowed[tool.name] = frozenset(tool.parameters.get("properties", {}))
        tools.append(tool)
    return MCPServer(
        name, instructions=instructions, tools=tools, middleware=[RejectUnknownArguments(allowed)]
    )


def detail(system: System, table: str, record_id: str, row: sqlite3.Row) -> RecordDetail:
    return RecordDetail(system=system, table=table, record_id=record_id, fields=dict(row))


def euros(cents: int) -> str:
    return f"{cents / 100:.2f} EUR"
