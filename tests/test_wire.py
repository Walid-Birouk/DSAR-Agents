"""The real OpenAI SDK, talking to a mock HTTP transport: what DeepSeek would receive and how its answer is
read. This is the closest check to a live call that needs no key (DeepSeek's fields from
api-docs.deepseek.com, read 2026-11-21)."""

from __future__ import annotations

import json
from typing import Any

import anyio
import httpx2
import openai

from article15.core.model import ModelRequest, Thinking
from article15.core.openai_compat import OpenAICompatibleProvider, ProviderConfig

DEEPSEEK_REPLY = {
    "id": "c-1",
    "object": "chat.completion",
    "created": 1795000000,
    "model": "deepseek-flash",
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Check the sender first.",
                "tool_calls": [
                    {
                        "id": "call_0",
                        "type": "function",
                        "function": {"name": "crm_check_identity", "arguments": "{}"},
                    }
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 7000,
        "completion_tokens": 40,
        "total_tokens": 7040,
        "prompt_tokens_details": {"cached_tokens": 6400},
        "prompt_cache_hit_tokens": 6400,
        "prompt_cache_miss_tokens": 600,
        "completion_tokens_details": {"reasoning_tokens": 12},
    },
}


def _provider(seen: list[dict[str, Any]]) -> OpenAICompatibleProvider:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx2.Response(200, json=DEEPSEEK_REPLY)

    client = openai.AsyncOpenAI(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    return OpenAICompatibleProvider(
        ProviderConfig(name="deepseek", base_url="https://api.deepseek.com", api_key="test-key"),
        client=client,
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "crm_check_identity",
            "description": "d",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }
]


def test_what_deepseek_receives_without_thinking() -> None:
    seen: list[dict[str, Any]] = []
    request = ModelRequest(
        model="deepseek-flash",
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        tools=TOOLS,
        tool_choice="required",
        max_tokens=8192,
    )
    response = anyio.run(lambda: _provider(seen).complete(request))
    body = seen[0]["body"]
    assert seen[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert (
        body["model"] == "deepseek-flash" and body["tool_choice"] == "required" and body["max_tokens"] == 8192
    )
    assert body["thinking"] == {"type": "disabled"} and "reasoning_effort" not in body
    assert body["tools"] == TOOLS
    assert response.reasoning_content == "Check the sender first."
    assert response.usage.cache_hit_tokens == 6400 and response.usage.cache_miss_tokens == 600
    assert response.tool_calls[0].name == "crm_check_identity"


def test_what_deepseek_receives_in_thinking_mode_with_reasoning_sent_back() -> None:
    seen: list[dict[str, Any]] = []
    provider = _provider(seen)
    first = ModelRequest(
        model="deepseek-flash",
        messages=[{"role": "user", "content": "u"}],
        tools=TOOLS,
        tool_choice="auto",
        thinking=Thinking.HIGH,
    )
    response = anyio.run(lambda: provider.complete(first))
    follow = first.model_copy(
        update={
            "messages": [
                *first.messages,
                response.assistant_message(),
                {"role": "tool", "tool_call_id": "call_0", "content": "{}"},
            ]
        }
    )
    anyio.run(lambda: provider.complete(follow))
    body = seen[1]["body"]
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high"
    assert body["messages"][1]["reasoning_content"] == "Check the sender first."
    assert body["messages"][1]["tool_calls"][0]["id"] == "call_0"
