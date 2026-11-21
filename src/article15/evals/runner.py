"""The eval runner: parallel, resumable and budget-capped (PLAN.md 8.7, M3).

- **Parallel:** `jobs` cases at a time, each on its own desk (decision 0010), so they cannot interfere.
- **Resumable:** every finished case leaves `grade.json` in its run directory. `resume=True` skips those
  cases, provided the run's settings (suite, agent, model, policy, prompt) are the ones it was started with.
- **Budget-capped:** a case may cost at most its per-case cap, so the runner starts a case only when the money
  already spent, plus the cap of every case still running, plus this case's cap, fits in the budget. The run
  can therefore never exceed its budget. Cases it could not afford are listed as not run.
- **Agents:** `oracle` (a perfect agent built from the truth: free, checks the graders and the desk),
  `live` (DeepSeek or Mistral, optionally recording) or `replay` (recorded cassettes, free, must reproduce the
  recorded fingerprints exactly).

Layout of an eval directory:

    eval.json            the run's settings, for resuming
    report.json/.md      the report
    run-1/<case>/...     the case runs (see desk/runner.py) plus grade.json
    run-2/...            further runs when repeat > 1
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from article15.company.models import CaseTruth, RequestCase
from article15.company.store import DataPaths, load_cases, load_people
from article15.core.provider import Provider
from article15.desk import prompts
from article15.desk.runner import CaseOutcome, Desk, RunConfig, run_case
from article15.desk.team import prompts_hash as team_prompts_hash
from article15.evals import broken as broken_versions
from article15.evals.graders import CaseGrade, GradingContext, grade_case
from article15.evals.judge import JUDGE_CAPS
from article15.evals.oracle import ScriptedProvider, oracle, team_agents, team_script
from article15.evals.report import Report, RunInfo, build

ProviderFor = Callable[[str, int], Provider]
"""(case ID, run number) -> the provider for that case."""
JudgeFn = Callable[[str, Path, int], Awaitable[tuple[dict[str, float] | None, float]]]
"""(case ID, case run directory, run number) -> (letter scores or None, the judge's cost in USD)."""


class EvalError(Exception):
    pass


@dataclass
class EvalConfig:
    name: str
    suite: str
    case_ids: list[str]
    run: RunConfig  # out_dir is the eval directory; cases go to run-<n>/
    agent: str  # "oracle", or the provider's name for live and replay runs
    jobs: int = 1
    budget_usd: float | None = None
    resume: bool = False
    repeat: int = 1
    broken: str | None = None
    expected_fingerprints: dict[str, str] = field(
        default_factory=dict
    )  # replay: what each case must reproduce

    def key(self, policy_version: str, prompt_hash: str) -> dict[str, Any]:
        """The settings a resumed run must share with the run it continues."""
        return {
            "suite": self.suite,
            "cases": self.case_ids,
            "agent": self.agent,
            "model": self.run.model,
            "thinking": self.run.thinking.value,
            "max_output_tokens": self.run.max_output_tokens,
            "policy_version": policy_version,
            "prompt_hash": prompt_hash,
            "repeat": self.repeat,
            "broken": self.broken,
            "arm": self.run.arm,
            "shadow": self.run.shadow,
        }


@dataclass
class EvalResult:
    report: Report
    outcomes: dict[int, dict[str, CaseOutcome]]
    replay_mismatches: list[str]


def oracle_provider_for(data: DataPaths, desk: Desk, arm: str = "single") -> ProviderFor:
    requests = {c.case_id: c for c in load_cases(data)}
    ctx_truths = GradingContext.load(data).truths
    names = {p.person_id: p.name for p in load_people(data)}

    def provider_for(case_id: str, run: int) -> Provider:
        truth = ctx_truths[case_id]
        script = oracle(truth, requests[case_id], desk.register, names[truth.requester.person_id])
        if arm == "team":
            return ScriptedProvider(team_script(script), agents=team_agents(desk.policy, desk.register))
        return ScriptedProvider(script)

    return provider_for


async def run_eval(
    cfg: EvalConfig,
    provider_for: ProviderFor,
    judge: JudgeFn | None = None,
    grading: GradingContext | None = None,
) -> EvalResult:
    data = cfg.run.data
    root = cfg.run.out_dir
    requests: dict[str, RequestCase] = {c.case_id: c for c in load_cases(data)}
    grading = grading or GradingContext.load(data)
    truths: dict[str, CaseTruth] = grading.truths
    unknown = [c for c in cfg.case_ids if c not in requests]
    if unknown:
        raise EvalError(f"unknown case(s): {', '.join(unknown)}")
    desk = Desk.load(load_people(data))
    prompt_hash = (
        team_prompts_hash(desk.policy, desk.register)
        if cfg.run.arm == "team"
        else prompts.prompt_hash(prompts.system_prompt(desk.policy, desk.register))
    )
    key = cfg.key(desk.policy.version, prompt_hash)
    manifest = root / "eval.json"
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        if not cfg.resume:
            raise EvalError(
                f"{root} already holds an eval run; pass resume to continue it, or use another out dir"
            )
        if previous["key"] != key:
            changed = sorted(k for k in key if previous["key"].get(k) != key[k])
            raise EvalError(f"cannot resume {root}: settings changed ({', '.join(changed)})")
        started_at = previous["started_at"]
    else:
        root.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        manifest.write_text(
            json.dumps({"name": cfg.name, "started_at": started_at, "key": key}, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    cap = cfg.run.caps.max_cost_usd + (JUDGE_CAPS.max_cost_usd if judge is not None else 0.0)
    spent = 0.0
    reserved = 0.0
    grades: dict[int, dict[str, CaseGrade]] = {}
    outcomes: dict[int, dict[str, CaseOutcome]] = {}
    not_run: set[str] = set()
    mismatches: list[str] = []
    slots = anyio.Semaphore(max(1, cfg.jobs))

    with ExitStack() as stack:
        if cfg.broken:
            stack.enter_context(broken_versions.applied(cfg.broken, data))
        for n in range(1, cfg.repeat + 1):
            run_root = root / f"run-{n}"
            run_cfg = replace(cfg.run, out_dir=run_root)
            grades[n], outcomes[n] = {}, {}
            pending = []
            for case_id in cfg.case_ids:
                done = run_root / case_id / "grade.json"
                if cfg.resume and done.exists():
                    g = CaseGrade.model_validate_json(done.read_text(encoding="utf-8"))
                    grades[n][case_id] = g
                    spent += g.cost_usd + g.judge_cost_usd
                else:
                    pending.append(case_id)

            async def one(case_id: str, n: int = n, run_cfg: RunConfig = run_cfg) -> None:
                nonlocal spent, reserved
                try:
                    outcome = await run_case(
                        requests[case_id], truths.get(case_id), desk, run_cfg, provider_for(case_id, n)
                    )
                    outcomes[n][case_id] = outcome
                    expected = cfg.expected_fingerprints.get(case_id)
                    if expected is not None and expected != outcome.fingerprint():
                        mismatches.append(f"run {n} {case_id}")
                    case_dir = run_cfg.out_dir / case_id
                    g = grade_case(case_dir, grading)
                    if judge is not None and g.letter:
                        scores, judge_cost = await judge(case_id, case_dir, n)
                        g = g.model_copy(update={"letter_scores": scores, "judge_cost_usd": judge_cost})
                    (case_dir / "grade.json").write_text(
                        g.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
                    )
                    grades[n][case_id] = g
                    spent += g.cost_usd + g.judge_cost_usd
                finally:
                    reserved -= cap
                    slots.release()

            async with anyio.create_task_group() as tg:
                for case_id in pending:
                    await slots.acquire()
                    while cfg.budget_usd is not None and spent + reserved + cap > cfg.budget_usd + 1e-9:
                        if reserved <= 0:
                            break
                        await anyio.sleep(0.05)  # wait for a running case to finish and free its reserve
                    if cfg.budget_usd is not None and spent + reserved + cap > cfg.budget_usd + 1e-9:
                        slots.release()
                        not_run.add(case_id)
                        continue
                    reserved += cap
                    tg.start_soon(one, case_id)

    info = RunInfo(
        name=cfg.name,
        suite=cfg.suite,
        provider=cfg.agent,
        model=cfg.run.model,
        thinking=cfg.run.thinking.value,
        policy_version=desk.policy.version,
        prompt_hash=prompt_hash,
        started_at=started_at,
        repeat=cfg.repeat,
        budget_usd=cfg.budget_usd,
        broken=cfg.broken,
        arm=cfg.run.arm,
        shadow=cfg.run.shadow,
    )
    first = [grades[1][c] for c in cfg.case_ids if c in grades[1]]
    repeats = [[grades[n][c] for c in cfg.case_ids if c in grades[n]] for n in sorted(grades)]
    report = build(
        info, cfg.case_ids, first, repeats if cfg.repeat > 1 else None, require_quality=judge is not None
    )
    if not_run:
        report = report.model_copy(update={"cases_not_run": sorted(not_run | set(report.cases_not_run))})
    return EvalResult(report=report, outcomes=outcomes, replay_mismatches=sorted(mismatches))
