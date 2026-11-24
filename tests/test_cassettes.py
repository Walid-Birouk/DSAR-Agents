"""Recorded live runs replay offline with identical results (M2 done-when; PLAN.md 8.7).

Each `evals/cassettes/<suite>/<case>.jsonl` holds every model call of one live run, and `<case>.outcome.json`
its outcome. Replaying must reproduce the outcome's fingerprint exactly: same states, same tool calls, same
pack hash, same tokens and cost. A miss means a prompt, a tool, the policy or the data changed what is sent,
and the cassette must be recorded again with a live run.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import anyio
import pytest

from article15.company.models import World
from article15.company.store import DataPaths
from article15.core.model import Thinking
from article15.core.replay import ReplayProvider
from article15.desk.runner import Desk, RunConfig, run_case
from article15.evals import broken

CASSETTES = Path(__file__).resolve().parents[1] / "evals" / "cassettes"
# the judge's calls (<case>.judge.jsonl) replay inside their case's eval run, not here
# a suite run with --repeat keeps one folder per run (<suite>/run-<n>/)
RECORDED = sorted(
    p
    for pattern in ("*/*.jsonl", "*/run-*/*.jsonl")
    for p in CASSETTES.glob(pattern)
    if not p.name.endswith(".judge.jsonl")
)
REPO = CASSETTES.parents[1]


def _marker(cassette: Path, name: str) -> str | None:
    """A setting the whole recorded suite shares, in a file next to it: BROKEN (a deliberately broken desk),
    POLICY (the policy file, for experiment E3)."""
    for folder in (cassette.parent, cassette.parent.parent):
        if (folder / name).exists():
            return (folder / name).read_text(encoding="utf-8").strip()
    return None


@pytest.mark.skipif(not RECORDED, reason="no recorded live runs yet")
@pytest.mark.parametrize("cassette", RECORDED, ids=lambda p: str(p.relative_to(CASSETTES).with_suffix("")))
def test_a_recorded_run_replays_identically(
    cassette: Path, world: World, data: DataPaths, tmp_path: Path
) -> None:
    expected = json.loads(cassette.with_suffix(".outcome.json").read_text(encoding="utf-8"))
    case_id = cassette.stem
    request = next(c for c in world.cases if c.case_id == case_id)
    truth = next(t for t in world.truths if t.case_id == case_id)
    cfg = RunConfig(
        data=data,
        out_dir=tmp_path,
        model=expected["model"],
        thinking=Thinking(expected["thinking"]),
        max_output_tokens=expected["max_output_tokens"],
        # experiment runs replay in their own setting: E1's arm, E2's shadow mode
        arm=expected.get("arm", "single"),
        shadow=expected.get("shadow", False),
    )
    replayer = ReplayProvider(cassette)
    # a broken desk's recording replays with that version applied, an E3 run with its policy
    version = _marker(cassette, "BROKEN")
    policy = _marker(cassette, "POLICY")
    desk = Desk.load(world.people, REPO / policy if policy else None)
    with broken.applied(version, data) if version else contextlib.nullcontext():
        outcome = anyio.run(lambda: run_case(request, truth, desk, cfg, replayer))
    assert outcome.error is None, outcome.error
    assert outcome.fingerprint() == expected["fingerprint"]
    assert replayer.unused() == 0
