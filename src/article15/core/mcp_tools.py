"""MCP servers as tools for the agents.

* The `case_token` argument is removed from every schema the model sees, and the current token is injected
  on every call from a `TokenHolder` the control plane updates (decision 0008). A model that passes a token
  of its own is refused, and no error text the model sees ever contains a token.
* Required and unknown arguments are checked here, before the call: a server-side validation error would echo
  the arguments, token included.
* Results are compact JSON wrapped in `<untrusted_data>`: record text is data, never instructions.
* `McpHub.call` gives code (not models) direct access, for example to fetch full records into the pack.
"""

from __future__ import annotations

import copy
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import mcp.types as mt
from mcp.client import Client
from mcp.server import MCPServer

from article15.core.model import canonical_json
from article15.core.tools import Tool, ToolResult, error_result, flatten_schema

TOKEN_ARG = "case_token"
# Tool results and the request are wrapped in <untrusted_...> tags, which the prompts say are data, never
# instructions. Only the broken version `injection_defences_off` (evals/broken.py) turns this off.
MARK_UNTRUSTED = True


def escape_untrusted(body: str) -> str:
    """`body` with any closing `</untrusted_...>` tag neutralised, so text inside a wrapper cannot end it
    early and pass itself off as instructions."""
    return body.replace("</untrusted_", "&lt;/untrusted_")


def untrusted(source: str, body: str) -> str:
    if not MARK_UNTRUSTED:
        return body
    return f'<untrusted_data source="{source}">\n{escape_untrusted(body)}\n</untrusted_data>'


_TOKEN_TEXT = re.compile(r"a15g\.[^\s'\",}\]]*")
_SDK_PREFIX = re.compile(r"^Error executing tool [\w.-]+: ")
_PYDANTIC_NOISE = re.compile(r"\s*\[type=[^\]]*\]|\s*For further information visit \S+")
_KINDS = ("refused", "invalid", "timeout", "unavailable")


@dataclass
class TokenHolder:
    """The case token tools act with. Only the control plane changes it (e.g. after identity is verified)."""

    token: str = ""


class McpCallError(Exception):
    pass


def clean_error(text: str) -> str:
    """One readable line, with the SDK's wrapper removed and any case token scrubbed."""
    text = _SDK_PREFIX.sub("", text.strip())
    text = _TOKEN_TEXT.sub("<case token>", text)
    text = _PYDANTIC_NOISE.sub("", text)
    text = " ".join(text.split())
    if not text.startswith(tuple(f"{k}:" for k in _KINDS)):
        text = f"invalid: {text}"
    return text


def _text(result: mt.CallToolResult) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


class McpTool(Tool):
    def __init__(self, client: Client, tool: mt.Tool, holder: TokenHolder, timeout_s: float) -> None:
        self.client = client
        self.holder = holder
        self.name = tool.name
        self.description = tool.description or ""
        self.timeout_s = timeout_s
        schema = copy.deepcopy(dict(tool.input_schema))
        props: dict[str, Any] = schema.setdefault("properties", {})
        self.needs_token = TOKEN_ARG in props
        props.pop(TOKEN_ARG, None)
        schema["required"] = [r for r in schema.get("required", []) if r != TOKEN_ARG]
        if not schema["required"]:
            schema.pop("required")
        schema["additionalProperties"] = False
        self._parameters = flatten_schema(schema)
        self._allowed = frozenset(props)
        self._required = frozenset(schema.get("required", []))

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def run(self, args: dict[str, Any]) -> ToolResult:
        if TOKEN_ARG in args:
            return error_result("refused", "the case token is supplied by the desk; do not pass one")
        unknown = sorted(set(args) - self._allowed)
        if unknown:
            allowed = ", ".join(sorted(self._allowed)) or "none"
            return error_result(
                "invalid", f"unknown argument(s) {', '.join(unknown)} for {self.name}; allowed: {allowed}"
            )
        missing = sorted(self._required - set(args))
        if missing:
            return error_result("invalid", f"missing argument(s) {', '.join(missing)} for {self.name}")
        call_args = {**args, TOKEN_ARG: self.holder.token} if self.needs_token else dict(args)
        result = await self.client.call_tool(self.name, call_args)
        if result.is_error:
            return ToolResult(text=clean_error(_text(result)), is_error=True)
        payload: Any = result.structured_content if result.structured_content is not None else _text(result)
        body = canonical_json(payload) if not isinstance(payload, str) else payload
        return ToolResult(text=untrusted(self.name, body), data=payload)


@dataclass
class McpHub:
    clients: dict[str, Client]
    tools: list[McpTool]
    holder: TokenHolder
    _owner: dict[str, Client] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._owner = {t.name: t.client for t in self.tools}

    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        """Call a tool as code, with the current token. Returns the structured result; raises on error."""
        client = self._owner.get(tool)
        if client is None:
            raise McpCallError(f"unknown tool {tool}")
        result = await client.call_tool(tool, {**(args or {}), TOKEN_ARG: self.holder.token})
        if result.is_error:
            raise McpCallError(clean_error(_text(result)))
        return result.structured_content


@asynccontextmanager
async def connect(
    servers: Mapping[str, MCPServer], holder: TokenHolder, timeout_s: float
) -> AsyncIterator[McpHub]:
    """Open one MCP client per server (in-process transport) for the duration of a case."""
    async with AsyncExitStack() as stack:
        clients: dict[str, Client] = {}
        tools: list[McpTool] = []
        for name, server in servers.items():
            client = await stack.enter_async_context(Client(server, read_timeout_seconds=timeout_s))
            clients[name] = client
            listed = await client.list_tools()
            tools.extend(McpTool(client, t, holder, timeout_s) for t in listed.tools)
        yield McpHub(clients=clients, tools=tools, holder=holder)
