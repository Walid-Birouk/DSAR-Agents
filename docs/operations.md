# Operations runbook

What to do when an alert fires, and how to make the routine changes safely. The alerts are in
[deploy/observability/alerts.yml](../deploy/observability/alerts.yml). They are evaluated by the private
Prometheus, fed by the desk's OpenTelemetry metrics through the collector. Setting up the stack is in
[running.md](running.md).

**Where to look first:**
- Grafana (metrics) and Jaeger (one trace per case, keyed by the case ID), over the SSH tunnel;
- the web app's System page (runs today, spend against the cap);
- the audit log of a case (every stage, decision, approval and release, with the pack hash).

None of them holds personal data. A case's contents are in its run directory on the server; open it only for
that case.

## Alerts

### Spend near the daily cap
`DeskSpendNearDailyCap`: model spend in the last 24 h is past 80% of the EUR 2 cap.

The app stops new live runs at the cap by itself. Visitors then get prepared cases (scripted) or bring their
own key. Nothing is lost; the question is whether the spend is legitimate.

1. **Is it traffic or abuse?** Look at the System page (runs today), then at the per-IP limits in the logs.
   Many runs from a few addresses suggest the proof-of-work check or the per-IP limits need tightening
   (`web/guards.py`).
2. **Is a run costing more than it should?** A cost per run far above about USD 0.005 (DeepSeek) or 0.03
   (Mistral Large, EU) points at a loop or at a very large case.
   - The per-case cap (USD 0.30) stops a run in any case.
   - Very large histories go to a person after identity (decision 0018), so they should not reach the model.
3. **Raise the cap only on purpose:** change it in the configuration and record why. Remember the provider
   account's own spending limit too.

### System failures
`DeskSystemFailures`: more than 5% of tool calls fail for system reasons (`error`, `timeout`, `unavailable`).
The model's form errors (`invalid`) and guard refusals (`refused`) are normal and are not counted.

1. **Which system?** Group `a15_tool_calls_total` by `tool`:
   - the prefix names the system (`crm_`, `orders_`, `helpdesk_`, `weblogs_`);
   - `advisor_` means the DPO advisor;
   - `case_` means the desk's own code.
2. **A company system.**
   - Its circuit breaker opens after repeated failures: its tools then fail fast with `unavailable:`.
   - Cases that need it go to PARTIAL, naming the system. They are never sent incomplete.
   - Fix the system; the breaker closes on its own after 60 s.
3. **The advisor (tier T2).** Edge cases go straight to a person, with "advisor not available" in the reason.
   Restart the advisor container.
4. **The model provider.** Retries handle short outages.
   - In the web app, DeepSeek falls back to Mistral when a Mistral key is set; the demo itself runs on
     Mistral without fallback.
   - A run that cannot reach any provider pauses at its stage and can be resumed (checkpoints).
5. **The desk's own code** (`error` on a `case_` tool): look at the traces of the failing cases in Jaeger,
   then replay their recordings locally (`a15 eval run --agent replay`).

### The model is slow
`DeskModelSlow`: 95th percentile model latency above 30 s for 15 minutes.

- **The provider's status page first.** Off-peak hours change DeepSeek's load; Mistral's EU endpoint has its
  own capacity.
- **Long latency on one case only:** usually a large history. Code sends 150+ records to a person.
- **The hard stop:** the per-case time cap (15 minutes) stops a run in any case, and the case goes to a
  person.

### Guard refusals spike
`DeskGuardRefusalsSpike`: the guards refused more than 20 state changes in an hour.

A refusal is the guard layer working. A spike means either the model changed (a provider update, or a prompt
or policy change), or someone is pushing (Break it is public).

1. **Which rule?** The refused transitions carry the rule IDs: group by `to` and read a few traces.
2. **A model change?** Replay the smoke recordings against the current code (free; must be identical). Then
   run the live smoke suite and compare with the baseline report (`--baseline`).
3. **An attack?** The Break it counter on the System page shows attempts and blocks. Every block is a
   guard doing its job; nothing to do unless something got through.

## Routine changes

### Changing the policy
The policy is one file, rendered into the prompts and enforced by code. A change is an experiment, as E3 shows:
1. Copy `policy.yaml` to a new version, change the rule, and bump the version.
   - If the rule's structured fields (such as R-OTHERS's `redact`) change, the guards follow them.
   - `a15 policy-lint` must pass.
2. Run the test split live with `--policy <file>`, then `a15 eval experiment E3 <old> <new>`.
3. Ship only if the hard gates hold and the bars do not regress. Then re-record the suites CI replays,
   because the prompts changed.

### Changing the model
- Run E4: the test split on the new model, then `a15 eval experiment E4`.
- Compare the cost per fully correct case and the hard gates, not only accuracy.
- Evals never fall back to another model, so each result is one model's.

### After a code change
CI replays every recorded live run.
- **A case now stops sooner:** `--rebaseline` updates its recording for free.
- **The model would be asked something new:** that case needs `--continue-from`. This pays only for the
  calls after the change.
- **Never edit a recording by hand.**

### Rotating secrets
- **The secrets:** `A15_SCOPE_SECRET`, `A15_APPROVAL_SECRET`, `A15_TELEMETRY_KEY` and `A15_ADVISOR_KEY`
  live in `deploy/production.env`.
- **To rotate:** generate new ones (`python -c "import secrets; print(secrets.token_hex(32))"`), then
  restart the stack.
- **The effect:** approvals waiting on the old approval secret no longer verify. A reviewer approves those
  packs again; nothing is sent without a valid approval.

### Restoring a backup
The nightly backup (`deploy/backup.sh`, 14 days kept) holds the web store: sessions, runs and the audit log.
1. Stop the app.
2. Copy the backup's files back into the `runs` volume.
3. Start the app.

Sandboxes are not backed up. They are rebuilt from the seed and expire after 24 hours anyway.

## Not covered yet
- **A "case near its deadline" alert.** It needs a metric of open cases by days left. The case store has the
  deadlines; the metric is not built.
- **Notifications.** The rules fire in Prometheus. Sending them needs an Alertmanager with a
  notification channel (email or Slack).
