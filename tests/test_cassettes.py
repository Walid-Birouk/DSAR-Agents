"""Recorded live runs replay offline with identical results (M2 done-when; PLAN.md 8.7).

Each `evals/cassettes/<suite>/<case>.jsonl` holds every model call of one live run, and `<case>.outcome.json`
its outcome. Replaying must reproduce the outcome's fingerprint exactly: same states, same tool calls, same
pack hash, same tokens and cost. A miss means a prompt, a tool, the policy or the data changed what is sent,
and the cassette must be recorded again with a live run.
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from article15.company.models import World
from article15.company.store import DataPaths
from article15.core.model import Thinking
from article15.core.replay import ReplayProvider
from article15.desk.runner import Desk, RunConfig, run_case

CASSETTES = Path(__file__).resolve().parents[1] / "evals" / "cassettes"
# the judge's calls (<case>.judge.jsonl) replay inside their case's eval run, not here
RECORDED = sorted(p for p in CASSETTES.glob("*/*.jsonl") if not p.name.endswith(".judge.jsonl"))


@pytest.mark.skipif(not RECORDED, reason="no recorded live runs yet")
@pytest.mark.parametrize("cassette", RECORDED, ids=lambda p: f"{p.parent.name}/{p.stem}")
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
    )
    replayer = ReplayProvider(cassette)
    outcome = anyio.run(lambda: run_case(request, truth, Desk.load(world.people), cfg, replayer))
    assert outcome.error is None, outcome.error
    assert outcome.fingerprint() == expected["fingerprint"]
    assert replayer.unused() == 0
