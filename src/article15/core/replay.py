"""Record every model call of a live run, and replay it later at no cost (PLAN.md 8.7).

A cassette is a JSONL file:
  {"kind": "header", ...}
  {"kind": "tools", "digest": ..., "tools": [...]}            once per distinct tool list
  {"kind": "call", "digest": ..., "prefix": k, "messages": [...], "response": {...}, ...}

A call stores only the messages added since the previous call (`prefix` messages are shared with it), so a
cassette grows with the conversation instead of with its square. Replay rebuilds every recorded request,
matches requests by digest, and returns the recorded response. A request that was never recorded (because a
prompt, a tool or the code changed what is sent) raises `ReplayMiss`, which names the first message that
differs from the recorded request at the same position.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from article15.core.model import Message, ModelRequest, ModelResponse, Thinking, canonical_json, sha256_hex
from article15.core.provider import Provider, ProviderError

CASSETTE_VERSION = 1


class ReplayMiss(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, transient=False)


class _Header(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["header"] = "header"
    version: int = CASSETTE_VERSION
    label: str


class _Tools(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tools"] = "tools"
    digest: str
    tools: list[dict[str, Any]]


class _Call(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["call"] = "call"
    index: int
    digest: str
    model: str
    tools_digest: str
    tool_choice: str | None
    max_tokens: int | None
    thinking: Thinking
    prefix: int
    messages: list[Message]
    response: ModelResponse


def _tools_digest(tools: list[dict[str, Any]]) -> str:
    return sha256_hex(canonical_json(tools))


def _common_prefix(a: list[Message], b: list[Message]) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if canonical_json(x) != canonical_json(y):
            break
        n += 1
    return n


class RecordingProvider:
    """Wraps a live provider and appends every call to a cassette file."""

    def __init__(self, inner: Provider, path: Path, label: str) -> None:
        self.inner = inner
        self.name = inner.name
        self.path = path
        self._index = 0
        self._previous: list[Message] = []
        self._tools_seen: set[str] = set()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write(_Header(label=label))

    def _write(self, entry: BaseModel) -> None:
        mode = "a" if self.path.exists() and entry.__class__ is not _Header else "w"
        with self.path.open(mode, encoding="utf-8", newline="\n") as f:
            f.write(canonical_json(entry.model_dump(mode="json")) + "\n")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response = await self.inner.complete(request)
        tools_digest = _tools_digest(request.tools)
        if tools_digest not in self._tools_seen:
            self._tools_seen.add(tools_digest)
            self._write(_Tools(digest=tools_digest, tools=request.tools))
        prefix = _common_prefix(self._previous, request.messages)
        self._write(
            _Call(
                index=self._index,
                digest=request.digest(),
                model=request.model,
                tools_digest=tools_digest,
                tool_choice=request.tool_choice,
                max_tokens=request.max_tokens,
                thinking=request.thinking,
                prefix=prefix,
                messages=request.messages[prefix:],
                response=response,
            )
        )
        self._index += 1
        self._previous = list(request.messages)
        return response


def load_requests(path: Path) -> list[tuple[ModelRequest, ModelResponse]]:
    """Every recorded request, rebuilt in full, with its response."""
    tools: dict[str, list[dict[str, Any]]] = {}
    previous: list[Message] = []
    calls: list[tuple[ModelRequest, ModelResponse]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw["kind"] == "header":
                header = _Header.model_validate(raw)
                if header.version != CASSETTE_VERSION:
                    raise ValueError(f"{path}: cassette version {header.version} is not supported")
            elif raw["kind"] == "tools":
                t = _Tools.model_validate(raw)
                tools[t.digest] = t.tools
            else:
                call = _Call.model_validate(raw)
                messages = previous[: call.prefix] + call.messages
                request = ModelRequest(
                    model=call.model,
                    messages=messages,
                    tools=tools[call.tools_digest],
                    tool_choice=call.tool_choice,
                    max_tokens=call.max_tokens,
                    thinking=call.thinking,
                )
                if request.digest() != call.digest:
                    raise ValueError(f"{path}: call {call.index} does not rebuild to its recorded digest")
                calls.append((request, call.response))
                previous = messages
    return calls


def _first_difference(recorded: ModelRequest, actual: ModelRequest) -> str:
    for field in ("model", "tool_choice", "max_tokens", "thinking"):
        if getattr(recorded, field) != getattr(actual, field):
            return f"{field} differs: recorded {getattr(recorded, field)!r}, now {getattr(actual, field)!r}"
    if canonical_json(recorded.tools) != canonical_json(actual.tools):
        return "the tool definitions differ"
    n = _common_prefix(recorded.messages, actual.messages)
    if n == len(recorded.messages) == len(actual.messages):
        return "requests are equal"
    rec = canonical_json(recorded.messages[n])[:300] if n < len(recorded.messages) else "<none>"
    now = canonical_json(actual.messages[n])[:300] if n < len(actual.messages) else "<none>"
    return f"message {n} differs:\n  recorded: {rec}\n  now:      {now}"


class ReplayProvider:
    """Returns recorded responses for recorded requests; anything else is a `ReplayMiss`."""

    def __init__(self, path: Path, name: str = "replay") -> None:
        self.name = name
        self.path = path
        self.calls = load_requests(path)
        self._by_digest: dict[str, deque[int]] = {}
        for i, (request, _) in enumerate(self.calls):
            self._by_digest.setdefault(request.digest(), deque()).append(i)
        self._served = 0
        self._used: set[int] = set()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        queue = self._by_digest.get(request.digest())
        if not queue:
            position = min(self._served, len(self.calls) - 1) if self.calls else -1
            detail = (
                _first_difference(self.calls[position][0], request)
                if position >= 0
                else "the cassette is empty"
            )
            raise ReplayMiss(f"replay miss at call {self._served} ({self.path.name}): {detail}")
        i = queue.popleft()
        self._used.add(i)
        self._served += 1
        return self.calls[i][1]

    def unused(self) -> int:
        """Recorded calls that were never asked for (a replay that stopped early)."""
        return len(self.calls) - len(self._used)
