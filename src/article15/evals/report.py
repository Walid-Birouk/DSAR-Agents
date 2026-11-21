"""Eval reports: JSON for machines, Markdown for people, with a comparison to a baseline (PLAN.md 8.7).

The rule for changes: a change to prompts, policy or models comes with a live smoke run and its comparison
with the last baseline. `compare` lists every metric that moved and flags regressions: a gate that was 0 and
is not, a bar that was met and is not, or a measured metric getting worse by more than its tolerance.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from article15.evals.graders import CaseGrade
from article15.evals.metrics import SPEC, SPECS, Kind, Spec, checks, compute, green, spread

REPORT_VERSION = 1
TOLERANCE = 0.005  # a rate moving less than half a point is noise, not a regression


class RunInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    suite: str
    provider: str
    model: str
    thinking: str
    policy_version: str
    prompt_hash: str
    started_at: str
    repeat: int = 1
    budget_usd: float | None = None
    broken: str | None = None


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_version: int = REPORT_VERSION
    run: RunInfo
    cases_planned: list[str]
    cases_not_run: list[str]
    metrics: dict[str, float | None]
    green: bool
    spread: dict[str, float | None] = {}
    by_kind: dict[str, dict[str, float]]
    total_cost_usd: float
    grades: list[CaseGrade]
    comparison: list[dict[str, Any]] | None = None


def build(
    info: RunInfo,
    planned: list[str],
    grades: list[CaseGrade],
    repeats: list[list[CaseGrade]] | None = None,
    require_quality: bool = False,
) -> Report:
    metrics = compute(grades)
    kinds: dict[str, list[CaseGrade]] = defaultdict(list)
    for g in grades:
        kinds[g.kind.value].append(g)
    by_kind = {
        k: {"cases": float(len(v)), "passed": float(sum(x.passed for x in v))}
        for k, v in sorted(kinds.items())
    }
    done = {g.case_id for g in grades}
    return Report(
        run=info,
        cases_planned=planned,
        cases_not_run=[c for c in planned if c not in done],
        metrics=metrics,
        green=green(metrics, require_quality) and not [c for c in planned if c not in done],
        spread=spread(repeats or []),
        by_kind=by_kind,
        total_cost_usd=round(sum(g.cost_usd for g in grades), 6),
        grades=grades,
    )


def compare(current: Report, baseline: Report) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in SPECS:
        now, before = current.metrics.get(spec.key), baseline.metrics.get(spec.key)
        if now is None and before is None:
            continue
        regression = False
        if now is not None and before is not None:
            if spec.kind is Kind.GATE:
                regression = before == 0 and now > 0
            elif spec.kind is Kind.BAR:
                regression = spec.ok(before) is True and spec.ok(now) is False
                worse = (before - now) if spec.higher_is_better else (now - before)
                regression = regression or (spec.percent and worse > TOLERANCE)
        elif now is None:
            regression = spec.kind is not Kind.REPORT  # it was measured, now it is not
        rows.append({"metric": spec.key, "baseline": before, "current": now, "regression": regression})
    return rows


def fmt(spec: Spec, value: float | None) -> str:
    if value is None:
        return "not measured"
    if spec.kind is Kind.GATE:
        return f"{value:g}"
    if spec.percent:
        return f"{value:.1%}"
    if "usd" in spec.label.lower() or "cost" in spec.key:
        return f"${value:.4f}"
    return f"{value:.2f}"


def markdown(report: Report) -> str:
    r = report.run
    lines = [
        f"# Eval report: {r.name}",
        "",
        f"- **Verdict:** {'GREEN' if report.green else 'RED'}",
        f"- **Suite:** {r.suite} ({len(report.grades)} of {len(report.cases_planned)} cases run"
        + (f"; not run: {', '.join(report.cases_not_run)}" if report.cases_not_run else "")
        + ")",
        f"- **Agent and model:** {r.provider} / {r.model} (thinking {r.thinking})",
        f"- **Policy:** v{r.policy_version}; prompt hash {r.prompt_hash[:12]}",
        f"- **Started:** {r.started_at}; total model cost ${report.total_cost_usd:.4f}"
        + (f" (budget ${r.budget_usd:.2f})" if r.budget_usd is not None else ""),
    ]
    if r.broken:
        lines.append(f"- **Deliberately broken version:** `{r.broken}` (this run is expected to be red)")
    lines += [
        "",
        "## Hard gates and bars",
        "",
        "| Area | Metric | Value | Bar | Result |",
        "|---|---|---|---|---|",
    ]
    for c in checks(report.metrics):
        if c.spec.kind is Kind.REPORT:
            continue
        bar = (
            "0 (hard gate)"
            if c.spec.kind is Kind.GATE
            else (">= " if c.spec.higher_is_better else "<= ")
            + (f"{c.spec.threshold:.0%}" if c.spec.percent else f"{c.spec.threshold:g}")
        )
        result = {True: "pass", False: "**FAIL**", None: "-"}[c.ok]
        lines.append(f"| {c.spec.area} | {c.spec.label} | {fmt(c.spec, c.value)} | {bar} | {result} |")
    lines += ["", "## Reported", "", "| Area | Metric | Value |", "|---|---|---|"]
    for c in checks(report.metrics):
        if c.spec.kind is Kind.REPORT:
            lines.append(f"| {c.spec.area} | {c.spec.label} | {fmt(c.spec, c.value)} |")
    if report.spread:
        lines += ["", f"## Spread over {r.repeat} runs", ""]
        lines += [f"- {k}: {'-' if v is None else f'{v:.4g}'}" for k, v in report.spread.items()]
    lines += ["", "## By kind of case", "", "| Kind | Cases | Fully correct |", "|---|---|---|"]
    for kind, row in report.by_kind.items():
        lines.append(f"| {kind} | {row['cases']:g} | {row['passed']:g} |")
    failing = [g for g in report.grades if not g.passed]
    lines += ["", "## Cases not fully correct", ""]
    if not failing:
        lines.append("None.")
    for g in failing:
        lines.append(
            f"- **{g.case_id}** ({g.kind.value}, ended {g.final_state.value}): " + "; ".join(g.failures)
        )
    if report.comparison is not None:
        lines += [
            "",
            "## Compared with the baseline",
            "",
            "| Metric | Baseline | Now | |",
            "|---|---|---|---|",
        ]
        for diff in report.comparison:
            spec = SPEC[diff["metric"]]
            if diff["baseline"] == diff["current"]:
                continue
            flag = "**regression**" if diff["regression"] else ""
            lines.append(
                f"| {spec.label} | {fmt(spec, diff['baseline'])} | {fmt(spec, diff['current'])} | {flag} |"
            )
    return "\n".join(lines) + "\n"


def write(report: Report, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    js, md = out_dir / "report.json", out_dir / "report.md"
    js.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md.write_text(markdown(report), encoding="utf-8", newline="\n")
    return js, md


def load(path: Path) -> Report:
    return Report.model_validate_json(path.read_text(encoding="utf-8"))
