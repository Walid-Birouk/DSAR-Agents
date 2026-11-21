"""What goes to a model and what comes back, in the OpenAI chat-completions format.

Messages are kept as plain JSON dicts: they are sent as they are, hashed for replay, and recorded. A request's
digest is the SHA-256 of its canonical JSON, so the same conversation, tools and settings always give the same
digest, on any machine.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

Message = dict[str, Any]


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no spaces, non-ASCII kept as is."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Thinking(StrEnum):
    """Reasoning mode. OFF by default (decision 0010); LOW and HIGH map to the provider's effort levels."""

    OFF = "off"
    LOW = "low"
    HIGH = "high"


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    messages: list[Message]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str | None = None
    max_tokens: int | None = None
    thinking: Thinking = Thinking.OFF

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    def digest(self) -> str:
        return sha256_hex(self.canonical())


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    arguments: str  # raw JSON text as the model wrote it; parsed (and possibly rejected) by the tool manager


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str  # the model that served the request, as the provider reports it
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_s: float = 0.0

    def assistant_message(self) -> Message:
        """The message to append to the conversation.

        `reasoning_content` is kept when present: DeepSeek requires it to be sent back in every later request
        that carries tools, and answers 400 otherwise (api-docs.deepseek.com/guides/thinking_mode, read
        2026-11-21)."""
        message: Message = {"role": "assistant", "content": self.content}
        if self.reasoning_content is not None:
            message["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in self.tool_calls
            ]
        return message
