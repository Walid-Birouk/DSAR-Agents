"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

DEFAULT_TOOL_TIMEOUT_S = 5.0
DEFAULT_GRANT_TTL = timedelta(hours=24)
MIN_SECRET_BYTES = 32


class SettingsError(RuntimeError):
    pass


def secret_from_hex(value: str, name: str) -> bytes:
    try:
        secret = bytes.fromhex(value.strip())
    except ValueError as exc:
        raise SettingsError(f"{name} must be hex") from exc
    if len(secret) < MIN_SECRET_BYTES:
        raise SettingsError(
            f"{name} must be at least {MIN_SECRET_BYTES} random bytes ({2 * MIN_SECRET_BYTES} hex)"
        )
    return secret


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    runtime_dir: Path
    scope_secret: bytes
    tool_timeout_s: float = DEFAULT_TOOL_TIMEOUT_S
    grant_ttl: timedelta = DEFAULT_GRANT_TTL

    @property
    def scope_db(self) -> Path:
        return self.runtime_dir / "scope.sqlite"

    @property
    def notifier_db(self) -> Path:
        return self.runtime_dir / "notifier.sqlite"

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("A15_SCOPE_SECRET", "")
        if not secret:
            raise SettingsError("A15_SCOPE_SECRET is not set (see .env.example)")
        return cls(
            data_dir=Path(os.environ.get("A15_DATA_DIR", "data/generated")),
            runtime_dir=Path(os.environ.get("A15_RUNTIME_DIR", "data/runtime")),
            scope_secret=secret_from_hex(secret, "A15_SCOPE_SECRET"),
            tool_timeout_s=float(os.environ.get("A15_TOOL_TIMEOUT_S", DEFAULT_TOOL_TIMEOUT_S)),
            grant_ttl=timedelta(hours=float(os.environ.get("A15_GRANT_TTL_HOURS", 24))),
        )
