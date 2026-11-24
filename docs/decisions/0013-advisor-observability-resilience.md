# 0013: The DPO advisor, observability and resilience

**Status: accepted** on 2026-11-23, after starting as provisional choices ([0018](0018-provisional-choices-settled.md)
says what changed).

**Decision.** M5 adds the advisor service over A2A, OpenTelemetry tracing and metrics without personal data,
and the resilience of PLAN.md 10.

## The DPO advisor (`src/article15/advisor/`)

- **A separate A2A agent.** It uses `a2a-sdk` 1.3.1 (A2A protocol 1.0, JSON-RPC binding). The current
  version was checked on PyPI on 2026-11-21, and the SDK's API was read from the installed package.
  - Its Agent Card is public at `/.well-known/agent-card.json`, with one skill per question: possibly
    excessive, extension, special category, other rights.
  - JSON-RPC sits at `/a2a`, behind a bearer key (`A15_ADVISOR_KEY`).
  - `a15 advisor serve` runs it on its own.
  - In case runs it runs in process, over the real protocol through an ASGI transport, as the MCP servers do.
- **What it receives is minimised by type.** A `CaseSummary` has a question, dates, counts and flags, and no
  free-text field.
  - Code fills it in from the case file. Earlier requests are counted from the CRM through the scoped tools.
  - The egress filter (guard `a2a.egress_filter`, rule R-MINIMISE-EGRESS) scans the serialised payload for
    email addresses, phone numbers, IP addresses and record IDs, and for the case's own names and addresses.
    It refuses the message when it finds any.
  - Every payload sent is logged in `advisor_payloads.jsonl`, and the eval's advisor gate scans it for every
    seeded personal value.
- **One client tool per skill** (`advisor_possibly_excessive`, ...), for the coordinator and the single
  agent. The advice goes into the case file. *It is advice:* a person still decides (R-EXCESSIVE).
- *(Open choice:)* runs use the deterministic `rules_advice`: free, replayable, and grounded in the Appendix A
  passages (`knowledge.py`). `ModelAdvisor` (its own model, a knowledge tool and typed advice) is built and
  tested, but not yet the default.
- **Tier T2:** with `advisor="off"`, or with its breaker open, the tool says the advisor is unavailable, and
  the case goes straight to a person.

## Observability (`src/article15/telemetry/`)

- **One trace per case:** case > agent run > model call / tool call. A specialist's run nests inside the
  coordinator's tool call. *(Open choice:)* stage changes are events on the case span, and every span carries
  its stage, rather than stage spans: one agent's run spans several stages.
- **Identifiers are keyed hashes** (`h:` + HMAC). The key comes from `A15_TELEMETRY_KEY`, or is random per
  process.
- **The scrubber** checks every attribute before it is set. It looks for email, phone, IPv4 and record-ID
  patterns and for the seeded people's names and mailboxes, and replaces a match with `[scrubbed]`.
  - *(Open choice:)* this is rule-based. Presidio is not adopted yet, because its spaCy models are large for
    CI.
  - The collector's redaction processor is a third line (`deploy/observability/otel-collector.yaml`). *That
    config is not validated yet:* the Docker daemon was not running.
- **Exporters:**
  - In memory per case, written to `spans.jsonl`, so the leak gate scans both events and spans.
  - OTLP over HTTP to the private collector when `A15_OTLP_ENDPOINT` is set.
- **Metrics:** tokens, cost, model latency, tool calls by status, errors by type, and transitions by status.
- **The private stack** (`deploy/observability/`): collector 0.165.0, Jaeger 2.23.0, Prometheus 3.16.0 and
  Grafana 13.2.6, with tags checked on Docker Hub on 2026-11-21. Every port binds to 127.0.0.1.

## Resilience (`core/resilience.py`, `core/live.py`, `desk/checkpoints.py`)

- **Retries** for transient errors only: exponential backoff with full jitter, capped at 30 s, at most 5 tries.
- **Circuit breakers** open at 50% failures over 10 calls and allow one trial call after 60 s. There is one
  per model provider, one per company system (its tools fail fast with `unavailable:` while open, and the case
  goes to PARTIAL) and one for the advisor.
- **Fallback:** DeepSeek, then Mistral with its own model when `MISTRAL_API_KEY` is set, then pause. A pause
  is a provider error: the case stays at its stage, checkpointed and resumable.
- **Tiers T1 to T4** are computed from the kill switches, the breakers and the spend.
- **Checkpoints** are saved after every successful transition. `run_case(..., resume=True)` continues from the
  last one:
  - tokens are issued anew, and the identity evidence is re-checked as it was the first time;
  - confirmed addresses come from the scope store;
  - the records are fetched again;
  - finished stages are not redone.
- **Operation IDs** are attached to the follow-up question, the address confirmation and the release, so a
  resumed stage never sends twice.
- **One worker per case:** a lease lock that expires after 20 minutes by default, and can be taken over once
  it has.

## Testing the tests

There are seven broken versions now:
- the four of 0011;
- `release_guard_off` (0012);
- `egress_filter_off`: the payload carries the sender, and the advisor gate turns red;
- `raw_prompts_to_telemetry`: prompts go on the spans with the scrubber off, and the telemetry gate turns red.

With the scrubber on, the same misconfiguration leaks nothing (a test).

**Why.**
- A typed payload with no text field is a stronger guarantee than any filter, and the filter catches what the
  type cannot.
- Rule-based advice and in-process A2A keep runs deterministic and free, while still using the real protocol.
- Checkpoints, operation IDs and a lease together make "resume, never send twice" testable.

**Given up.**
- The advisor's model mode is not used by default. Its calls would need their own cassettes.
- Presidio is deferred, and the collector config is not validated.
- No Grafana dashboards are provisioned yet, only the data sources.
- The plan's broken version "injection defences removed" needs a live model.
- The redaction specialist still has no entity detector (P16).

**Tested by.**
- `tests/test_resilience.py`:
  - breakers, retries and backoff, fallback;
  - a system that is down, and the tiers;
  - a crash after discovery that resumes with nothing redone and the follow-up sent once;
  - a provider outage that pauses cleanly;
  - the lock and operation IDs;
  - no case tokens or bearer keys in any log.
- `tests/test_observability.py`:
  - the trace hierarchy, and no personal value in any span;
  - the scrubber, raw prompts caught by the scrubber, and metrics;
  - the advisor over A2A, its card, its key and the egress filter;
  - rules and model advice;
  - the desk's payload carrying no personal data, and the advisor switched off.
- The oracle is green on all 160 cases in both arms, with both gates measured at 0.
