"""M4: the seven-agent team, proof of control, the claims cross-check, shadow mode (E2) and the release
service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from article15.case.models import CaseState
from article15.company.models import CaseTruth, RecordKey, SpanCategory, TruthSpan, World
from article15.company.store import DataPaths, load_people
from article15.core.agent import Agent, Budget
from article15.core.model import Thinking
from article15.desk import release as reviewer
from article15.desk.claims import check_claims
from article15.desk.runner import Desk, RunConfig, open_desk, run_case
from article15.desk.team import COORDINATOR_TOOLS, SPECIALISTS, TOOLS, Brief, build_team, slice_for, tool_name
from article15.evals import broken, suites
from article15.evals.experiments import markdown as experiment_md
from article15.evals.graders import GradingContext
from article15.evals.oracle import ScriptedProvider, _letter_body, oracle
from article15.evals.runner import EvalConfig, oracle_provider_for, run_eval
from article15.schemas import AgentName, Art15Item, Language, LetterDraft, System

S = CaseState


@pytest.fixture(scope="module")
def desk(world: World) -> Desk:
    return Desk.load(world.people)


@pytest.fixture(scope="module")
def grading(data: DataPaths) -> GradingContext:
    return GradingContext.load(data)


def _eval(data: DataPaths, grading: GradingContext, out: Path, ids: list[str], arm: str, **run: Any) -> Any:
    desk = Desk.load(load_people(data))
    cfg = EvalConfig(
        name=arm,
        suite="t",
        case_ids=ids,
        run=RunConfig(data=data, out_dir=out, model="deepseek-flash", arm=arm, **run),
        agent="oracle",
        jobs=4,
    )
    return anyio.run(lambda: run_eval(cfg, oracle_provider_for(data, desk, arm), grading=grading)).report


@pytest.fixture(scope="module")
def team_smoke(world: World, data: DataPaths, grading: GradingContext, tmp_path_factory: Any) -> Any:
    ids = suites.select("smoke", world.truths)
    return _eval(data, grading, tmp_path_factory.mktemp("team"), ids, "team")


# --- the team -----------------------------------------------------------------------------------------------


def test_the_team_passes_the_smoke_suite(team_smoke: Any) -> None:
    assert team_smoke.green, [(g.case_id, g.failures) for g in team_smoke.grades if not g.passed]
    assert team_smoke.run.arm == "team"
    assert all(g.arm == "team" for g in team_smoke.grades)
    assert team_smoke.metrics["violations_attempted"] == 0


def test_every_agent_uses_only_its_own_tools(team_smoke: Any, tmp_path: Path) -> None:
    # the tool log records which agent called what; nothing outside an agent's own set was ever called
    allowed = {a.value: TOOLS[a] for a in SPECIALISTS}
    allowed["coordinator"] = frozenset({tool_name(a) for a in SPECIALISTS}) | COORDINATOR_TOOLS
    assert sum(g.unknown_tool_calls for g in team_smoke.grades) == 0
    assert allowed["coordinator"].isdisjoint({n for names in TOOLS.values() for n in names})


def test_tool_sets_are_built_as_declared(world: World, data: DataPaths, desk: Desk, tmp_path: Path) -> None:
    request = next(c for c in world.cases if c.case_id == "A15-004")
    cfg = RunConfig(data=data, out_dir=tmp_path, model="m")

    async def go() -> None:
        async with open_desk(request, desk, cfg, tmp_path / "A15-004") as d:
            team = build_team(
                session=d.session,
                hub_tools=d.hub_tools,
                case_tools=d.case_tools,
                policy=desk.policy,
                register=desk.register,
                provider=ScriptedProvider(iter([])),  # type: ignore[arg-type]
                model="m",
                thinking=Thinking.OFF,
                max_tokens=100,
                budget=Budget(),
                sink=d.sink,
                log=d.manager.log,
            )
            assert {t.name for t in team.manager.tools} == {
                tool_name(a) for a in SPECIALISTS
            } | COORDINATOR_TOOLS
            for agent, spec in team.specialists.items():
                tools = {t.name for t in spec.make_agent().tools.tools}
                assert tools == set(TOOLS[agent]), agent
            assert "notify_post_message" not in {t.name for t in d.hub_tools}

    anyio.run(go)


def test_slices_hold_only_what_each_specialist_needs(
    world: World, data: DataPaths, desk: Desk, grading: GradingContext, tmp_path: Path
) -> None:
    request = next(c for c in world.cases if c.case_id == "A15-107")
    truth = next(t for t in world.truths if t.case_id == "A15-107")
    cfg = RunConfig(data=data, out_dir=tmp_path, model="m")

    async def go() -> dict[AgentName, str]:
        name = next(p.name for p in world.people if p.person_id == truth.requester.person_id)
        provider = ScriptedProvider(oracle(truth, request, desk.register, name))
        async with open_desk(request, desk, cfg, tmp_path / "A15-107") as d:
            agent = Agent("single", provider, d.manager, "m")
            await agent.run(d.messages, Budget())
            assert d.session.state is S.AWAITING_APPROVAL
            return {a: slice_for(a, d.session, Brief()) for a in SPECIALISTS}

    slices = anyio.run(go)
    for agent in (AgentName.DISCOVERY, AgentName.REDACTION, AgentName.LETTER, AgentName.VALIDATOR):
        leaked = [v for v in grading.personal_values if v in slices[agent] and v not in request.sender]
        assert not leaked, (agent, leaked[:3])
    assert "<untrusted_request>" in slices[AgentName.INTAKE]
    assert "Records in the copy" in slices[AgentName.LETTER]


# --- proof of control ---------------------------------------------------------------------------------------


def test_an_address_the_requester_cannot_read_is_never_linked(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    truth = next(t for t in world.truths if t.links_needing_proof and t.split.value == "dev")
    request = next(c for c in world.cases if c.case_id == truth.case_id)
    others = [m for m in truth.requester.mailboxes if m not in truth.links_needing_proof]
    # The address never gets confirmed, so as far as code can tell it may be someone else's: the email and
    # phone check (decision 0017) wants it hidden wherever the customer's own messages mention it.
    own = {(k.system, k.record_id) for k in truth.records}
    hide = [
        TruthSpan(
            record=RecordKey(system=System.HELPDESK, table="messages", record_id=m.message_id),
            field="body",
            start=m.body.index(address),
            end=m.body.index(address) + len(address),
            category=SpanCategory.THIRD_PARTY,
            text=address,
            note="an address the requester could not confirm",
        )
        for address in truth.links_needing_proof
        for m in world.messages
        if (System.HELPDESK, m.message_id) in own and address in m.body
    ]
    blind: CaseTruth = truth.model_copy(
        update={
            "requester": truth.requester.model_copy(update={"mailboxes": others}),
            "redactions": [*truth.redactions, *hide],
        }
    )
    name = next(p.name for p in world.people if p.person_id == truth.requester.person_id)
    cfg = RunConfig(data=data, out_dir=tmp_path, model="m")
    provider = ScriptedProvider(oracle(blind, request, desk.register, name))
    outcome = anyio.run(lambda: run_case(request, blind, desk, cfg, provider))
    assert outcome.final_state is S.AWAITING_APPROVAL
    assert S.AWAITING_REQUESTER in outcome.states  # the code was asked for
    pack = json.loads((tmp_path / truth.case_id / "pack.json").read_text(encoding="utf-8"))
    got = {(r["system"], r["table"], r["record_id"]) for r in pack["records"]}
    want = {(k.system.value, k.table, k.record_id) for k in truth.records}
    assert got < want  # the records behind the unconfirmed address stay out
    assert hide and not any(a in json.dumps(pack, ensure_ascii=False) for a in truth.links_needing_proof)


# --- the claims cross-check ---------------------------------------------------------------------------------


def _letter(**update: Any) -> LetterDraft:
    from article15.company.register import load_register

    body = _letter_body(load_register(), Language.EN, {"crm": 2}, redacted=False)
    base = LetterDraft(
        ok=True,
        language=Language.EN,
        subject_line="Your access request",
        body=body,
        items_covered=list(Art15Item),
        record_count_claimed=2,
    )
    return base.model_copy(update=update)


PACK = {
    "records": [{"record_id": "C0001"}, {"record_id": "CS0001"}],
    "record_counts": {"crm": 2},
}


def test_claims_check_passes_a_true_letter_and_catches_each_false_claim() -> None:
    assert check_claims(_letter(), PACK) == []
    assert "record_count_claimed is 3" in check_claims(_letter(record_count_claimed=3), PACK)[0]
    problems = check_claims(_letter(body=_letter().body + "\nWe also hold 7 records about you."), PACK)
    assert any("states 7 records" in p for p in problems)
    # "0 records" is true of a system with nothing in the copy, and false when every system has records
    zero = _letter(body=_letter().body + "\nWeb logs: 0 records.")
    assert check_claims(zero, PACK) == []
    full = {**PACK, "record_counts": {"crm": 2, "orders": 1, "helpdesk": 1, "weblogs": 1}}
    assert any("states 0 records" in p for p in check_claims(zero, full))
    problems = check_claims(_letter(body=_letter().body + "\nSee order FL-100999."), PACK)
    assert any("FL-100999" in p for p in problems)
    with broken.applied("retention_missing"):
        from article15.company.register import load_register

        thin = _letter_body(load_register(), Language.EN, {"crm": 2}, redacted=False)
    problems = check_claims(_letter(body=thin), PACK)
    assert any("d_retention" in p for p in problems) and any("items_covered lists" in p for p in problems)


# --- shadow mode (E2) ---------------------------------------------------------------------------------------


def test_shadow_mode_lets_a_violation_through_and_counts_it(
    data: DataPaths, grading: GradingContext, tmp_path: Path
) -> None:
    case = ["A15-004"]
    with broken.applied("retention_missing"):  # the letter's claims fail the cross-check
        guarded = _eval(data, grading, tmp_path / "guarded", case, "single")
        shadow = _eval(data, grading, tmp_path / "shadow", case, "single", shadow=True)
    g, s = guarded.grades[0], shadow.grades[0]
    assert g.final_state is not S.AWAITING_APPROVAL and g.guard_refusals >= 1 and g.shadow_violations == 0
    assert s.final_state is S.AWAITING_APPROVAL and s.shadow_violations >= 1
    assert shadow.metrics["violations_through"] >= 1 and guarded.metrics["violations_through"] == 0
    assert guarded.metrics["violations_attempted"] >= 1
    text = experiment_md("E2", shadow, guarded)
    assert "violations let through (shadow mode)" in text


def _missing_span(script: Any, text: str) -> Any:
    """The oracle's script, except that its redaction plan forgets one span (as the live model did)."""
    seen = None
    try:
        while True:
            turn = script.send(seen) if seen is not None else next(script)
            turn = [
                (name, {**args, "spans": [s for s in args["spans"] if s["text"] != text]})
                if name == "case_submit_redaction"
                else (name, args)
                for name, args in turn
            ]
            seen = yield turn
    except StopIteration:
        return


