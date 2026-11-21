"""The provider interface: one request in, one response out."""

from __future__ import annotations

from typing import Protocol

from article15.core.model import ModelRequest, ModelResponse


class ProviderError(Exception):
    """A model call that failed.

    `transient` errors (timeouts, rate limits, server errors) may succeed if tried again later; the others
    (bad request, authentication) will not."""

    def __init__(self, message: str, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class Provider(Protocol):
    name: str

    async def complete(self, request: ModelRequest) -> ModelResponse: ...
