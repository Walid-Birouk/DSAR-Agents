"""One provider for every OpenAI-compatible chat-completions API: DeepSeek and Mistral (decision 0003).

Provider quirks handled here, from the official docs (read 2026-11-21):
* DeepSeek thinking mode is switched per request with `thinking: {"type": "enabled" | "disabled"}` in the
  body and `reasoning_effort` ("low", "high"); it is on by default, so it is always sent explicitly.
  `tool_choice: "required"` is refused in thinking mode.
  (api-docs.deepseek.com/guides/thinking_mode, /api/create-chat-completion)
* DeepSeek reports cache use as `usage.prompt_cache_hit_tokens` / `usage.prompt_cache_miss_tokens`
  (api-docs.deepseek.com/api/create-chat-completion). Mistral's API spec reports cached input as
  `usage.prompt_tokens_details.cached_tokens` (docs.mistral.ai/api, openapi.yaml); not yet confirmed live.
* The assistant message's `reasoning_content` is read from the SDK's extra fields.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import openai

from article15.core.model import ModelRequest, ModelResponse, Thinking, ToolCall, Usage
from article15.core.pricing import cost_usd, price_for
from article15.core.provider import ProviderError

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MISTRAL_EU_BASE_URL = "https://api.eu.mistral.ai/v1"
MISTRAL_EU_HOST = "api.eu.mistral.ai"

_TRANSIENT = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


@dataclass(frozen=True)
class ProviderConfig:
    name: str  # "deepseek" or "mistral"
    base_url: str
    api_key: str
    timeout_s: float = 120.0
    max_retries: int = 2  # the SDK's own retries on transient errors; the M5 retry policy comes on top

    @property
    def eu_endpoint(self) -> bool:
        return urlparse(self.base_url).hostname == MISTRAL_EU_HOST


def _extra(obj: Any, key: str) -> Any:
    extra = getattr(obj, "model_extra", None) or {}
    return extra.get(key, getattr(obj, key, None))


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    prompt = int(raw.prompt_tokens or 0)
    hit = _extra(raw, "prompt_cache_hit_tokens")
    if hit is None:
        details = getattr(raw, "prompt_tokens_details", None)
        hit = getattr(details, "cached_tokens", None) or 0
    hit = int(hit)
    miss = _extra(raw, "prompt_cache_miss_tokens")
    miss = int(miss) if miss is not None else max(prompt - hit, 0)
    out_details = getattr(raw, "completion_tokens_details", None)
    return Usage(
        input_tokens=prompt,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        output_tokens=int(raw.completion_tokens or 0),
        reasoning_tokens=int(getattr(out_details, "reasoning_tokens", None) or 0),
    )


class OpenAICompatibleProvider:
    def __init__(
        self,
        config: ProviderConfig,
        client: Any | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.name = config.name
        self.now = now
        self.client = client or openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_s,
            max_retries=config.max_retries,
        )

    def _body(self, request: ModelRequest) -> dict[str, Any]:
        body: dict[str, Any] = {"model": request.model, "messages": request.messages}
        if request.tools:
            body["tools"] = request.tools
            if request.tool_choice:
                body["tool_choice"] = request.tool_choice
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if self.name == "deepseek":
            if request.thinking is Thinking.OFF:
                body["extra_body"] = {"thinking": {"type": "disabled"}}
            else:
                body["extra_body"] = {"thinking": {"type": "enabled"}}
                body["reasoning_effort"] = request.thinking.value
        return body

    async def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        at = self.now()
        try:
            raw = await self.client.chat.completions.create(**self._body(request))
        except _TRANSIENT as exc:
            raise ProviderError(f"{self.name}: {type(exc).__name__}: {exc}", transient=True) from exc
        except openai.APIError as exc:
            raise ProviderError(f"{self.name}: {type(exc).__name__}: {exc}", transient=False) from exc
        latency = time.monotonic() - started
        if not raw.choices:
            raise ProviderError(f"{self.name}: response without choices", transient=True)
        choice = raw.choices[0]
        message = choice.message
        usage = _usage(raw.usage)
        served = raw.model or request.model
        price = price_for(self.name, request.model, at, eu_endpoint=self.config.eu_endpoint)
        return ModelResponse(
            provider=self.name,
            model=served,
            content=message.content,
            reasoning_content=_extra(message, "reasoning_content"),
            tool_calls=[
                ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "")
                for c in (message.tool_calls or [])
                if getattr(c, "function", None) is not None
            ],
            finish_reason=str(choice.finish_reason or "unknown"),
            usage=usage,
            cost_usd=cost_usd(usage, price),
            latency_s=round(latency, 3),
        )