@pytest.mark.parametrize("shadow", [False, True])
def test_shadow_mode_also_lets_a_redaction_leak_through(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path, shadow: bool
) -> None:
    # A15-027: another customer's order number in the subject's own message (the live model's slip).
    request = next(c for c in world.cases if c.case_id == "A15-027")
    truth = next(t for t in world.truths if t.case_id == "A15-027")
    name = next(p.name for p in world.people if p.person_id == truth.requester.person_id)
    cfg = RunConfig(data=data, out_dir=tmp_path, model="m", shadow=shadow)
    script = _missing_span(oracle(truth, request, desk.register, name), "FL-100514")
    outcome = anyio.run(run_case, request, truth, desk, cfg, ScriptedProvider(script))
    pack = tmp_path / "A15-027" / "pack.json"
    if shadow:  # let through, and counted once
        assert outcome.final_state is S.AWAITING_APPROVAL
        assert "FL-100514" in pack.read_text(encoding="utf-8")
        assert outcome.guard_would_block.count("R-OTHERS case.redaction_verified") == 1
    else:  # refused: no pack holds the order number
        assert outcome.final_state is not S.AWAITING_APPROVAL
        assert not pack.exists() or "FL-100514" not in pack.read_text(encoding="utf-8")


def test_e1_compares_the_two_arms(
    team_smoke: Any, world: World, data: DataPaths, grading: GradingContext, tmp_path: Path
) -> None:
    ids = [g.case_id for g in team_smoke.grades][:4]
    single = _eval(data, grading, tmp_path, ids, "single")
    text = experiment_md("E1", single, team_smoke)
    assert text.startswith("# E1: one agent with every tool against seven agents")
    assert "did not grade the same cases" in text  # 4 cases against 20


