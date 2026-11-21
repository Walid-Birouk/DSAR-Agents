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


# --- model providers and run caps (M2) -----------------------------------------------------------------


def load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from a .env file into the environment, without overriding what is already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


DEFAULT_MODELS = {"deepseek": "deepseek-flash", "mistral": "mistral-small-2603"}
DEFAULT_BASE_URLS = {"deepseek": "https://api.deepseek.com", "mistral": "https://api.eu.mistral.ai/v1"}
PROVIDERS = ("deepseek", "mistral", "replay")


@dataclass(frozen=True)
class ModelSettings:
    provider: str  # deepseek | mistral | replay
    model: str
    api_key: str
    base_url: str
    thinking: str = "off"  # off | low | high
    timeout_s: float = 120.0
    max_output_tokens: int = 8192
    max_calls: int = 60
    max_input_tokens: int = 1_000_000
    max_seconds: float = 900.0
    max_cost_usd: float = 0.30

    @classmethod
    def from_env(cls, provider: str | None = None, model: str | None = None) -> ModelSettings:
        env = os.environ
        name = (provider or env.get("A15_PROVIDER", "deepseek")).strip().lower()
        if name not in PROVIDERS:
            raise SettingsError(f"A15_PROVIDER must be one of {', '.join(PROVIDERS)}, not {name!r}")
        # Replay serves recorded responses; it needs the model the recording was made with, but no key.
        live = "deepseek" if name == "replay" else name
        key = env.get(f"{live.upper()}_API_KEY", "")
        if name != "replay" and not key:
            raise SettingsError(f"{live.upper()}_API_KEY is not set (see .env.example)")
        thinking = env.get("A15_THINKING", "off").strip().lower()
        if thinking not in ("off", "low", "high"):
            raise SettingsError("A15_THINKING must be off, low or high")
        return cls(
            provider=name,
            model=model or env.get(f"A15_{live.upper()}_MODEL", DEFAULT_MODELS[live]),
            api_key=key,
            base_url=env.get(f"{live.upper()}_BASE_URL", DEFAULT_BASE_URLS[live]),
            thinking=thinking,
            timeout_s=float(env.get("A15_MODEL_TIMEOUT_S", 120)),
            max_output_tokens=int(env.get("A15_MAX_OUTPUT_TOKENS", 8192)),
            max_calls=int(env.get("A15_CASE_MAX_CALLS", 60)),
            max_input_tokens=int(env.get("A15_CASE_MAX_INPUT_TOKENS", 1_000_000)),
            max_seconds=float(env.get("A15_CASE_MAX_SECONDS", 900)),
            max_cost_usd=float(env.get("A15_CASE_MAX_COST_USD", 0.30)),
        )
