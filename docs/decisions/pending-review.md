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
