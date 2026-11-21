"""`a15 eval ...`: run a suite and grade it, re-render a report, write the golden-20 sheet."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


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
    run.add_argument("--out", help="eval directory (default: runs/eval/<time>-<suite>-<agent>)")
    run.add_argument("--jobs", type=int, default=4)
    run.add_argument("--budget", type=float, metavar="USD", help="stop starting cases past this spend")
    run.add_argument("--resume", action="store_true", help="continue an interrupted run in --out")
    run.add_argument("--repeat", type=int, default=1, help="run the suite N times to measure the spread")
    run.add_argument("--judge", action="store_true", help="score each letter with the LLM judge")
    run.add_argument("--baseline", metavar="REPORT", help="compare with this report.json")
    run.add_argument("--broken", choices=sorted(BROKEN_VERSIONS), help="run a deliberately broken desk")
    run.add_argument(
        "--fail-on",
        choices=["red", "replay", "never"],
        default="red",
        help="exit 1 when the report is red or regresses (default), only when a replay differs, or never",
    )
    run.add_argument("--data", default="data/generated")
    run.set_defaults(fn=_cmd_eval_run)
    rep = evsub.add_parser("report", help="re-render a report, optionally compared with a baseline")
    rep.add_argument("report", help="report.json")
    rep.add_argument("--baseline", metavar="REPORT")
    rep.set_defaults(fn=_cmd_eval_report)
    gold = evsub.add_parser("golden", help="write the golden-20 review sheet and ratings file")
    gold.add_argument("--data", default="data/generated")
    gold.add_argument("--out", default="evals/golden")
    gold.set_defaults(fn=_cmd_eval_golden)


def _cmd_eval_run(args: argparse.Namespace) -> int:
    import logging

    import anyio

    from article15.company.store import DataPaths, load_people, load_truths
    from article15.core.agent import Caps
    from article15.core.model import Thinking
    from article15.core.openai_compat import OpenAICompatibleProvider, ProviderConfig
    from article15.core.provider import Provider
    from article15.core.replay import RecordingProvider, ReplayProvider
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

    expected: dict[str, str] = {}
    recorded: set[tuple[str, str, object]] = set()
    if replay_dir is not None:
        for case_id in case_ids:
            path = replay_dir / f"{case_id}.outcome.json"
            if not path.exists() or not (replay_dir / f"{case_id}.jsonl").exists():
                print(f"error: no recording for {case_id} in {replay_dir}", file=sys.stderr)
                return 2
            body = json.loads(path.read_text(encoding="utf-8"))
            expected[case_id] = body["fingerprint"]
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
    desk = Desk.load(load_people(data))

    def live() -> Provider:
        assert settings is not None
        return OpenAICompatibleProvider(
            ProviderConfig(
                name=settings.provider,
                base_url=settings.base_url,
                api_key=settings.api_key,
                timeout_s=settings.timeout_s,
            )
        )

    def cassette_dir(base: Path, n: int) -> Path:
        return base if args.repeat == 1 else base / f"run-{n}"

    def provider_for(case_id: str, n: int) -> Provider:
        if args.agent == "replay":
            assert replay_dir is not None
            return ReplayProvider(cassette_dir(replay_dir, n) / f"{case_id}.jsonl")
        if record_dir is not None:
            assert settings is not None
            path = cassette_dir(record_dir, n) / f"{case_id}.jsonl"
            return RecordingProvider(live(), path, label=f"{case_id} {settings.model}")
        return live()

    judge: JudgeFn | None = None
    if args.judge:
        if args.agent == "oracle":
            print("error: --judge needs a model (--agent live or replay)", file=sys.stderr)
            return 2

        async def judge_fn(case_id: str, case_dir: Path, n: int) -> tuple[dict[str, float] | None, float]:
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
    name = f"{stamp}-{args.suite}-{agent_name}" + (f"-broken-{args.broken}" if args.broken else "")
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
    )
    chosen = oracle_provider_for(data, desk) if args.agent == "oracle" else provider_for
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
    if result.replay_mismatches:
        print(f"REPLAY DIFFERS: {', '.join(result.replay_mismatches)}", file=sys.stderr)
        return 0 if args.fail_on == "never" else 1
    regressions = [r["metric"] for r in report.comparison or [] if r["regression"]]
    if regressions:
        print(f"regressions against the baseline: {', '.join(regressions)}", file=sys.stderr)
    if args.fail_on != "red":
        return 0
    return 0 if report.green and not regressions else 1


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


def _cmd_eval_golden(args: argparse.Namespace) -> int:
    from article15.company.store import DataPaths
    from article15.evals.golden import write_golden

    sheet, ratings = write_golden(DataPaths(Path(args.data)), Path(args.out))
    print(f"wrote {sheet} and {ratings}")
    return 0