# --- the release service ------------------------------------------------------------------------------------


def _at_approval(world: World, data: DataPaths, desk: Desk, tmp_path: Path, case_id: str) -> Any:
    request = next(c for c in world.cases if c.case_id == case_id)
    truth = next(t for t in world.truths if t.case_id == case_id)
    name = next(p.name for p in world.people if p.person_id == truth.requester.person_id)
    cfg = RunConfig(data=data, out_dir=tmp_path, model="m")
    return request, truth, name, cfg


def test_release_needs_a_signed_approval_of_the_exact_pack(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    request, truth, name, cfg = _at_approval(world, data, desk, tmp_path, "A15-107")

    async def go() -> None:
        async with open_desk(request, desk, cfg, tmp_path / request.case_id) as d:
            await Agent(
                "single", ScriptedProvider(oracle(truth, request, desk.register, name)), d.manager, "m"
            ).run(d.messages, Budget())
            s = d.session
            assert s.state is S.AWAITING_APPROVAL
            with pytest.raises(reviewer.ReviewRefused):
                reviewer.release(s)  # no approval
            with pytest.raises(reviewer.ReviewRefused, match="note"):
                reviewer.request_changes(s, "rev", " ")
            special = s.case.flags.special_category_found
            if special:
                with pytest.raises(reviewer.ReviewRefused, match="R-SPECIAL"):
                    reviewer.approve(s, "rev@ferlane.example")
            reviewer.approve(s, "rev@ferlane.example", special_category_acknowledged=special)
            assert s.state is S.APPROVED
            tampered = dict(s.pack or {})
            tampered["letter"] = {"subject_line": "x", "body": "changed after approval"}
            from article15.desk.pack import hash_pack

            original_hash = s.case.pack_hash
            s.update(pack_hash=hash_pack(tampered))
            with pytest.raises(reviewer.ReviewRefused, match="R-RELEASE-APPROVAL"):
                reviewer.release(s)
            s.update(pack_hash=original_hash)
            sent = reviewer.release(s)
            assert s.state is S.CLOSED and sent.pack_hash == original_hash
            out = [n for n in d.notifier.sent(request.case_id) if n.subject == s.case.letter.subject_line]  # type: ignore[union-attr]
            assert len(out) == 1 and out[0].address == request.sender.lower()

    anyio.run(go)


def test_cli_runs_the_team(data: DataPaths, tmp_path: Path) -> None:
    from article15.cli import main

    out = tmp_path / "ev"
    code = main(
        ["eval", "run", "--arm", "team", "--cases", "A15-004", "--data", str(data.root), "--out", str(out)]
    )
    assert code == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["run"]["arm"] == "team"
    a = tmp_path / "ev" / "report.json"
    assert main(["eval", "experiment", "E1", str(a), str(a), "--out", str(tmp_path / "e1.md")]) == 0
