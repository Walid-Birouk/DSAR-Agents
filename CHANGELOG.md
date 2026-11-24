# Changelog

All milestones are local and unreleased so far. Versions will follow semantic versioning from the first public
release.

## Unreleased

- **Provisional choices settled and tested** (decision 0018). P1-P52 are accepted; eight changed, and
  0011-0015 are now accepted. Spend: USD 1.29 of DeepSeek V4.1 Flash, off-peak.
  - **E1 live** (one agent against seven, 78 test requests): 98.7% against 94.9% fully correct; USD 0.0040
    against 0.0050 a request; 0 hard-gate failures in both. One agent wins on this model.
  - **E3 live** (policy v2 also hides staff names): the same 98.7% at the same cost. R-OTHERS's new `redact`
    list drives the verifier, the pack and the graders, so the guard follows the policy.
  - **The eighth broken version**, `injection_defences_off`, run live on all 20 injection cases: the model
    followed none of them even without the prompt defences. The injection set is too easy to show their value.
  - **The 3-run spread** on smoke: 95-100% fully correct, USD 0.0032 a request, 2 of 20 verdicts changing.
  - **Code sends repeat and very large requests to a person** right after identity, with the DPO advisor's
    view. Before, 5 live cases went to approval instead.
  - **The Art. 12(4) refusal letter**, built by code: the reason, how to go ahead, the complaint and the
    court remedy. It never confirms an account to a possible impostor, and a person sends it.
  - **Identity:** the simulated requester answers every follow-up the code allows; a representative without a
    mandate is refused with that letter.
  - **The claims check** refuses a letter that speaks of its own approval ("will be checked before sending").
  - **Discovery must list every record** the scoped servers return for the person (R-SCOPE in code). The
    next guard, confirming every address in the customer's own messages, is documented and not built.
  - **The copy downloads as a PDF**, rendered with no library from the exact pack, its hash on every page.
  - **The seven-agent arm:** its redaction and validator briefs now carry what code knows (the customer's own
    orders; the code checks' results), tuned once on the dev smoke run. Redaction errors say where the text is.
  - **Tools:** `--rebaseline` and `--continue-from` keep recordings in step with the code honestly; replayed
    reports give the live latency.
  - **Operations:**
    - a bug fixed: the collector config was invalid YAML. It is validated in CI now, and the stack was run
      end to end;
    - metrics are exported at last (they were built only in tests);
    - Prometheus alert rules, and an operations runbook (`docs/operations.md`);
    - the service name in traces;
    - `Cache-Control: no-store` on the API, and an HTTPS-only session cookie;
    - Vercel security headers, a static first page, and a polling fallback;
    - CI builds and smoke-tests the image, validates the deployment configs, and replays the team; a tag
      publishes to GHCR.

- **The first page shows the guard layer** (decision 0017):
  - every refusal of the model in the recorded live runs, by guard, in plain words;
  - experiment E2 run live on the test split, guards on against guards off;
  - the cost per request, with what the simulated systems leave unmeasured.

  - E2 result: 7 requests with a problem reached approval with the guards off, 0 with them on.
  - A new redaction check, found by E2, refuses another person's email address or phone number left readable.
  - The cost section shows how the model's cost grows with the customer's history, measured and estimated,
    and the current 1M-token limit (about 230 records).

  New `a15 eval harness` writes `evals/reports/harness.json`. Shadow mode now also covers the redaction
  verifier and the follow-up limit (P14). The A15-027 story moves to `/story`.
- **The guided story** is the web app's new first page (decision 0016). It follows one request (A15-027)
  through four steps: the request and its three traps; the careless answer, which is the desk's real output
  with two safeguards off; the desk at work, a free replay of the recorded live run in which the model slips
  and the redaction guard catches it; and the visitor's decision. Try it moves to `/try`. `redaction_off` now
  also disables the order-number check, which it had missed since that check was added.
- **First live runs** on DeepSeek V4.1 Flash: the M2 cases, smoke, the golden 20 with the judge, and the test
  split (78 cases, 92.3% fully correct, USD 0.0046 a case), recorded in `evals/cassettes/`. Two bugs they found
  are fixed: the claims check rejected "0 records" for an empty system, and discovery precision counted the
  subject's own records as extra on cases that go to a person. Proof-of-control codes in the simulated desk
  are now keyed (a fixed key for recorded runs, so they replay; a secret per run in the web app). New
  `a15 eval regrade`. The redaction verifier now refuses another customer's order number left readable. The
  golden-20 labels are reviewed (`scripts/golden_check.py`); the review sheet keeps the
  reviewer's `review` block.
- **M7 (in part):** the public demo's protections. An input detector refuses real-looking personal data, live
  runs have per-IP limits and a proof-of-work human check, and visitors can bring their own key. Also a
  Dockerfile and a Compose deployment with Caddy (automatic HTTPS), backups, a load test (20 visitors), the
  architecture, threat model, running guide and eval report docs, and repository hygiene.
- **M6:** the web app. Try it, the run view with the reviewer, the trace view, Break it, Eval results, My runs
  and System, with sandboxes, SSE, an append-only audit log and Playwright tests.
- **M5:** the DPO advisor over A2A with an egress filter, OpenTelemetry without personal data, and resilience:
  retries, circuit breakers, fallback, checkpoints and resume.
- **M4:** the seven-agent desk, proof of control, the claims cross-check, shadow mode for E2, and the release
  service.
- **M3:** the eval harness: suites, exact graders, the LLM judge, a budget-capped runner, reports, and broken
  versions.
- **M2:** the agent core and the single-agent baseline, with record and replay.
- **M1:** the fictional company Ferlane and its MCP servers.
- **M0:** foundations: policy as code, the case state machine and GDPR deadlines.
