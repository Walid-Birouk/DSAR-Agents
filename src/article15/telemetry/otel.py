"""OpenTelemetry tracing and metrics, without personal data (PLAN.md 9).

`OtelSink` turns the core's events into spans, one trace per case:

    case (keyed hash of the case ID)
      agent.run            one per agent run (a specialist's run nests inside the coordinator's tool call)
        llm.call           tokens, cache hits, cost, latency, stop reason
        tool.call          tool name, status, duration
    stage changes are events on the case span, and every span carries the stage it ran in

Attributes are names, counts, statuses and digests; never contents. Two lines of defence keep personal data
out anyway:
1. Identifiers are keyed hashes (`hid`), so a trace can be correlated without naming anyone.
2. The scrubber checks every attribute value before it is set: anything that looks like an email address, a
   phone number, an IP address or a record ID, or that is one of the values the caller says are personal
   (the seeded people in evals), is replaced by "[scrubbed]" and counted.

Spans go to the exporters of the tracer provider: an in-memory exporter per case (written to spans.jsonl, so
the eval's leak test can scan it), and OTLP to the private collector when A15_OTLP_ENDPOINT is set.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.metrics import Meter
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, Status, StatusCode

from article15.core.events import EVENT_SCHEMA_VERSION, Scalar, _check

SCRUBBED = "[scrubbed]"
PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"),  # email
    re.compile(r"(\+\d{2}\s?|\b0)\d([\s.-]?\d{2}){4}\b"),  # phone
    re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"),  # IPv4
    re.compile(r"\b(CS\d{4}|C\d{4}|DR\d{3}|FL-\d{6}|L\d{6}|T\d{5}|M\d{6}|E\d{7})\b"),  # record IDs
)
# Attributes whose values are identifiers to hash, not to scrub.
HASHED = frozenset({"case_id"})


def hid(key: bytes, value: str) -> str:
    """A keyed hash of an identifier: stable within a deployment, meaningless outside it."""
    return "h:" + hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:16]


@dataclass
class Scrubber:
    known: frozenset[str] = frozenset()
    hits: int = 0

    @classmethod
    def with_values(cls, values: Iterable[str]) -> Scrubber:
        return cls(frozenset(v for v in values if len(v) >= 6))

    def clean(self, value: Scalar) -> Scalar:
        if not isinstance(value, str):
            return value
        if any(p.search(value) for p in PATTERNS) or any(k in value for k in self.known):
            self.hits += 1
            return SCRUBBED
        return value


@dataclass
class Instruments:
    """The metrics of PLAN.md 9 that the agent events can feed (the rest come from the case store)."""

    meter: Meter

    def __post_init__(self) -> None:
        m = self.meter
        self.tokens = m.create_counter("a15.llm.tokens", unit="token", description="model tokens")
        self.cost = m.create_counter("a15.llm.cost", unit="USD", description="model cost")
        self.llm_latency = m.create_histogram("a15.llm.latency", unit="s", description="model call latency")
        self.tool_calls = m.create_counter("a15.tool.calls", description="tool calls by status")
        self.errors = m.create_counter("a15.errors", description="errors by type")
        self.transitions = m.create_counter("a15.case.transitions", description="state changes by status")


@dataclass
class OtelSink:
    """An EventSink that writes spans (and metrics). One per case; `close()` ends the case span."""

    tracer: trace.Tracer
    case_id: str
    key: bytes
    scrubber: Scrubber = field(default_factory=Scrubber)
    instruments: Instruments | None = None
    _case: Span | None = None
    _agents: list[Span] = field(default_factory=list)
    _llm: dict[str, Span] = field(default_factory=dict)
    _tools: list[Span] = field(default_factory=list)
    _stage: str = "RECEIVED"

    def __post_init__(self) -> None:
        self._case = self.tracer.start_span("case", attributes={"case.hid": hid(self.key, self.case_id)})

    def _attrs(self, attrs: dict[str, Scalar]) -> dict[str, Any]:
        out: dict[str, Any] = {"event.schema": EVENT_SCHEMA_VERSION, "case.stage": self._stage}
        for k, v in attrs.items():
            if v is None:
                continue
            out[k] = hid(self.key, str(v)) if k in HASHED else self.scrubber.clean(v)
        return out

    def _parent(self) -> Span:
        if self._tools:
            return self._tools[-1]
        if self._agents:
            return self._agents[-1]
        assert self._case is not None
        return self._case

    def _start(self, name: str, attrs: dict[str, Scalar]) -> Span:
        ctx = trace.set_span_in_context(self._parent())
        return self.tracer.start_span(name, context=ctx, attributes=self._attrs(attrs))

    def emit(self, name: str, **attrs: Scalar) -> None:
        _check(name, attrs)
        agent = str(attrs.get("agent", ""))
        if name == "agent.start":
            self._agents.append(self._start("agent.run", attrs))
        elif name == "agent.complete":
            if self._agents:
                span = self._agents.pop()
                span.set_attributes(self._attrs(attrs))
                if attrs.get("stop_reason") not in ("completed", None):
                    span.set_status(Status(StatusCode.ERROR, str(attrs.get("stop_reason"))))
                span.end()
        elif name == "llm.request":
            self._llm[agent] = self._start("llm.call", attrs)
        elif name == "llm.response":
            llm = self._llm.pop(agent) if agent in self._llm else None
            if llm is not None:
                llm.set_attributes(self._attrs(attrs))
                llm.end()
            if self.instruments is not None:
                i, labels = self.instruments, {"agent": agent, "model": str(attrs.get("model", ""))}
                i.tokens.add(int(attrs.get("input_tokens") or 0), {**labels, "kind": "input"})
                i.tokens.add(int(attrs.get("cache_hit_tokens") or 0), {**labels, "kind": "cache_hit"})
                i.tokens.add(int(attrs.get("output_tokens") or 0), {**labels, "kind": "output"})
                i.cost.add(float(attrs.get("cost_usd") or 0.0), labels)
                i.llm_latency.record(float(attrs.get("latency_s") or 0.0), labels)
        elif name == "tool.invoke":
            self._tools.append(self._start("tool.call", attrs))
        elif name == "tool.complete":
            if self._tools:
                span = self._tools.pop()
                span.set_attributes(self._attrs(attrs))
                if attrs.get("status") != "ok":
                    span.set_status(Status(StatusCode.ERROR, str(attrs.get("status"))))
                span.end()
            if self.instruments is not None:
                self.instruments.tool_calls.add(
                    1, {"tool": str(attrs.get("tool")), "status": str(attrs.get("status"))}
                )
        elif name == "case.transition":
            assert self._case is not None
            self._case.add_event("case.transition", self._attrs(attrs))
            if attrs.get("status") == "ok":
                self._stage = str(attrs.get("to_state"))
            if self.instruments is not None:
                self.instruments.transitions.add(
                    1, {"status": str(attrs.get("status")), "to": str(attrs.get("to_state"))}
                )
        elif name == "error.occurred":
            self._parent().add_event("error.occurred", self._attrs(attrs))
            if self.instruments is not None:
                self.instruments.errors.add(1, {"type": str(attrs.get("error_type"))})

    def close(self) -> None:
        for span in [*reversed(self._tools), *self._llm.values(), *reversed(self._agents)]:
            span.end()
        self._tools, self._llm, self._agents = [], {}, []
        if self._case is not None:
            self._case.set_attribute("case.final_stage", self._stage)
            self._case.end()
            self._case = None


@dataclass
class TeeSink:
    """Send every event to several sinks (the JSONL event log and the tracer)."""

    sinks: list[Any]

    def emit(self, name: str, **attrs: Scalar) -> None:
        for s in self.sinks:
            s.emit(name, **attrs)


def case_tracing(extra_processors: Iterable[Any] = ()) -> tuple[TracerProvider, InMemorySpanExporter]:
    """A tracer provider for one case run: spans kept in memory (for spans.jsonl) and sent to any extra
    processors (OTLP to the collector)."""
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    for p in extra_processors:
        provider.add_span_processor(p)
    return provider, memory


def span_rows(spans: Iterable[ReadableSpan]) -> list[dict[str, Any]]:
    rows = []
    for s in spans:
        ctx = s.get_span_context()
        rows.append(
            {
                "name": s.name,
                "trace_id": f"{ctx.trace_id:032x}" if ctx else None,
                "span_id": f"{ctx.span_id:016x}" if ctx else None,
                "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                "status": s.status.status_code.name,
                "attributes": dict(s.attributes or {}),
                "events": [{"name": e.name, "attributes": dict(e.attributes or {})} for e in s.events],
                "duration_ms": round(((s.end_time or 0) - (s.start_time or 0)) / 1e6, 3),
            }
        )
    return rows


def write_spans(path: Path, spans: Iterable[ReadableSpan]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in span_rows(spans):
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
