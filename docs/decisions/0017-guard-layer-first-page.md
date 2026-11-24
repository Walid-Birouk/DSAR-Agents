# 0017: The first page shows the guard layer, with live numbers

**Status: accepted** on 2026-11-23. After decision 0016 the demo
still did not show that a harness had been built, and that its value did not show: one case, with one guard
firing once, is an anecdote.

**Decision.**
- **The harness shown is the guard layer:** the code around the model that decides what it can see, which step
  comes next and whether a result is acceptable. The eval harness stays on the Eval results page.
- **The first page (`/`)** is built from recorded live runs, not from the scripted agent. It has five parts:
  1. the claim, with three headline numbers;
  2. why a guard layer and not a better prompt, with sources;
  3. experiment E2: the same model on the same requests, guards on against guards off;
  4. the seven guards, each with its rule IDs, where it is enforced, how often it refused the model, and real
     refusals in plain words;
  5. the cost: what was measured, and what was not.
- **The A15-027 story** (decision 0016) stays as the drill-down, at `/story`.
- **E2 is run live** on the 78-case test split (DeepSeek V4.1 Flash, one agent, thinking off), with a budget of
  about USD 0.40. *What "guards off" means (was P14):* shadow mode now also covers the
  stage tools' policy checks, namely the redaction verifier (R-OTHERS) and the follow-up limit (R-IDENTITY).
  The scoped servers stay enforced, so E2 measures the checks, not the scoping. The guarded arm is `live-test`,
  with the two cases re-run after the order-number check was added (`live-test-guard`), so both arms use the
  same checks.
- **Value is shown honestly.** The cost figures come from simulated systems.
  - The page states what was measured: model cost, model and tool calls, tokens and time per request.
  - It states what was not: real systems' latency on each tool call, longer real histories, integration,
    hosting, and the reviewer's time.
  - It says the model's cost is not comparable one-to-one with the manual cost.
- **Sources on the page**, each checked on 2026-11-23:
  - GDPR Art. 12(3), 15(4) and 33 (EUR-Lex).
  - The EDPB report on the right of access (20 January 2025): 30 authorities examined 1,185 controllers in
    2024; access is "one of the most frequently exercised data subject rights".
  - Gartner, *Market Guide for Subject Rights Request Automation* (Henein, Willemsen, Woo, 12 April 2023): a
    manual estimate of USD 1,524 per request. It is quoted from DataGrail's methodology page, because the
    Gartner report is no longer available; the page says DataGrail sells such tools.

**Results (2026-11-23, USD 0.35 in all).**
- **E2, 78 test requests:**
  - With the guards off, 7 requests reached the approval step with a problem. 3 left another person's
    details readable, and 2 of those were prompt injections that worked.
  - With the guards on: 0 (8 refusals). Fully correct: 68 against 74.
  - The full breakdown is in [the eval report](../eval-report.md).
- **A gap, found by E2 and fixed:**
  - *The gap:* on A15-072, another customer's email address, planted by an injection, stayed readable, and no
    guard saw it, because the redaction verifier only checked what the model chose to hide, plus order
    numbers.
  - *The fix:* a new check refuses any email address or phone number left readable that is not one of the
    customer's verified ones.
  - *The proof:* replaying the recorded shadow runs (`scripts/rebaseline_shadow.py`), it now flags A15-072.
    Live, it caught another customer's address in A15-124 and A15-149, and the model fixed both
    (re-recorded).
  - *The trade-off:* it errs on the safe side, so a customer's own second address that was never confirmed
    is hidden too.
- **Cost scaling:** the page shows the measured cost per record (about USD 0.0002)
  and a table of estimates for bigger histories and other price settings. It also shows the current limit:
  about 230 records before the 1M-token cap per request, above which a request goes to a person. Every
  estimate is labelled as such.

**How the data is built.** `a15 eval harness` (`evals/harness_summary.py`) reads the live eval runs and writes
`evals/reports/harness.json`, which is committed; the runs themselves are not, because they hold full
transcripts.
- **Grouping:** every refusal goes under the guard that made it. Harmless refusals of a step already done
  (re-linking an address already linked) are counted apart.
- **Plain words:** each example says what the model tried, in plain words, next to the refusal text.
- **Counts:** a request run in two suites, or re-run, counts once per run. The page says "runs" and gives the
  number of distinct requests.

**Why.** The project's argument is that rules enforced in code catch what rules in a prompt miss (PLAN.md 2).
The page now shows that argument with measured numbers, across every recorded live run, instead of one replay.

**Tested by.**
- `tests/test_harness_summary.py`:
  - each refusal is grouped under the right guard;
  - the summary counts catches, release probes and both E2 arms with re-runs;
  - each problem let through is classified, and a missed leak is checked against the re-baselined recording;
  - the cost scaling is checked against the price table.
- `tests/test_letter_inputs.py`: the email and phone check catches another person's address or number, and
  hides an unconfirmed one.
- `tests/test_team.py`: shadow mode lets a redaction leak through and counts it once; enforced mode refuses
  it. The shadow case fails without the change.
- `tests/test_web.py`: `/api/harness` serves the summary.
- `frontend/e2e/flows.spec.ts`: the first page shows the numbers and a real refusal, and links to the story;
  axe and keyboard checks cover `/` and `/story`.
