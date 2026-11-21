"""Command line: `a15 <command>`."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from article15.case.deadlines import deadlines_for
from article15.policy.lint import Level, lint
from article15.policy.models import load_policy
from article15.policy.render import render_policy_section
from article15.schemas import AgentName


def _cmd_lint(_: argparse.Namespace) -> int:
    policy = load_policy()
    issues = lint(policy)
    for issue in issues:
        print(issue)
    errors = sum(i.level is Level.ERROR for i in issues)
    print(
        f"policy {policy.policy_id} v{policy.version}: {len(policy.rules)} rules, {errors} errors, "
        f"{len(issues) - errors} warnings"
    )
    return 1 if errors else 0


def _cmd_render(args: argparse.Namespace) -> int:
    print(render_policy_section(load_policy(), AgentName(args.agent)))
    return 0


def _cmd_deadline(args: argparse.Namespace) -> int:
    d = deadlines_for(date.fromisoformat(args.received))
    print(
        f"received {d.received}  due {d.due}  extended due {d.extended_due}  extension notice by "
        f"{d.extension_notice_by}"
    )
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    from article15.company.generator import generate, world_digest
    from article15.company.store import write_world

    world = generate(seed=args.seed)
    paths = write_world(world, Path(args.out))
    print(
        f"seed {world.seed}: {len(world.customers)} customers, {len(world.orders)} orders, "
        f"{len(world.tickets)} tickets, {len(world.events)} web events, {len(world.cases)} cases -> "
        f"{paths.root}"
    )
    print(f"digest {world_digest(world)}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from article15.mcp_servers.registry import build_from_settings
    from article15.settings import Settings, SettingsError

    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    build_from_settings(args.system, settings).run("stdio")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    import json
    import logging
    from datetime import UTC, datetime

    import anyio

    from article15.company.store import DataPaths
    from article15.core.agent import Caps
    from article15.core.model import Thinking
    from article15.core.openai_compat import OpenAICompatibleProvider, ProviderConfig
    from article15.core.provider import Provider
    from article15.core.replay import RecordingProvider, ReplayProvider
    from article15.desk.runner import M2_CASES, RunConfig, run_many
    from article15.settings import ModelSettings, SettingsError, load_dotenv

    logging.getLogger("mcp").setLevel(logging.CRITICAL)  # tool errors already go back to the model
    load_dotenv()
    case_ids = list(args.cases) or list(M2_CASES)
    replay_dir = Path(args.replay) if args.replay else None
    record_dir = Path(args.record) if args.record else None
    if replay_dir and record_dir:
        print("error: --record and --replay cannot be combined", file=sys.stderr)
        return 2
    expected: dict[str, dict[str, object]] = {}
    if replay_dir:
        for case_id in case_ids:
            path = replay_dir / f"{case_id}.outcome.json"
            if not path.exists() or not (replay_dir / f"{case_id}.jsonl").exists():
                print(f"error: no recording for {case_id} in {replay_dir}", file=sys.stderr)
                return 2
            expected[case_id] = json.loads(path.read_text(encoding="utf-8"))
    # A replay must send exactly what was recorded, so it uses the recorded model settings.
    recorded = {(str(e["model"]), str(e["thinking"]), e["max_output_tokens"]) for e in expected.values()}
    if len(recorded) > 1:
        print(f"error: the recordings use different model settings: {sorted(map(str, recorded))}")
        return 2
    model, thinking, max_output = next(iter(recorded), (None, None, None))
    try:
        settings = ModelSettings.from_env(
            provider="replay" if replay_dir else args.provider, model=args.model or model
        )
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def provider_for(case_id: str) -> Provider:
        if replay_dir:
            return ReplayProvider(replay_dir / f"{case_id}.jsonl")
        live = OpenAICompatibleProvider(
            ProviderConfig(
                name=settings.provider,
                base_url=settings.base_url,
                api_key=settings.api_key,
                timeout_s=settings.timeout_s,
            )
        )
        if record_dir:
            return RecordingProvider(
                live, record_dir / f"{case_id}.jsonl", label=f"{case_id} {settings.model}"
            )
        return live

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else Path("runs") / f"{stamp}-{settings.provider}"
    cfg = RunConfig(
        data=DataPaths(Path(args.data)),
        out_dir=out,
        model=settings.model,
        thinking=Thinking(thinking or settings.thinking),
        caps=Caps(
            max_calls=settings.max_calls,
            max_input_tokens=settings.max_input_tokens,
            max_seconds=settings.max_seconds,
            max_cost_usd=settings.max_cost_usd,
        ),
        max_output_tokens=int(str(max_output))
        if replay_dir and max_output is not None
        else settings.max_output_tokens,
    )
    print(f"running {len(case_ids)} case(s) on {settings.provider} ({settings.model}) -> {out}")
    outcomes = anyio.run(lambda: run_many(case_ids, cfg, provider_for, jobs=args.jobs))

    failures = 0
    header = f"{'case':8} {'state':18} {'stop':16} {'calls':>5} {'in tok':>8} {'cache':>6} {'out tok':>7} "
    print(header + f"{'cost $':>8} {'secs':>6}  fingerprint")
    for o in outcomes:
        t = o.totals
        hit = t["cache_hit_tokens"] / t["input_tokens"] if t["input_tokens"] else 0.0
        line = (
            f"{o.case_id:8} {o.final_state.value:18} {o.stop_reason.value:16} {t['calls']:>5} "
            f"{t['input_tokens']:>8} {hit:>6.0%} {t['output_tokens']:>7} {t['cost_usd']:>8.4f} "
            f"{o.elapsed_s:>6.1f}  {o.fingerprint()[:12]}"
        )
        if replay_dir:
            same = expected[o.case_id].get("fingerprint") == o.fingerprint()
            failures += not same
            line += "  replay identical" if same else "  REPLAY DIFFERS"
        print(line)
        if record_dir:
            body = {**o.model_dump(mode="json"), "fingerprint": o.fingerprint()}
            (record_dir / f"{o.case_id}.outcome.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
    total_cost = sum(float(o.totals["cost_usd"]) for o in outcomes)
    print(f"total cost ${total_cost:.4f} over {len(outcomes)} case(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    from article15.company.generator import DEFAULT_SEED
    from article15.mcp_servers.registry import SERVER_NAMES

    parser = argparse.ArgumentParser(prog="a15", description="Article 15 access-request desk")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="generate Ferlane's systems and the request cases from a seed")
    gen.add_argument("--seed", type=int, default=DEFAULT_SEED)
    gen.add_argument("--out", default="data/generated")
    gen.set_defaults(fn=_cmd_generate)
    serve = sub.add_parser("serve", help="run one MCP server over stdio (needs A15_SCOPE_SECRET)")
    serve.add_argument("system", choices=SERVER_NAMES)
    serve.set_defaults(fn=_cmd_serve)
    sub.add_parser("policy-lint", help="check the policy file against the code guards").set_defaults(
        fn=_cmd_lint
    )
    render = sub.add_parser("policy-render", help="show the policy section of an agent's prompt")
    render.add_argument("agent", choices=[a.value for a in AgentName])
    render.set_defaults(fn=_cmd_render)
    deadline = sub.add_parser("deadline", help="compute the deadlines for a receipt date (YYYY-MM-DD)")
    deadline.add_argument("received")
    deadline.set_defaults(fn=_cmd_deadline)
    run = sub.add_parser("run", help="run cases end to end with the single agent (default: the M2 cases)")
    run.add_argument("cases", nargs="*", help="case IDs, e.g. A15-004")
    run.add_argument("--provider", choices=["deepseek", "mistral"], help="default: A15_PROVIDER")
    run.add_argument("--model", help="model ID (default: A15_DEEPSEEK_MODEL / A15_MISTRAL_MODEL)")
    run.add_argument("--record", metavar="DIR", help="record every model call into one cassette per case")
    run.add_argument("--replay", metavar="DIR", help="replay recorded cassettes; fails if a result differs")
    run.add_argument("--data", default="data/generated")
    run.add_argument("--out", help="run directory (default: runs/<time>-<provider>)")
    run.add_argument("--jobs", type=int, default=1, help="cases run at the same time")
    run.set_defaults(fn=_cmd_run)
    from article15.evals.cli import add_parser as add_eval_parser

    add_eval_parser(sub)
    args = parser.parse_args(argv)
    result: int = args.fn(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
