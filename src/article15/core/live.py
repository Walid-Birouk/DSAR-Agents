"""Live providers with the resilience of PLAN.md 10: each provider retries transient errors behind its own
circuit breaker, and DeepSeek falls back to Mistral (when a Mistral key is set), then pauses."""

from __future__ import annotations

from article15.core.openai_compat import OpenAICompatibleProvider, ProviderConfig
from article15.core.provider import Provider
from article15.core.resilience import FallbackProvider, ResilientProvider
from article15.settings import ModelSettings, SettingsError


def _one(settings: ModelSettings) -> Provider:
    raw = OpenAICompatibleProvider(
        ProviderConfig(
            name=settings.provider,
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout_s=settings.timeout_s,
        )
    )
    return ResilientProvider(raw)


def live_provider(settings: ModelSettings, fallback: bool = True) -> Provider:
    """The provider for live runs. The fallback provider is used only with the model of its own settings, so
    a run that falls back records which provider answered each call (ModelResponse.provider)."""
    primary = _one(settings)
    if not fallback or settings.provider != "deepseek":
        return primary
    try:
        backup = ModelSettings.from_env(provider="mistral")
    except SettingsError:
        return primary
    return FallbackProvider([primary, _one(backup)], models=[None, backup.model])
