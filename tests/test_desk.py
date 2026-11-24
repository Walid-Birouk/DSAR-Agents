"""End to end through the real desk (state machine, scope, in-process MCP servers, pack), driven offline by
the oracle agent from tests/fakes.py. Live model runs are recorded separately (evals/cassettes/)."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from article15.case.models import CaseState
from article15.company.models import CaseTruth, RecordKey, RequestCase, SpanCategory, World
from article15.company.store import DataPaths
from article15.core.replay import RecordingProvider, ReplayProvider
from article15.desk import prompts
from article15.desk.runner import CaseOutcome, Desk, RunConfig, run_case
from article15.desk.session import Pause
from tests.fakes import ScriptedProvider, oracle

S = CaseState


def _case(world: World, case_id: str) -> tuple[RequestCase, CaseTruth]:
    request = next(c for c in world.cases if c.case_id == case_id)
    truth = next(t for t in world.truths if t.case_id == case_id)
    return request, truth


@pytest.fixture(scope="module")
def desk(world: World) -> Desk:
    return Desk.load(world.people)


def run_oracle(
    world: World, data: DataPaths, desk: Desk, case_id: str, out: Path, provider_wrap: object = None
) -> CaseOutcome:
    request, truth = _case(world, case_id)
    name = next(p.name for p in world.people if p.person_id == truth.requester.person_id)
    provider = ScriptedProvider(oracle(truth, request, desk.register, name))
    wrapped = provider_wrap(provider) if callable(provider_wrap) else provider
    cfg = RunConfig(data=data, out_dir=out, model="deepseek-flash")
    return anyio.run(lambda: run_case(request, truth, desk, cfg, wrapped))


def _pack(out: Path, case_id: str) -> dict:  # type: ignore[type-arg]
    return json.loads((out / case_id / "pack.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case_id", ["A15-004", "A15-017", "A15-045", "A15-107", "A15-122"])
def test_oracle_reaches_approval_with_exactly_the_subjects_records(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path, case_id: str
) -> None:
    outcome = run_oracle(world, data, desk, case_id, tmp_path)
    _, truth = _case(world, case_id)
    assert outcome.final_state is S.AWAITING_APPROVAL, outcome
    assert outcome.pause is Pause.AWAITING_APPROVAL
    assert all(call.endswith(":ok") for call in outcome.tool_calls), outcome.tool_calls
    assert outcome.states == [
        S.INTAKE_DONE,
        S.IDENTITY_CHECK,
        S.IDENTITY_VERIFIED,
        S.DISCOVERY_DONE,
        S.REDACTION_DONE,
        S.DRAFT_READY,
        S.VALIDATED,
        S.AWAITING_APPROVAL,
    ]
    pack = _pack(tmp_path, case_id)
    got = {RecordKey(system=r["system"], table=r["table"], record_id=r["record_id"]) for r in pack["records"]}
    assert got == set(truth.records)
    text = json.dumps(pack, ensure_ascii=False)
    for span in truth.redactions:
        if span.category is SpanCategory.THIRD_PARTY:
            assert span.text not in text, f"third-party text {span.text!r} leaked into the pack"
    case = json.loads((tmp_path / case_id / "case.json").read_text(encoding="utf-8"))
    assert case["flags"]["special_category_found"] is bool(truth.special_category)
    assert outcome.totals["calls"] > 0 and outcome.totals["cost_usd"] > 0


def test_the_model_never_sees_a_case_token(world: World, data: DataPaths, desk: Desk, tmp_path: Path) -> None:
    run_oracle(world, data, desk, "A15-107", tmp_path)
    transcript = (tmp_path / "A15-107" / "transcript.json").read_text(encoding="utf-8")
    assert "a15g." not in transcript
    assert "case_token" not in transcript


def test_events_carry_no_personal_data(world: World, data: DataPaths, desk: Desk, tmp_path: Path) -> None:
    run_oracle(world, data, desk, "A15-107", tmp_path)
    request, truth = _case(world, "A15-107")
    events = (tmp_path / "A15-107" / "events.jsonl").read_text(encoding="utf-8")
    customer = next(c for c in world.customers if c.customer_id == truth.subject_id)
    for value in (customer.email, customer.last_name, customer.phone, customer.street, request.sender):
        assert value not in events
    for span in truth.redactions:
        assert span.text not in events


def test_follow_up_then_verified(world: World, data: DataPaths, desk: Desk, tmp_path: Path) -> None:
    outcome = run_oracle(world, data, desk, "A15-046", tmp_path)
    assert outcome.follow_ups == 1
    assert outcome.final_state is S.AWAITING_APPROVAL
    assert outcome.states[:5] == [
        S.INTAKE_DONE,
        S.IDENTITY_CHECK,
        S.AWAITING_REQUESTER,
        S.IDENTITY_CHECK,
        S.IDENTITY_VERIFIED,
    ]


def test_impostor_is_rejected_without_any_record(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    outcome = run_oracle(world, data, desk, "A15-020", tmp_path)
    assert outcome.final_state is S.REJECTED
    assert outcome.pack_hash is None
    assert not (tmp_path / "A15-020" / "pack.json").exists()
    assert not any(c.startswith(("crm_get_customer", "orders_search")) for c in outcome.tool_calls)


def test_representative_without_mandate_is_refused_with_the_art_12_4_letter(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    # Decision 0018: once the representative confirms they have no written mandate, the request is refused,
    # and code writes the letter: the reason, how to go ahead, the complaint and the judicial remedy.
    outcome = run_oracle(world, data, desk, "A15-051", tmp_path)
    assert outcome.final_state is S.REJECTED
    case = json.loads((tmp_path / "A15-051" / "case.json").read_text(encoding="utf-8"))
    refusal = case["refusal"]
    assert refusal["kind"] == "no_mandate"
    body = refusal["body"].lower()
    assert "cnil" in body and ("recours" in body or "judicial remedy" in body)
    assert (tmp_path / "A15-051" / "refusal.md").exists()
    # It may go to someone who is not the customer: it never confirms that the account exists.
    _, truth = _case(world, "A15-051")
    customer = next(p for p in world.people if p.person_id == truth.subject_id)
    assert not any(m.lower() in body for m in customer.mailboxes)
    assert truth.subject_id is not None and truth.subject_id.lower() not in body


def test_an_impostor_is_refused_without_learning_whether_the_account_exists(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    case_id = next(
        t.case_id
        for t in sorted(world.truths, key=lambda t: t.case_id)
        if t.kind.value == "identity_impostor"
    )
    outcome = run_oracle(world, data, desk, case_id, tmp_path)
    assert outcome.final_state is S.REJECTED
    case = json.loads((tmp_path / case_id / "case.json").read_text(encoding="utf-8"))
    assert case["refusal"]["kind"] == "identity_not_established"
    _, truth = _case(world, case_id)
    assert truth.subject_id is not None and truth.subject_id.lower() not in case["refusal"]["body"].lower()


def test_replay_reproduces_a_recorded_run_exactly(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    cassette = tmp_path / "cassettes" / "A15-046.jsonl"
    live = run_oracle(
        world,
        data,
        desk,
        "A15-046",
        tmp_path / "live",
        provider_wrap=lambda p: RecordingProvider(p, cassette, label="test"),
    )
    replayer = ReplayProvider(cassette)
    request, truth = _case(world, "A15-046")
    cfg = RunConfig(data=data, out_dir=tmp_path / "replay", model="deepseek-flash")
    again = anyio.run(lambda: run_case(request, truth, desk, cfg, replayer))
    assert again.fingerprint() == live.fingerprint()
    assert replayer.unused() == 0
    live_pack = (tmp_path / "live" / "A15-046" / "pack.json").read_bytes()
    assert (tmp_path / "replay" / "A15-046" / "pack.json").read_bytes() == live_pack


def test_replay_misses_when_the_prompt_changes(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cassette = tmp_path / "A15-004.jsonl"
    run_oracle(world, data, desk, "A15-004", tmp_path / "live", lambda p: RecordingProvider(p, cassette, "t"))
    original = prompts.single_role()
    monkeypatch.setattr(prompts, "single_role", lambda: original + "\nBe brief.")
    request, truth = _case(world, "A15-004")
    cfg = RunConfig(data=data, out_dir=tmp_path / "replay", model="deepseek-flash")
    outcome = anyio.run(lambda: run_case(request, truth, desk, cfg, ReplayProvider(cassette)))
    assert outcome.stop_reason == "provider_error"
    assert outcome.error and "message 0 differs" in outcome.error
    assert outcome.final_state is S.RECEIVED
