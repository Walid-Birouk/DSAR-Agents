"""M5: traces without personal data, and the DPO advisor over A2A behind its egress filter."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from article15.advisor.egress import EgressRefused, check_payload, outgoing
from article15.advisor.models import Advice, CaseSummary, Question, Recommendation
from article15.advisor.service import AdvisorClient, AdvisorError, ModelAdvisor, build_app, rules_advice
from article15.case.models import CaseState
from article15.company.models import CaseKind, World
from article15.company.store import DataPaths
from article15.core import agent as agent_module
from article15.desk.runner import Desk, RunConfig, run_case
from article15.evals.graders import GradingContext
from article15.evals.oracle import ScriptedProvider, oracle, script_of, team_agents, team_script
from article15.schemas import RequestType
from article15.telemetry.otel import SCRUBBED, Instruments, OtelSink, Scrubber, case_tracing, span_rows

S = CaseState
KEY = "k" * 24


@pytest.fixture(scope="module")
def desk(world: World) -> Desk:
    return Desk.load(world.people)


@pytest.fixture(scope="module")
def grading(data: DataPaths) -> GradingContext:
    return GradingContext.load(data)


def _run(
    world: World, data: DataPaths, desk: Desk, out: Path, case_id: str, arm: str = "single", **cfg: Any
) -> Any:
    request = next(c for c in world.cases if c.case_id == case_id)
    truth = next(t for t in world.truths if t.case_id == case_id)
    name = next(p.name for p in world.people if p.person_id == truth.requester.person_id)
    script = oracle(truth, request, desk.register, name)
    provider = (
        ScriptedProvider(team_script(script), agents=team_agents(desk.policy, desk.register))
        if arm == "team"
        else ScriptedProvider(script)
    )
    run_cfg = RunConfig(data=data, out_dir=out, model="m", arm=arm, **cfg)
    outcome = anyio.run(lambda: run_case(request, truth, desk, run_cfg, provider))
    spans = [json.loads(x) for x in (out / case_id / "spans.jsonl").read_text(encoding="utf-8").splitlines()]
    return outcome, spans


# --- traces -------------------------------------------------------------------------------------------------


def test_one_trace_per_case_with_the_planned_hierarchy(
    world: World, data: DataPaths, desk: Desk, grading: GradingContext, tmp_path: Path
) -> None:
    outcome, spans = _run(world, data, desk, tmp_path, "A15-107", arm="team")
    assert outcome.final_state is S.AWAITING_APPROVAL
    assert len({s["trace_id"] for s in spans}) == 1
    by_id = {s["span_id"]: s for s in spans}
    root = [s for s in spans if s["parent_id"] is None]
    assert [s["name"] for s in root] == ["case"]
    assert root[0]["attributes"]["case.hid"].startswith("h:") and "A15-107" not in json.dumps(root[0])
    transitions = [e for e in root[0]["events"] if e["name"] == "case.transition"]
    assert any(e["attributes"].get("to_state") == "AWAITING_APPROVAL" for e in transitions)
    for s in spans:
        if s["name"] in ("llm.call", "tool.call"):
            assert by_id[s["parent_id"]]["name"] in ("agent.run", "tool.call"), s
    # a specialist's run sits inside the coordinator's tool call that started it
    nested = [s for s in spans if s["name"] == "agent.run" and s["attributes"].get("agent") == "identity"]
    assert nested and by_id[nested[0]["parent_id"]]["attributes"].get("tool") == "identity_agent"
    text = json.dumps(spans, ensure_ascii=False)
    assert not [v for v in grading.personal_values if v in text]


def test_the_scrubber_catches_personal_data_in_an_attribute() -> None:
    provider, memory = case_tracing()
    scrubber = Scrubber.with_values(["Camille Durand"])
    sink = OtelSink(provider.get_tracer("t"), "A15-001", b"k" * 32, scrubber)
    sink.emit("tool.invoke", agent="a", tool="crm_check_identity")
    sink.emit("tool.complete", agent="a", tool="camille@courriel.example", status="ok", duration_s=0.1)
    sink.emit("error.occurred", error_type="Camille Durand wrote", tool="x")
    sink.emit("error.occurred", error_type="call 06 12 34 56 78", tool="x")
    sink.close()
    text = json.dumps(span_rows(memory.get_finished_spans()))
    assert "courriel.example" not in text and "Camille" not in text and "06 12" not in text
    assert text.count(SCRUBBED) == 3 and scrubber.hits == 3


def test_raw_prompts_on_spans_are_caught_by_the_scrubber(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The misconfiguration of broken version `raw_prompts_to_telemetry`, but with the scrubber still on: the
    # second line of defence holds for every seeded person's name and address.
    monkeypatch.setattr(agent_module, "TRACE_CONTENTS", True)
    _, spans = _run(world, data, desk, tmp_path, "A15-107")
    prompts = [s["attributes"]["prompt"] for s in spans if "prompt" in s["attributes"]]
    assert prompts and SCRUBBED in prompts
    text = json.dumps(spans, ensure_ascii=False)
    known = [p.name for p in world.people] + [m for p in world.people for m in p.mailboxes]
    assert not [v for v in known if len(v) >= 6 and v in text]


def test_metrics_count_tokens_cost_and_tool_calls() -> None:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("t")
    provider, _ = case_tracing()
    sink = OtelSink(provider.get_tracer("t"), "A15-001", b"k" * 32, instruments=Instruments(meter))
    sink.emit("llm.request", agent="single", provider="p", model="m", messages=2, digest="d")
    sink.emit(
        "llm.response",
        agent="single",
        provider="p",
        model="m",
        input_tokens=100,
        cache_hit_tokens=60,
        output_tokens=20,
        cost_usd=0.001,
        latency_s=0.5,
        finish_reason="stop",
        tool_calls=0,
        reasoning_tokens=0,
    )
    sink.emit("tool.invoke", agent="single", tool="orders_search")
    sink.emit("tool.complete", agent="single", tool="orders_search", status="refused", duration_s=0.01)
    data = reader.get_metrics_data()
    assert data is not None
    names = {m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
    assert {"a15.llm.tokens", "a15.llm.cost", "a15.llm.latency", "a15.tool.calls"} <= names


# --- the advisor over A2A
# ------------------------------------------------------------------------------------


SUMMARY = CaseSummary(
    question=Question.EXCESSIVE,
    received_on=date(2026, 3, 2),
    due=date(2026, 4, 2),
    request_types=[RequestType.ACCESS],
    earlier_requests_last_12_months=2,
    days_since_last_request=60,
    records_found=30,
    systems_with_records=3,
    special_category_mentions=0,
)


def _client(key: str = KEY, advise: Any = rules_advice) -> AdvisorClient:
    app = build_app(advise, KEY)
    return AdvisorClient("http://advisor.internal", key, transport=httpx.ASGITransport(app=app))


def test_the_advisor_answers_over_a2a_and_publishes_its_card() -> None:
    advice = anyio.run(lambda: _client().consult(SUMMARY))
    assert advice.recommendation is Recommendation.PERSON_DECIDES and advice.sources

    async def card() -> dict[str, Any]:
        app = build_app(rules_advice, KEY)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as http:
            r = await http.get("/.well-known/agent-card.json")
            assert r.status_code == 200  # the card is public
            return dict(r.json())

    body = card_body = anyio.run(card)
    assert {s["id"] for s in body["skills"]} == {q.value for q in Question}
    del card_body


def test_the_advisor_needs_its_bearer_key() -> None:
    with pytest.raises(AdvisorError):
        anyio.run(lambda: _client(key="x" * 24).consult(SUMMARY))


def test_the_egress_filter_blocks_anything_that_looks_personal() -> None:
    assert check_payload(SUMMARY.model_dump_json()) == []
    assert check_payload('{"x": "jeanne.martin@courriel.example"}') == ["looks like email"]
    assert check_payload('{"x": "06 12 34 56 78"}') == ["looks like phone"]
    assert check_payload('{"x": "order FL-100123"}') == ["looks like record id"]
    assert check_payload('{"x": "Jeanne Martin"}', never=["jeanne martin"]) == [
        "contains a value of the case's people"
    ]
    with pytest.raises(EgressRefused):
        outgoing(SUMMARY, never=["2026-03-02"])  # a value the caller says must not leave
    with pytest.raises(ValueError):
        CaseSummary.model_validate({**SUMMARY.model_dump(), "note": "free text is not allowed"})


def test_rules_advice() -> None:
    a = anyio.run(rules_advice, SUMMARY.model_copy(update={"earlier_requests_last_12_months": 0}))
    assert a.recommendation is Recommendation.PROCEED
    big = SUMMARY.model_copy(
        update={"question": Question.EXTENSION, "records_found": 241, "systems_with_records": 4}
    )
    assert anyio.run(rules_advice, big).recommendation is Recommendation.EXTENSION_JUSTIFIED


def test_the_model_advisor_uses_its_knowledge_tool_and_falls_back_to_rules() -> None:
    advice = Advice(
        recommendation=Recommendation.PERSON_DECIDES, reasons=["repeated"], sources=["GDPR Art. 12(5)"]
    )
    script = script_of(
        [[("gdpr_lookup", {"question": "possibly_excessive"})], [("submit_result", advice.model_dump())]]
    )
    got = anyio.run(ModelAdvisor(ScriptedProvider(script), "deepseek-flash"), SUMMARY)
    assert got.recommendation is Recommendation.PERSON_DECIDES and got.advisor == "deepseek-flash"
    bad = ScriptedProvider(script_of(["I think it is fine."] * 6))
    assert anyio.run(ModelAdvisor(bad, "m"), SUMMARY).advisor == "rules"


def test_the_desk_consults_the_advisor_without_personal_data(
    world: World, data: DataPaths, desk: Desk, grading: GradingContext, tmp_path: Path
) -> None:
    case_id = next(
        t.case_id for t in sorted(world.truths, key=lambda t: t.case_id) if t.kind is CaseKind.EDGE_REPEAT
    )
    outcome, _ = _run(world, data, desk, tmp_path, case_id)
    assert outcome.final_state is S.NEEDS_HUMAN
    payloads = (tmp_path / case_id / "advisor_payloads.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(payloads) == 1
    summary = CaseSummary.model_validate_json(payloads[0])
    assert summary.question is Question.EXCESSIVE and summary.earlier_requests_last_12_months >= 1
    assert not [v for v in grading.personal_values if v in payloads[0]]
    case = json.loads((tmp_path / case_id / "case.json").read_text(encoding="utf-8"))
    assert case["advice"] and case["advice"][0]["recommendation"] == "person_decides"


def test_with_the_advisor_off_edge_cases_go_straight_to_a_person(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    case_id = next(
        t.case_id for t in sorted(world.truths, key=lambda t: t.case_id) if t.kind is CaseKind.EDGE_REPEAT
    )
    outcome, _ = _run(world, data, desk, tmp_path, case_id, advisor="off")
    assert outcome.final_state is S.NEEDS_HUMAN
    assert any(c.startswith("advisor_possibly_excessive:unavailable") for c in outcome.tool_calls)
    assert not (tmp_path / case_id / "advisor_payloads.jsonl").exists()
