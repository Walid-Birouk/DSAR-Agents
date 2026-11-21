"""The tracing hook: structured events with attributes, never contents (PLAN.md 9).

Event names follow the plan's versioned schema: agent.start, llm.request, llm.response, tool.invoke,
tool.complete, error.occurred, agent.complete. Attribute values are scalars: names, counts, tokens, latencies,
statuses, digests. Prompts, tool arguments and results never go here; they belong to the run's transcript,
which is kept apart. M5 maps these events onto OpenTelemetry spans and adds the scrubber.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

EVENT_SCHEMA_VERSION = "1"
EVENT_NAMES = frozenset(
    {
        "agent.start",
        "llm.request",
        "llm.response",
        "tool.invoke",
        "tool.complete",
        "error.occurred",
        "agent.complete",
        "case.transition",
    }
)
Scalar = str | int | float | bool | None


@dataclass(frozen=True)
class Event:
    name: str
    at: datetime
    attrs: dict[str, Scalar]


class EventSink(Protocol):
    def emit(self, name: str, **attrs: Scalar) -> None: ...


def _check(name: str, attrs: dict[str, Scalar]) -> None:
    if name not in EVENT_NAMES:
        raise ValueError(f"unknown event name {name}")
    for key, value in attrs.items():
        if not isinstance(value, str | int | float | bool | type(None)):
            raise TypeError(f"event attribute {key} must be a scalar, not {type(value).__name__}")


@dataclass
class ListSink:
    events: list[Event] = field(default_factory=list)

    def emit(self, name: str, **attrs: Scalar) -> None:
        _check(name, attrs)
        self.events.append(Event(name, datetime.now(UTC), dict(attrs)))

    def named(self, name: str) -> list[Event]:
        return [e for e in self.events if e.name == name]


@dataclass
class JsonlSink:
    path: Path
    base: dict[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def emit(self, name: str, **attrs: Scalar) -> None:
        _check(name, attrs)
        row = {
            "schema": EVENT_SCHEMA_VERSION,
            "name": name,
            "at": datetime.now(UTC).isoformat(),
            **self.base,
            **attrs,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class NullSink:
    def emit(self, name: str, **attrs: Scalar) -> None:
        _check(name, attrs)
