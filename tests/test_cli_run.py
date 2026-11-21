"""`a15 run --replay`: the command that proves a recorded run reproduces offline."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from article15.cli import main
from article15.company.models import World
from article15.company.store import DataPaths
from article15.core.replay import RecordingProvider
from article15.desk.runner import Desk, RunConfig, run_case
from tests.fakes import ScriptedProvider, oracle


def _record(world: World, data: DataPaths, cassettes: Path, case_id: str, tmp: Path) -> None:
    request = next(c for c in world.cases if c.case_id == case_id)
    truth = next(t for t in world.truths if t.case_id == case_id)
    desk = Desk.load(world.people)
    name = next(p.name for p in world.people if p.person_id == truth.requester.person_id)
    recorder = RecordingProvider(
        ScriptedProvider(oracle(truth, request, desk.register, name)), cassettes / f"{case_id}.jsonl", "t"
    )
    cfg = RunConfig(data=data, out_dir=tmp / "live", model="deepseek-flash")
    outcome = anyio.run(lambda: run_case(request, truth, desk, cfg, recorder))
    body = {**outcome.model_dump(mode="json"), "fingerprint": outcome.fingerprint()}
    (cassettes / f"{case_id}.outcome.json").write_text(json.dumps(body), encoding="utf-8")


def test_replay_command_reports_identical_runs(
    world: World, data: DataPaths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cassettes = tmp_path / "cassettes"
    _record(world, data, cassettes, "A15-017", tmp_path)
    args = [
        "run",
        "A15-017",
        "--replay",
        str(cassettes),
        "--data",
        str(data.root),
        "--out",
        str(tmp_path / "r"),
    ]
    assert main(args) == 0
    assert "replay identical" in capsys.readouterr().out

    outcome = cassettes / "A15-017.outcome.json"
    tampered = json.loads(outcome.read_text(encoding="utf-8")) | {"fingerprint": "0" * 64}
    outcome.write_text(json.dumps(tampered), encoding="utf-8")
    assert main(args) == 1
    assert "REPLAY DIFFERS" in capsys.readouterr().out


def test_replay_without_a_recording_is_an_error(tmp_path: Path, data: DataPaths) -> None:
    assert main(["run", "A15-004", "--replay", str(tmp_path), "--data", str(data.root)]) == 2
