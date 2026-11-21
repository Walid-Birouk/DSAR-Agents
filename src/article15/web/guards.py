"""The public demo's own protections (PLAN.md 11.3): no real personal data in, abuse limits, bring your own
key.

- **Input detector.** Visitor text must hold no real contact data. An email address must be on a reserved
  `.example` domain, a phone number in ARCEP's fiction ranges and an IP address in the documentation blocks
  (the
  same rules the generator follows, company/fiction.py). IBANs, payment-card numbers (Luhn) and French social
  security numbers are refused outright.
- **Per-IP limits.** A sliding window on live runs per client address (3 an hour, 10 a day by default), on top
  of the per-session limits. Behind the reverse proxy the address comes from X-Forwarded-For, which is trusted
  only when the deployment says so (`A15_TRUST_PROXY=1`).
- **A human check.** A small proof of work, with no third party and no cookie: the server hands out a random
  challenge; the browser finds a nonce whose SHA-256 together with it starts with `bits` zero bits (about a
  second); each challenge is single-use and expires after 5 minutes.
- **Bring your own key.** A visitor's Mistral or DeepSeek key is held in this process's memory for their
session
  only, at most an hour, and used only for their own live runs. It is never written to disk, logs or traces.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from article15.company import fiction
from article15.core.provider import Provider
from article15.settings import ModelSettings, SettingsError

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<![\d])(?:\+33[\s.-]?|0)[1-9](?:[\s.-]?\d{2}){4}(?![\d])")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
NIR = re.compile(r"\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2])\s?(?:\d{2}|2[AB])\s?\d{3}\s?\d{3}(?:\s?\d{2})?\b")


def _luhn(digits: str) -> bool:
    total, odd = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if odd:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        odd = not odd
    return total % 10 == 0


def real_data_problems(text: str) -> list[str]:
    """Why `text` looks like it holds real personal data ([] when it does not)."""
    problems = []
    if any(not fiction.is_fictional_email(m.group(0).lower()) for m in EMAIL.finditer(text)):
        problems.append("an email address outside the fictional .example domains")
    if any(not fiction.is_fictional_phone(m.group(0)) for m in PHONE.finditer(text)):
        problems.append("a phone number outside the fiction ranges (for example 06 39 98 xx xx)")
    if any(not fiction.is_fictional_ip(m.group(0)) for m in IPV4.finditer(text) if _valid_ip(m.group(0))):
        problems.append("an IP address outside the documentation ranges")
    if IBAN.search(text):
        problems.append("what looks like a bank account number (IBAN)")
    if any(_luhn(re.sub(r"\D", "", m.group(0))) for m in CARD.finditer(text)):
        problems.append("what looks like a payment card number")
    if NIR.search(text):
        problems.append("what looks like a French social security number")
    return problems


def _valid_ip(text: str) -> bool:
    return all(0 <= int(p) <= 255 for p in text.split("."))


@dataclass
class IpLimiter:
    per_hour: int = 3
    per_day: int = 10
    clock: Callable[[], float] = time.time
    _hits: dict[str, deque[float]] = field(default_factory=dict)

    def allow(self, ip: str) -> bool:
        now = self.clock()
        hits = self._hits.setdefault(ip, deque())
        while hits and hits[0] <= now - 86400:
            hits.popleft()
        if len(hits) >= self.per_day or sum(1 for t in hits if t > now - 3600) >= self.per_hour:
            return False
        hits.append(now)
        return True


@dataclass
class HumanCheck:
    bits: int = 16
    ttl_s: float = 300.0
    clock: Callable[[], float] = time.time
    _open: dict[str, float] = field(default_factory=dict)

    def challenge(self) -> dict[str, object]:
        now = self.clock()
        self._open = {c: t for c, t in self._open.items() if t > now}
        c = secrets.token_hex(16)
        self._open[c] = now + self.ttl_s
        return {"challenge": c, "bits": self.bits}

    @staticmethod
    def solves(challenge: str, nonce: str, bits: int) -> bool:
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()
        value = int.from_bytes(digest, "big")
        return value >> (256 - bits) == 0

    def verify(self, challenge: str, nonce: str) -> bool:
        expires = self._open.pop(challenge, None)  # single use, solved or not
        return expires is not None and expires > self.clock() and self.solves(challenge, nonce, self.bits)


@dataclass
class VisitorKeys:
    """Visitors' own API keys: in memory, per session, for an hour at most."""

    ttl_s: float = 3600.0
    clock: Callable[[], float] = time.time
    _keys: dict[str, tuple[str, str, float]] = field(default_factory=dict)

    def put(self, session_id: str, provider: str, key: str) -> None:
        if provider not in ("mistral", "deepseek"):
            raise ValueError("bring a Mistral or DeepSeek key")
        if not 16 <= len(key) <= 200 or any(c.isspace() for c in key):
            raise ValueError("that does not look like an API key")
        self._keys[session_id] = (provider, key, self.clock() + self.ttl_s)

    def drop(self, session_id: str) -> None:
        self._keys.pop(session_id, None)

    def provider_of(self, session_id: str) -> str | None:
        entry = self._keys.get(session_id)
        if entry is None or entry[2] <= self.clock():
            self._keys.pop(session_id, None)
            return None
        return entry[0]

    def build(
        self, session_id: str, make: Callable[[ModelSettings], Provider]
    ) -> tuple[Provider, str] | None:
        name = self.provider_of(session_id)
        if name is None:
            return None
        provider, key, _ = self._keys[session_id]
        try:
            base = ModelSettings.from_env(provider=provider)
        except SettingsError:
            from article15.settings import DEFAULT_BASE_URLS, DEFAULT_MODELS

            base = ModelSettings(provider, DEFAULT_MODELS[provider], key, DEFAULT_BASE_URLS[provider])
        settings = replace(base, api_key=key)
        return make(settings), settings.model
