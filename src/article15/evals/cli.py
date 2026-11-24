"""`a15 eval ...`: run a suite and grade it, re-render a report, write the golden-20 sheet."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    from article15.evals.broken import BROKEN_VERSIONS
    from article15.evals.suites import SUITES

    ev = sub.add_parser("eval", help="run and grade an eval suite; reports; the golden-20 sheet")
    evsub = ev.add_subparsers(dest="eval_command", required=True)
    run = evsub.add_parser("run", help="run a suite and grade every case against the ground truth")
    run.add_argument("--suite", choices=SUITES, default="smoke")
    run.add_argument("--cases", nargs="+", help="only these case IDs (instead of the whole suite)")
    run.add_argument(
        "--agent",
        choices=["oracle", "live", "replay"],
        default="oracle",
        help="oracle: a perfect agent built from the truth (free); live: a model; replay: recorded runs",
    )
    run.add_argument("--provider", choices=["deepseek", "mistral"], help="live runs (default: A15_PROVIDER)")
    run.add_argument("--model", help="model ID (default: A15_DEEPSEEK_MODEL / A15_MISTRAL_MODEL)")
    run.add_argument("--record", metavar="DIR", help="live runs: record one cassette per case")
    run.add_argument("--replay", metavar="DIR", help="replay runs: the cassette directory")
    run.add_argument(
        "--continue-from",
        metavar="DIR",
        help="live runs with --record: replay each case's recording while the requests still match it, then "
        "go live (re-records a case after a code change, paying only for the calls that changed)",
    )
    run.add_argument("--out", help="eval directory (default: runs/eval/<time>-<suite>-<agent>)")
    run.add_argument("--jobs", type=int, default=4)
    run.add_argument("--budget", type=float, metavar="USD", help="stop starting cases past this spend")
    run.add_argument("--resume", action="store_true", help="continue an interrupted run in --out")
    run.add_argument("--repeat", type=int, default=1, help="run the suite N times to measure the spread")
    run.add_argument("--judge", action="store_true", help="score each letter with the LLM judge")
    run.add_argument("--baseline", metavar="REPORT", help="compare with this report.json")
    run.add_argument("--broken", choices=sorted(BROKEN_VERSIONS), help="run a deliberately broken desk")
    run.add_argument("--arm", choices=["single", "team"], default="single", help="E1: one agent or seven")
    run.add_argument(
        "--policy",
        metavar="FILE",
        help="E3: the policy file (default: policy.yaml, v1; src/article15/policy/policy-v2.yaml hides staff "
        "names too); the graders follow its R-OTHERS",
    )
    run.add_argument(
        "--shadow", action="store_true", help="E2: guards log what they would block, and let it pass"
    )
    run.add_argument(
        "--fail-on",
        choices=["red", "replay", "never"],
        default="red",
        help="exit 1 when the report is red or regresses (default), only when a replay differs, or never",
    )
    run.add_argument(
        "--rebaseline",
        action="store_true",
        help="replay runs: when the code now stops a case sooner (every call used is a recorded one, in "
        "order), keep only the calls used and write the new expected outcome; a case that needs a call "
        "never recorded is listed for a live re-record",
    )
    run.add_argument("--data", default="data/generated")
    run.set_defaults(fn=_cmd_eval_run)
    rep = evsub.add_parser("report", help="re-render a report, optionally compared with a baseline")
    rep.add_argument("report", help="report.json")
    rep.add_argument("--baseline", metavar="REPORT")
    rep.set_defaults(fn=_cmd_eval_report)
    reg = evsub.add_parser(
        "regrade", help="grade a finished run again with the current graders (no model calls, judge kept)"
    )
    reg.add_argument("report", help="report.json of the run")
    reg.add_argument("--data", default="data/generated")
    reg.set_defaults(fn=_cmd_eval_regrade)
    exp = evsub.add_parser("experiment", help="compare two reports for an experiment (E1-E4)")
    exp.add_argument("experiment", help="E1, E2, E3 or E4")
    exp.add_argument("a", help="report.json of arm A")
    exp.add_argument("b", help="report.json of arm B")
    exp.add_argument("--out", help="write the Markdown here (default: print it)")
    exp.set_defaults(fn=_cmd_eval_experiment)
    har = evsub.add_parser(
        "harness", help="summarise what the guard layer did on live runs (the web app's first page)"
    )
    har.add_argument("--live", nargs="+", required=True, metavar="RUN", help="live eval run directories")
    har.add_argument("--shadow", metavar="RUN", help="E2: the shadow-mode run")
    har.add_argument("--guarded", metavar="RUN", help="E2: the guarded run on the same cases")
    har.add_argument(
        "--rerun", nargs="*", default=[], metavar="RUN", help="guarded re-runs that replace their cases"
    )
    har.add_argument(
        "--shadow-cassettes", metavar="DIR", help="E2: the shadow run's recordings, re-baselined after a fix"
    )
    har.add_argument("--out", default="evals/reports/harness.json")
    har.add_argument("--static", default="frontend/public/harness.json", help="the web app's static copy")
    har.set_defaults(fn=_cmd_eval_harness)
    gold = evsub.add_parser("golden", help="write the golden-20 review sheet and ratings file")
    gold.add_argument("--data", default="data/generated")
    gold.add_argument("--out", default="evals/golden")
    gold.set_defaults(fn=_cmd_eval_golden)


def _cmd_eval_run(args: argparse.Namespace) -> int:
    import logging

    import anyio

    from article15.company.store import DataPaths, load_people, load_truths
    from article15.core.agent import Caps
    from article15.core.live import live_provider
    from article15.core.model import Thinking
    from article15.core.provider import Provider
    from article15.core.replay import ContinuingProvider, RecordingProvider, ReplayProvider
    from article15.desk.runner import Desk, RunConfig
    from article15.evals import report as reports
    from article15.evals import suites
    from article15.evals.judge import judge_letter
    from article15.evals.runner import EvalConfig, EvalError, JudgeFn, oracle_provider_for, run_eval
    from article15.settings import ModelSettings, SettingsError, load_dotenv

    logging.getLogger("mcp").setLevel(logging.CRITICAL)
    load_dotenv()
    data = DataPaths(Path(args.data))
    if not data.cases.exists():
        print(f"error: no generated data in {data.root}; run `a15 generate` first", file=sys.stderr)
        return 2
    case_ids = sorted(args.cases) if args.cases else suites.select(args.suite, load_truths(data))
    replay_dir = Path(args.replay) if args.replay else None
    record_dir = Path(args.record) if args.record else None
    if args.agent == "replay" and replay_dir is None:
        print("error: --agent replay needs --replay DIR", file=sys.stderr)
        return 2
    if args.agent != "replay" and replay_dir is not None:
        print("error: --replay needs --agent replay", file=sys.stderr)
        return 2
    if record_dir is not None and args.agent != "live":
        print("error: --record needs --agent live", file=sys.stderr)
        return 2
    continue_dir = Path(args.continue_from) if args.continue_from else None
    if continue_dir is not None and record_dir is None:
        print("error: --continue-from needs --record DIR", file=sys.stderr)
        return 2

    expected: dict[str, str] = {}
    recorded_elapsed: dict[str, float] = {}
    recorded: set[tuple[str, str, object]] = set()
    if replay_dir is not None:
        for case_id in case_ids:
            path = replay_dir / f"{case_id}.outcome.json"
            if not path.exists() or not (replay_dir / f"{case_id}.jsonl").exists():
                print(f"error: no recording for {case_id} in {replay_dir}", file=sys.stderr)
                return 2
            body = json.loads(path.read_text(encoding="utf-8"))
            expected[case_id] = body["fingerprint"]
            if body.get("elapsed_s") is not None:
                recorded_elapsed[case_id] = float(body["elapsed_s"])
            recorded.add((str(body["model"]), str(body["thinking"]), body["max_output_tokens"]))
        if len(recorded) > 1:
            print(f"error: the recordings use different model settings: {sorted(map(str, recorded))}")
            return 2

    model, thinking, max_output = next(iter(recorded), (None, None, None))
    settings = None
    if args.agent != "oracle":
        try:
            settings = ModelSettings.from_env(
                provider="replay" if args.agent == "replay" else args.provider, model=args.model or model
            )
        except SettingsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    policy_path = Path(args.policy) if args.policy else None
    desk = Desk.load(load_people(data), policy_path)

    def live() -> Provider:
        assert settings is not None
        # No fallback in evals: a run that switched model halfway would mix two models in one result (P24).
        return live_provider(settings, fallback=False)

    def cassette_dir(base: Path, n: int) -> Path:
        return base if args.repeat == 1 else base / f"run-{n}"

    if args.rebaseline and args.agent != "replay":
        print("error: --rebaseline needs --agent replay", file=sys.stderr)
        return 2
    replayers: dict[tuple[str, int], ReplayProvider] = {}

    def provider_for(case_id: str, n: int) -> Provider:
        if args.agent == "replay":
            assert replay_dir is not None
            replayer = ReplayProvider(cassette_dir(replay_dir, n) / f"{case_id}.jsonl")
            replayers[(case_id, n)] = replayer
            return replayer
        if record_dir is not None:
            assert settings is not None
            path = cassette_dir(record_dir, n) / f"{case_id}.jsonl"
            inner: Provider = live()
            if continue_dir is not None and (cassette_dir(continue_dir, n) / f"{case_id}.jsonl").exists():
                inner = ContinuingProvider(cassette_dir(continue_dir, n) / f"{case_id}.jsonl", inner)
            return RecordingProvider(inner, path, label=f"{case_id} {settings.model}")
        return live()

    judge: JudgeFn | None = None
    if args.judge:
        if args.agent == "oracle":
            print("error: --judge needs a model (--agent live or replay)", file=sys.stderr)
            return 2

        async def judge_fn(
            case_id: str, case_dir: Path, n: int
        ) -> tuple[dict[str, float] | None, str, float]:
            assert settings is not None
            if args.agent == "replay":
                assert replay_dir is not None
                provider: Provider = ReplayProvider(cassette_dir(replay_dir, n) / f"{case_id}.judge.jsonl")
            elif record_dir is not None:
                path = cassette_dir(record_dir, n) / f"{case_id}.judge.jsonl"
                provider = RecordingProvider(live(), path, label=f"{case_id} judge {settings.model}")
            else:
                provider = live()
            return await judge_letter(case_dir, desk.register, provider, settings.model)

        judge = judge_fn

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    agent_name = "oracle" if settings is None else f"{args.agent}-{settings.provider}"
    name = f"{stamp}-{args.suite}-{args.arm}-{agent_name}" + (f"-broken-{args.broken}" if args.broken else "")
    name += "-shadow" if args.shadow else ""
    out = Path(args.out) if args.out else Path("runs") / "eval" / name
    model_id = settings.model if settings else "oracle"
    caps = (
        Caps(
            max_calls=settings.max_calls,
            max_input_tokens=settings.max_input_tokens,
            max_seconds=settings.max_seconds,
            max_cost_usd=settings.max_cost_usd,
        )
        if settings
        else Caps()
    )
    run_cfg = RunConfig(
        data=data,
        out_dir=out,
        model=model_id,
        thinking=Thinking(thinking or (settings.thinking if settings else "off")),
        caps=caps,
        max_output_tokens=int(str(max_output))
        if replay_dir and max_output is not None
        else (settings.max_output_tokens if settings else 8192),
        arm=args.arm,
        shadow=args.shadow,
        policy_path=policy_path,
    )
    cfg = EvalConfig(
        name=name if not args.out else out.name,
        suite=args.suite if not args.cases else "cases",
        case_ids=case_ids,
        run=run_cfg,
        agent=agent_name,
        jobs=args.jobs,
        budget_usd=args.budget,
        resume=args.resume,
        repeat=args.repeat,
        broken=args.broken,
        expected_fingerprints=expected,
        recorded_elapsed_s=recorded_elapsed,
    )
    chosen = oracle_provider_for(data, desk, args.arm) if args.agent == "oracle" else provider_for
    print(f"eval {cfg.suite}: {len(case_ids)} case(s), agent {agent_name} ({model_id}) -> {out}")
    try:
        result = anyio.run(lambda: run_eval(cfg, chosen, judge))
    except EvalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = result.report
    if args.baseline:
        report = report.model_copy(
            update={"comparison": reports.compare(report, reports.load(Path(args.baseline)))}
        )
    js, md = reports.write(report, out)
    if record_dir is not None:
        for n, outcomes in result.outcomes.items():
            for o in outcomes.values():
                body = {**o.model_dump(mode="json"), "fingerprint": o.fingerprint()}
                (cassette_dir(record_dir, n) / f"{o.case_id}.outcome.json").write_text(
                    json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
                )
    print(md.read_text(encoding="utf-8").split("\n## Reported")[0])
    print(f"report: {md} ({js.name})")
    if args.rebaseline and result.replay_mismatches:
        assert replay_dir is not None
        needs_live = _rebaseline(result.outcomes, replayers, expected, lambda n: cassette_dir(replay_dir, n))
        if needs_live:
            print(f"NEEDS A LIVE RE-RECORD: {', '.join(needs_live)}", file=sys.stderr)
            return 0 if args.fail_on == "never" else 1
        return 0
    if result.replay_mismatches:
        print(f"REPLAY DIFFERS: {', '.join(result.replay_mismatches)}", file=sys.stderr)
        return 0 if args.fail_on == "never" else 1
    regressions = [r["metric"] for r in report.comparison or [] if r["regression"]]
    if regressions:
        print(f"regressions against the baseline: {', '.join(regressions)}", file=sys.stderr)
    if args.fail_on != "red":
        return 0
    return 0 if report.green and not regressions else 1


def _rebaseline(
    outcomes: dict[int, dict[str, Any]],
    replayers: dict[tuple[str, int], Any],
    expected: dict[str, str],
    cassettes: Callable[[int], Path],
) -> list[str]:
    """After a code change, bring recordings up to date without a model call where that is honest: when the
    code now stops a case sooner, every call the replay used is a recorded one, in the recorded order, so the
    calls past that point are dropped and the new outcome becomes the expected one. A case that needed a call
    that was never recorded is returned, for a live re-record."""
    from article15.core.replay import trim_cassette

    needs_live: list[str] = []
    for n, by_case in sorted(outcomes.items()):
        for case_id, outcome in sorted(by_case.items()):
            if expected.get(case_id) == outcome.fingerprint():
                continue
            replayer = replayers[(case_id, n)]
            keep = replayer.used_prefix()
            if outcome.error is not None or keep is None:
                needs_live.append(case_id)
                print(f"{case_id}: needs a live re-record ({outcome.error or 'calls used out of order'})")
                continue
            folder = cassettes(n)
            dropped = trim_cassette(folder / f"{case_id}.jsonl", keep) if replayer.unused() else 0
            old = json.loads((folder / f"{case_id}.outcome.json").read_text(encoding="utf-8"))
            body = {**outcome.model_dump(mode="json"), "fingerprint": outcome.fingerprint()}
            # wall time is not in the fingerprint: keep the live run's (an upper bound for the shorter run)
            body["elapsed_s"] = old.get("elapsed_s", body["elapsed_s"])
            (folder / f"{case_id}.outcome.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
            print(
                f"{case_id}: re-baselined, {old['final_state']} -> {outcome.final_state}"
                f" ({dropped} recorded call(s) no longer reached; guards would block "
                f"{len(old.get('guard_would_block', []))} -> {len(outcome.guard_would_block)})"
            )
    return needs_live


def _cmd_eval_report(args: argparse.Namespace) -> int:
    from article15.evals import report as reports

    path = Path(args.report)
    report = reports.load(path)
    if args.baseline:
        report = report.model_copy(
            update={"comparison": reports.compare(report, reports.load(Path(args.baseline)))}
        )
    _, md = reports.write(report, path.parent)
    print(md.read_text(encoding="utf-8"))
    return 0 if report.green else 1


def _cmd_eval_harness(args: argparse.Namespace) -> int:
    from article15.evals.harness_summary import build

    summary = build(
        [Path(r) for r in args.live],
        Path(args.shadow) if args.shadow else None,
        Path(args.guarded) if args.guarded else None,
        [Path(r) for r in args.rerun],
        Path(args.shadow_cassettes) if args.shadow_cassettes else None,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(summary, ensure_ascii=False, indent=1) + "\n"
    out.write_text(body, encoding="utf-8", newline="\n")
    # The same numbers as a static file of the web app, so a static deploy (the frontend alone, before the
    # backend is linked) still shows the first page from recorded runs.
    static = Path(args.static)
    if static.parent.exists():
        static.write_text(body, encoding="utf-8", newline="\n")
    c = summary["catches"]
    print(f"{c['model_refusals']} refusals in {c['cases_with_a_refusal']} of {c['cases']} cases -> {out}")
    return 0


def _cmd_eval_regrade(args: argparse.Namespace) -> int:
    from article15.company.store import DataPaths
    from article15.evals import report as reports
    from article15.evals.graders import GradingContext, grade_case

    path = Path(args.report)
    old = reports.load(path)
    grading = GradingContext.load(DataPaths(Path(args.data)))
    runs = sorted(path.parent.glob("run-*"), key=lambda d: int(d.name.split("-")[1]))
    repeats = []
    for run_dir in runs:
        grades = []
        for case_id in old.cases_planned:
            case_dir = run_dir / case_id
            done = case_dir / "grade.json"
            if not done.exists():
                continue
            before = json.loads(done.read_text(encoding="utf-8"))
            kept = {
                "letter_scores": before.get("letter_scores"),
                "letter_notes": before.get("letter_notes", ""),
                "judge_cost_usd": before.get("judge_cost_usd", 0.0),
            }
            g = grade_case(case_dir, grading).model_copy(update=kept)
            done.write_text(g.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
            grades.append(g)
        repeats.append(grades)
    quality = old.metrics.get("letter_quality") is not None
    many = repeats if len(repeats) > 1 else None
    report = reports.build(old.run, old.cases_planned, repeats[0], many, require_quality=quality)
    _, md = reports.write(report, path.parent)
    print(md.read_text(encoding="utf-8").split("\n## Reported")[0])
    print(f"report: {md}")
    return 0 if report.green else 1


def _cmd_eval_golden(args: argparse.Namespace) -> int:
    from article15.company.store import DataPaths
    from article15.evals.golden import write_golden

    sheet, ratings = write_golden(DataPaths(Path(args.data)), Path(args.out))
    print(f"wrote {sheet} and {ratings}")
    return 0


def _cmd_eval_experiment(args: argparse.Namespace) -> int:
    from article15.evals import report as reports
    from article15.evals.experiments import markdown

    text = markdown(args.experiment, reports.load(Path(args.a)), reports.load(Path(args.b)))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {out}")
    else:
        print(text)
    return 0
