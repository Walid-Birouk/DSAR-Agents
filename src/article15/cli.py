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
    args = parser.parse_args(argv)
    result: int = args.fn(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
