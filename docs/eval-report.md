# Eval report

**State on 2026-11-22: the harness is verified; the agents are not measured yet.** No model has been called:
there is no API key in this environment. Everything below comes from the *oracle*: a perfect agent built from
the generator's ground truth, which drives the real desk (state machine, scoped MCP servers, the advisor over A2A,
the pack and the release service). Its runs prove that the desk can reach every correct outcome and that the
graders score it correctly. They say nothing about how well a model does; the model runs below are still to do.

## The harness on a perfect agent (full suite, 160 cases)

`uv run a15 eval run --suite full --arm single|team --jobs 8` (oracle):

| Metric | Bar | One agent | Seven agents |
|---|---|---|---|
| Impostor accepted | 0 (gate) | 0 | 0 |
| Third-party data in a pack | 0 (gate) | 0 | 0 |
| Another person's record in a pack | 0 (gate) | 0 | 0 |
| Release without a valid approval (release probe on every case) | 0 (gate) | 0 | 0 |
| Successful injections | 0 (gate) | 0 | 0 |
| Personal data in telemetry (events and spans) | 0 (gate) | 0 | 0 |
| Personal data in advisor payloads | 0 (gate) | 0 | 0 |
| Identity decision accuracy | >= 95% | 100% | 100% |
| Discovery recall | >= 98% | 100% | 100% |
| Redaction recall | >= 98% | 100% | 100% |
| Art. 15(1) items in the letter | 100% | 100% | 100% |
| Cases fully correct | report | 100% | 100% |
| Tool calls per case | report | 13.3 | 18.9 |

Latency here is the desk's own work: the oracle answers instantly. p50 is about 3.6 s and p95 about 6 s per case.

## Testing the tests

A check that has never been seen to fail can't be trusted (PLAN.md 8.6). Each deliberately broken version below
turns its gate or bar red, while the same cases are green when nothing is broken (`tests/test_evals.py`):

| Broken version | What breaks | Turns red |
|---|---|---|
| `redaction_off` | masking does nothing and the redaction verifier passes anything | third-party leaks |
| `identity_skipped` | the claimed customer is granted without evidence | impostor accepted |
| `lookalikes_included` | records that only look like the subject's enter the pack | foreign records |
| `retention_missing` | the letter leaves out the retention periods | Art. 15(1) completeness |
| `release_guard_off` | the release guard passes anything | release without approval |
| `egress_filter_off` | the advisor's payload carries the sender | advisor payload leaks |
| `raw_prompts_to_telemetry` | prompts go on spans with the scrubber off | telemetry leaks |

Run artifacts tampered with in eight ways (leak, foreign record, impostor, telemetry value, missing retention,
over-redaction, release without approval, a cap stop counted as an escalation) each turn their check red as
well.

The plan's eighth version, "injection defences removed", needs a model that can be fooled; it comes with the
first live run.

## Still to measure (needs a model key)

| Step | Command | Cost estimate |
|---|---|---|
| M2: the five M2 cases live, then replayed identically | `a15 run --record evals/cassettes/m2`, then `--replay` | well under USD 1.50 (capped at 0.30 per case) |
| M3: the single-agent baseline on the smoke suite | `a15 eval run --suite smoke --agent live --record evals/cassettes/smoke --budget 2` | under USD 2 |
| M4: E1 (one agent vs seven) and E2 (shadow vs guarded) on smoke, then full | `--arm single/team`, `--shadow`, then `a15 eval experiment` | about USD 5-15 (PLAN.md 15.1) |
| M7: test split, 3-run spread, E3 (policy v2), E4 (DeepSeek vs Mistral) | `--suite test --repeat 3` | about USD 15-35 in all |
| Letter quality | `--judge`, calibrated against the golden-20 ratings | small |

Until then, the bars in PLAN.md 8.2 stay starting points.
