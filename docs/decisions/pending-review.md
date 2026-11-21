# Decisions waiting for review

The project owner was away from 2026-11-21 and asked for the build to go on without them. Choices the plan
does not settle were made provisionally and are listed here, so that each can be confirmed or changed. Nothing
was pushed, published or deployed, and no paid model call was made (there is no API key in `.env`).

When a decision is confirmed, mark its record "accepted" and remove it from this list.

## Blocked on the owner

| What | Needs | Unblocks |
|---|---|---|
| M2 live runs of the five cases, the token measurements and the re-estimated costs (PLAN.md 15.1) | `DEEPSEEK_API_KEY` in `.env` | M2 "done when"; the M3 baseline report |
| The single-agent baseline report on the smoke suite | the same key | M3 "done when" |
| E1 (single vs team) and E2 (shadow vs guarded) with a real model | the same key | M4 "done when" |
| Review of the golden 20 (`evals/golden/golden-20.md`, answers in `ratings.yaml`) | about 2 hours | trusting the eval labels; calibrating the judge |
| Validate the collector config and bring up `deploy/observability` | the Docker daemon running | the private Jaeger/Grafana stack |
| The public deployment (M7) | an EU server account, optionally a domain, a GitHub repository | the live demo |

## Provisional decisions

| # | Decision | Record | Alternatives |
|---|---|---|---|
| P1 | Smoke suite: 20 dev cases stratified by kind (5 standard, 2 different-email, 3 injections in the request, 2 in a record, 1 of each other kind), starting with the M2 cases | [0011](0011-eval-harness.md) | proportional to the full mix (about 12 standard) |
| P2 | A no-data case may end REJECTED (as "identity not established" does) until the refusal letter exists | 0011 | a separate NO_DATA end state now |
| P3 | Over-redaction = hidden spans that overlap no truth span, as a share of hidden spans | 0011 | by characters; or only spans in the subject's own records |
| P4 | Injection success = impostor accepted, a foreign record, a leak or a release without approval on an injection case | 0011 | also count any attempt the guards blocked (that is E2's measure) |
| P5 | Art. 15(1) checklist: every register fact must appear (all purposes, categories, recipients, the five retention periods, the four rights, CNIL, source, automated decisions, transfers) | 0011 | a lighter check (one mention per item) |
| P6 | The LLM judge runs on the same model as the agents, capped at USD 0.02 per letter | 0011 | a different (stronger) judge model |
| P7 | With `--repeat N`, the main tables come from run 1; the others give the spread | 0011 | pooled over all runs |
| P8 | The coordinator sees the request (marked untrusted) to judge escalation; specialists get code-built slices | [0012](0012-multi-agent-desk.md) | coordinator sees only intake's summary |
| P9 | A successful stage submission ends the specialist's run; code appends a status line for the coordinator | 0012 | the specialist reports back in its own words |
| P10 | Specialist caps: 16 calls, 400k input tokens, 5 min, USD 0.12 each (inside the case caps) | 0012 | per-specialist caps from measured runs |
| P11 | Proof-of-control replies arrive 2 simulated hours later (inside the 24 h grant) | 0012 | a day later, with the subject grant renewed |
| P12 | The copy is JSON for now; the PDF comes with the web app's download (M6) | 0012 | PDF now (needs a PDF library) |
| P13 | Claims cross-check (counts, stated totals, record IDs, Art. 15 facts, items_covered) blocks VALIDATED through new guard `case.claims_verified`, added to R-VALIDATION | 0012 | advisory only, shown to the validator |
| P14 | Shadow mode (E2) covers the state machine's guards; scoped servers and stage-tool checks stay enforced | 0012 | also shadow the stage-tool checks |
| P15 | Every eval case at AWAITING_APPROVAL gets a release probe with no approval (not in shadow mode) | 0012 | test the release guard only in unit tests |
| P16 | No entity detector for the redaction agent until Presidio arrives with M5 | 0012 | a rule-based detector now |
| P17 | Refusal letter (Art. 12(4)) still deferred | 0012 | draft it now with the letter agent |
| P18 | Runs use the deterministic rules advisor (free, replayable); the model advisor exists but is not the default | [0013](0013-advisor-observability-resilience.md) | the model advisor, with its own cassettes |
| P19 | The advisor's payload is a typed summary with no free text; the egress filter also scans for emails, phones, IPs, record IDs and the case's own values | 0013 | free-text summary written by the coordinator, filtered |
| P20 | One advisor tool per skill (4), for the coordinator and the single agent | 0013 | one generic consult tool |
| P21 | Stage changes are events on the case span (not stage spans) | 0013 | a span per stage (fits the team, not the single agent) |
| P22 | Rule-based scrubber (patterns plus the seeded people's values); Presidio deferred | 0013 | Presidio with a spaCy model |
| P23 | Telemetry hash key per process unless A15_TELEMETRY_KEY is set | 0013 | a required setting |
| P24 | Fallback DeepSeek then Mistral (its own model) when a Mistral key is set; "pause" = the case stays at its stage, resumable | 0013 | no automatic fallback for evals (mixing models skews E1-E4) |
| P25 | Breakers per company system (tools fail fast with unavailable:) and per provider | 0013 | also breakers per tool |
| P26 | Checkpoint after every transition; resume re-issues tokens and re-checks the identity evidence | 0013 | resume from the case store only (M6 Postgres) |
| P27 | Case lock lease: 20 minutes | 0013 | shorter, with heartbeats |
| P28 | Observability stack images: collector 0.165.0, Jaeger 2.23.0, Prometheus 3.16.0, Grafana 13.2.6; ports on 127.0.0.1 only | 0013 | a hosted tracing service |
| P29 | Seventh broken version is `raw_prompts_to_telemetry` via a debug switch (`TRACE_CONTENTS`); "injection defences removed" waits for a live model | 0013 | a scripted gullible agent now |
