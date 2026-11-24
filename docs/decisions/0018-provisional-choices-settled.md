# 0018: The provisional choices, settled and tested

**Status: accepted** on 2026-11-23. Milestones M2-M7 were built with provisional choices P1-P52
([provisional-choices.md](provisional-choices.md)). This record settles each one. Each choice was tested where
a test was possible, and the evidence is named. It also says what each choice would cost to change.

Live spend for this record: USD 1.29 (DeepSeek V4.1 Flash, off-peak).

## 1. The provisional decisions P1-P52

All are **accepted as they stand**, except the eight marked *changed* below.

| # | Settled as | Evidence or reason |
|---|---|---|
| P1 | accepted | the stratified smoke suite covers every kind of case; the 3-run spread is measured on it |
| P2 | accepted, now with a refusal letter | a no-account case ends REJECTED with the Art. 12(4) letter (section 2.3) |
| P3, P4, P5, P7 | accepted | the graders behave as intended on every live run; tampered artifacts turn each red (0011) |
| P6 | accepted, with a limit stated | the judge runs on the agents' model; it is **not calibrated against a person** (section 4) |
| P8, P9, P11, P13, P15 | accepted | E1 ran live with them (section 3.1) |
| P10 | accepted | measured on the live team run: 1 specialist in 78 cases hit its 16-call cap and the coordinator recovered |
| P12, P37 | *changed*: PDF now | the copy downloads as a PDF rendered from the exact pack (section 2.5) |
| P14 | replaced by 0017 | - |
| P16, P22, P40 | accepted: no Presidio | the code checks (other customers' order numbers, emails, phones; staff names under v2) are the detector; 0 telemetry leaks on every run |
| P17 | *changed*: refusal letter now | Art. 12(4) letter built by code (section 2.3) |
| P18-P21, P23, P25-P27 | accepted | traces sent to the real collector and Jaeger (section 2.8) |
| P24 | *changed* for evals | live eval runs never fall back to another model (it would mix two models in one result); the web app keeps the fallback |
| P28 | accepted, one bug fixed | `otelcol validate` found the collector config invalid (section 2.8) |
| P29 | accepted, and the eighth version built | `injection_defences_off` (section 3.4) |
| P30-P36, P38, P39, P41-P45 | accepted | - |
| P46 | *changed*: also on Vercel | the same security headers in `frontend/vercel.json`, for a frontend hosted there |
| P47, P48 | accepted | the largest live case cost USD 0.015 (team) and 0.008 (one agent), under the 0.05 cap |
| P49 | accepted, limit stated | the golden labels were checked by script and an automated review, not yet by a person |
| P50, P51 | accepted | - |
| P52 | accepted | the prompt line stays in the team prompt only; the code checks cover the single agent |

## 2. New decisions, and what they fixed

### 2.1 Code sends repeat and very large requests to a person (R-EXCESSIVE, R-DEADLINE)
- **The gap:** on 5 live cases (repeat requests, a 250-record history) the model prepared a pack instead of
  sending the case to a person, because the guard relied on a flag the model had to set.
- **The fix:** right after identity is verified, and before any search, code checks:
  - earlier access requests in the CRM;
  - the size of the person's history, from the scoped servers.

  It uses the DPO advisor's own thresholds: 2 requests in 12 months, 150 records. Two new guards,
  `case.repeat_request_needs_a_person` and `case.large_request_needs_a_person`, then send the case to a person.
- **The advice:** code also asks the advisor itself (minimised summary, egress filter), so the person gets
  the reason and the advice. It is traced as an event on the case span.
- **Side effect:** the model never reads a 250-record history it is not going to answer. That case cost
  USD 0.045 before.
- **Tests:** `tests/test_observability.py` (both kinds, with the advice; and with the advisor off).

### 2.2 Identity follow-ups end in a decision
- **The gap:** the code allowed 2 follow-up questions, but the simulated requester answered only one. Two
  impostor runs stopped half-way, and the labels contradicted each other: a representative was expected to
  be asked again, an impostor to be refused.
- **The fix:**
  - the requester answers every follow-up the code allows;
  - a representative who confirms they have no written mandate is refused, with a letter that says how to
    go ahead (label changed in the generator; the cases' text is byte-identical).
- **Tests:** `tests/test_desk.py`.

### 2.3 The refusal letter (GDPR Art. 12(4))
- **What:** code builds the letter, never the model, in French or English.
- **What it says:** the reason, how to go ahead, and the complaint to the CNIL with its address. It also
  names the judicial remedy.
- **Why code writes it:** it may go to an impostor. The model's own reasons often say whether the account
  exists ("the address is registered on account C0178"); this letter never does.
- **The guard:** `case.rejection_explained` refuses REJECTED unless the letter is there and names the
  complaint and the remedy.
- **Sending it:** a person sends it from the run view ("Send the refusal letter"). A reviewer's own
  refusal needs a reason written for the requester.
- **Tests:** `tests/test_refusal_pdf_rerecord.py`, `tests/test_web.py`; mutation-checked (section 5).

### 2.4 The letter must not speak of its own approval
- **The gap:** in 6 of 416 letters from live runs, the letter says it "will be checked by an authorised person
  before it is sent". That is false once a person has approved it, and the judge marked one down (3.33).
- **The fix:** the claims cross-check now refuses such a sentence. It leaves true sentences alone, such as
  "masked before the copy was sent".
- **Result:** the re-recorded A15-027 letter scores 4.33.

### 2.5 The PDF copy
- **How:** `desk/pdf.py` writes the PDF by hand: standard Courier and WinAnsi encoding, so French accents
  work and no font file is needed.
- **Why by hand:** fpdf2 is LGPL and pulls in Pillow. Writing it by hand needs no library, and the same
  pack always gives the same bytes.
- **Tied to the approval:** every page carries the pack's SHA-256, the hash the approval signs.
- **Checked:** pypdf (strict mode) reads it.

### 2.6 A redaction error says where the text is
- **The gap:** a specialist that addressed a ticket instead of its message got the same error again and
  again.
- **The fix:** the error now names the record's text fields, or the ticket's message records.

### 2.7 Discovery must list everything the servers return (R-SCOPE)
- **The gap:** the R-SCOPE guard checked that discovery succeeded, for the right person, but not that it
  was complete.
- **The fix:** the scoped servers return only the verified person's records, so code now refuses a list
  that leaves any of them out, and names what is missing. In shadow mode it logs instead (it caught one in
  the E2 shadow run, A15-062).
- **Tested:** `tests/test_stage_tools.py` (mutation-checked).
- **The next guard, not built:**
  - **What still slips:** records behind a second address of the customer's, written in their own ticket,
    which the model does not ask to confirm (proof of control). This happened once each in the v2 and the
    shadow runs (A15-055, A15-124); in the one-agent v1 run the model asked in all 9 such cases.
  - **The check:** code would insist that every address in the customer's own messages is either
    confirmed or excluded with a reason.
  - **Why it waits:** 22 of the 78 test cases hold someone else's address in those messages, so it would
    change the model's path in many recordings and shift E1-E3's baselines. It waits for a re-recording
    budget.

### 2.8 The seven-agent arm, tuned once on the dev split
The first live smoke run of the team found two wiring bugs, both in what code tells a specialist:
- **The redaction specialist** was not told which orders were the customer's own. It hid them (35%
  over-redaction).
- **The validator** started fresh with no view of the structured records or of the code checks. It refused
  correct packs, and 7 of 20 cases went to a person.

Both now get that context in their code-built brief, and the redaction role says "hide exactly what the
policy names". On smoke (dev split) this took it from 70% to 90% fully correct and from 35% to 0%
over-redaction. The test split was then run once.

### 2.9 Operations
- **The collector bug:** `otelcol validate` showed that the collector config was invalid YAML: `\w`
  inside double quotes. The private stack would not have started. Fixed and checked:
  - the stack brought up;
  - traces sent;
  - Jaeger queried: one trace per case, no value holding an email.
- **The service name:** the desk now names itself (`article15-desk`), and the deprecated exporter alias is
  replaced.
- **Metrics:** they were never exported. The metric instruments existed only in tests, so Prometheus would
  have stayed empty. A process-wide meter now exports them over OTLP; checked in a running Prometheus.
- **Alerts:** four alert rules (`deploy/observability/alerts.yml`), validated with `promtool` and evaluated
  by a running Prometheus:
  - spend near the daily cap;
  - system failures over 5%;
  - model latency;
  - a spike in guard refusals.

  [docs/operations.md](../operations.md) is the runbook. Sending alerts needs a notification channel (email or Slack).
- **API caching:** every API answer is `Cache-Control: no-store`. A CDN in front, such as a Vercel rewrite,
  honours it, and each answer belongs to one visitor's session.
- **The session cookie** is HTTPS-only in production (`A15_SECURE_COOKIES=1`).
- **Without a backend:** the first page falls back to a static copy of its numbers (`/harness.json`), and the
  app says when the live desk is not connected.
- **Live updates:** the run view polls if a proxy drops the event stream. Vercel's docs do not say whether
  external rewrites stream; they do say rewrites proxy and honour upstream Cache-Control.
- **CI** gained four things:
  - image build and container smoke test (non-root, read-only, health);
  - config validation for Compose, the collector, Prometheus and Caddy;
  - replay of the team's recordings;
  - the oracle on all 160 cases.

  A tag publishes the image to GHCR (`release.yml`).

### 2.10 Recordings follow the code, honestly
Two tools keep recorded live runs in step with code changes without pretending:
- **`a15 eval run --agent replay --rebaseline`:** when code now stops a case sooner, every call used is a
  recorded one, in order. The calls past that point are dropped, and the new outcome becomes the expected
  one. A case that needs a call never recorded is listed instead.
- **`a15 eval run --agent live --record DIR --continue-from DIR`:** replays a recording while the requests
  match, then calls the live model from the first changed request. The model's earlier behaviour is kept
  (including the A15-027 slip the story shows), and only the changed tail is paid for.

Of the recordings made before these changes, 9 were re-baselined for free and 9 re-recorded from their first
changed call; every other one replayed unchanged. A replayed
report gives each case's latency from its live recording, not the replay's milliseconds.

## 3. Experiments run live

All runs: DeepSeek V4.1 Flash (`deepseek-flash`), thinking off. Full numbers in
[the eval report](../eval-report.md).

**3.1 E1, one agent against seven, on the 78 test requests.**

| | One agent | Seven agents |
|---|---|---|
| Fully correct | 98.7% | 94.9% |
| Hard-gate failures | 0 | 0 |
| Cost per request | USD 0.0040 | USD 0.0050 |
| Latency p50 | 25 s | 32 s |
| Searches repeated after a handoff | 61 | 366 |

On this model one agent is cheaper, faster and slightly more accurate. Seven agents bring separation of
duties, which the guards do not need.

**3.2 E2** was re-baselined on the current code, with no new model call.
- **Guards off:** 9 requests reached approval with a rule broken, against 7 in 0017. The shadow arm now also
  logs the triage guards: a repeat request reached approval with no person deciding. The 9 are 3 leaks, 3
  skipped decisions and 3 harmless.
- **Guards on:** 0, and 77 of 78 fully correct.

**3.3 E3, policy v1 against v2 (staff names hidden too, their role kept), on the 78 test requests.**
- **How v2 differs:** only rule R-OTHERS. Its structured `redact` list now drives the redaction verifier,
  the pack and the graders, so the guard moved with the policy.
- **Result:** both reach 98.7% fully correct, with 0 leaks and the same over-redaction (1.8%), at USD 0.0039
  against USD 0.0040 a request.
- **The guard's part:** it refused 4 more submissions under v2.

**3.4 The eighth broken version, `injection_defences_off`:**
- **What it removes:** the `<untrusted_...>` marking, the "data, not instructions" rule and the redaction
  checks.
- **Result:** on all 20 injection cases (both splits) the model followed **none** of the planted
  instructions: 0 leaks, 20 of 20 correct.
- **What it means:** with this model and this injection set, the prompt defences' value cannot be
  measured. Two things were measured:
  - the code guards' value, by E2;
  - the grader's ability to turn red, by tampered artifacts (0011).

  A harder injection set is future work. The recording is replayed in the tests.

**3.5 Spread: smoke, 3 live runs.**

| Fully correct | Cost per request | Cases whose verdict changed between runs |
|---|---|---|
| 95-100% | USD 0.0032 | 2 of 20 |

## 4. Still open
- **A person's letter scores** in `evals/golden/ratings.yaml`, to calibrate the judge. A judge
  calibrated by another model would not be a calibration.
- **E4, DeepSeek against Mistral:** needs a Mistral key.
- **The public deployment:** a backend host, a domain and the Mistral key for the demo.
- **A public red-team round:** needs the live demo.

## 5. How the new checks were tested
- **Mutation checks:** every new test was run against a deliberately broken version of the code it
  protects, and each one turned red:
  - the refusal letter without its remedy sentence;
  - a guard that accepts a letter without it;
  - a narrower own-approval pattern;
  - unescaped PDF strings;
  - triage switched off;
  - the representative's refusal kind;
  - the discovery completeness check removed.
- **The oracle:** green on all 160 cases in both arms (v1), and on the test split under v2.
- **The recordings:** all 457 replay exactly (`tests/test_cassettes.py`).
