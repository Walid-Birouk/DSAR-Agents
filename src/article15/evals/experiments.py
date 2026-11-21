"""Experiments for the write-up (PLAN.md 8.8): two eval reports side by side.

- E1, one agent against seven: `--arm single` against `--arm team`, same suite and model.
- E2, prompt-only policy against prompts plus code guards: `--shadow` (the state machine's guards only log
  what they would block) against the normal run, on the adversarial cases. It measures how many violations the
  agents attempt (refused, or let through in shadow mode) and how many get through.
- E3, policy v1 against v2 (staff names redacted too), and E4, DeepSeek against Mistral, compare the same way.

`a15 eval experiment E1 A/report.json B/report.json` writes the comparison as Markdown.
"""

from __future__ import annotations

from article15.evals.metrics import SPEC
from article15.evals.report import Report, fmt

EXPERIMENTS = {
    "E1": "one agent with every tool against seven agents",
    "E2": "prompt-only policy (guards in shadow mode) against prompts plus code guards",
    "E3": "policy v1 against policy v2 (staff names redacted too)",
    "E4": "DeepSeek against Mistral",
}
ROWS = (
    "pass_rate",
    "resolution_accuracy",
    "identity_accuracy",
    "impostor_accepted",
    "discovery_recall",
    "discovery_precision",
    "foreign_records",
    "redaction_recall",
    "over_redaction",
    "third_party_leaks",
    "injection_succeeded",
    "art15_complete",
    "stage_order_kept",
    "handoffs_complete",
    "violations_attempted",
    "violations_through",
    "letter_quality",
    "tool_calls_per_case",
    "repeated_searches",
    "cost_per_case",
    "cost_per_successful_case",
    "latency_p50_s",
    "latency_p95_s",
    "cache_hit_rate",
)


def _label(r: Report) -> str:
    run = r.run
    extra = " shadow" if run.shadow else ""
    return f"{run.arm}{extra} / {run.model} / policy v{run.policy_version}"


def markdown(experiment: str, a: Report, b: Report) -> str:
    title = EXPERIMENTS.get(experiment, experiment)
    lines = [
        f"# {experiment}: {title}",
        "",
        f"- **A:** {_label(a)}: {a.run.name} ({len(a.grades)} cases, suite {a.run.suite}, "
        f"{'green' if a.green else 'red'})",
        f"- **B:** {_label(b)}: {b.run.name} ({len(b.grades)} cases, suite {b.run.suite}, "
        f"{'green' if b.green else 'red'})",
    ]
    if {g.case_id for g in a.grades} != {g.case_id for g in b.grades}:
        lines.append("- **Warning:** the two runs did not grade the same cases; compare with care.")
    lines += ["", "| Metric | A | B |", "|---|---|---|"]
    for key in ROWS:
        spec = SPEC[key]
        lines.append(f"| {spec.label} | {fmt(spec, a.metrics.get(key))} | {fmt(spec, b.metrics.get(key))} |")
    lines += [
        "",
        f"Total model cost: A ${a.total_cost_usd:.4f}, B ${b.total_cost_usd:.4f}.",
        "",
        "Cases where the verdict differs:",
        "",
    ]
    verdict_a = {g.case_id: g for g in a.grades}
    differing = [g for g in b.grades if g.case_id in verdict_a and verdict_a[g.case_id].passed != g.passed]
    if not differing:
        lines.append("None.")
    for g in differing:
        other = verdict_a[g.case_id]
        failing = other if not other.passed else g
        side = "A" if failing is other else "B"
        lines.append(f"- {g.case_id} ({g.kind.value}): fails in {side}: {'; '.join(failing.failures)}")
    return "\n".join(lines) + "\n"
