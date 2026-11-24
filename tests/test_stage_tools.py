"""The stage tools refuse what the rules forbid. Each test plays one wrong move against the real desk."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import pytest

from article15.case.models import CaseState
from article15.company.models import CaseTruth, RequestCase, SpanCategory, World
from article15.company.store import DataPaths
from article15.core.model import ToolCall
from article15.core.tools import ToolResult
from article15.desk import redaction
from article15.desk.runner import Desk, OpenDesk, RunConfig, open_desk
from tests.fakes import payload

S = CaseState


@pytest.fixture(scope="module")
def desk(world: World) -> Desk:
    return Desk.load(world.people)


def _case(world: World, case_id: str) -> tuple[RequestCase, CaseTruth]:
    return (
        next(c for c in world.cases if c.case_id == case_id),
        next(t for t in world.truths if t.case_id == case_id),
    )


Play = Callable[[OpenDesk, Callable[..., Awaitable[ToolResult]]], Awaitable[None]]


def play(world: World, data: DataPaths, desk: Desk, tmp: Path, case_id: str, body: Play) -> None:
    request, _ = _case(world, case_id)
    cfg = RunConfig(data=data, out_dir=tmp, model="test")

    async def go() -> None:
        async with open_desk(request, desk, cfg, tmp / case_id) as d:
            n = 0

            async def call(name: str, **args: Any) -> ToolResult:
                nonlocal n
                n += 1
                return await d.manager.call(ToolCall(id=f"c{n}", name=name, arguments=json.dumps(args)))

            await body(d, call)

    anyio.run(go)


def intake_args(request: RequestCase, truth: CaseTruth) -> dict[str, Any]:
    return {
        "ok": True,
        "received_on": str(request.received_on),
        "channel": request.channel.value,
        "language": truth.language.value,
        "request_types": ["access"],
    }


async def verify_by_email(call: Callable[..., Awaitable[ToolResult]], truth: CaseTruth) -> None:
    r = await call(
        "case_submit_identity",
        ok=True,
        outcome="VERIFIED",
        subject_id=truth.subject_id,
        evidence=[{"kind": "registered_email", "detail": "sender"}],
        confidence=0.9,
    )
    assert not r.is_error, r.text


def test_stages_cannot_be_skipped(world: World, data: DataPaths, desk: Desk, tmp_path: Path) -> None:
    _, truth = _case(world, "A15-004")

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        r = await call(
            "case_submit_discovery",
            ok=True,
            subject_id=truth.subject_id,
            records=[],
            identifiers_used=["x"],
            systems_searched=["crm"],
        )
        assert r.is_error and r.text.startswith("refused: R-STAGE-ORDER")
        assert d.session.state is S.RECEIVED

    play(world, data, desk, tmp_path, "A15-004", body)


def test_records_stay_locked_until_identity_is_verified(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        r = await call("crm_get_customer")
        assert r.is_error and r.text.startswith("refused:") and "not verified" in r.text

    play(world, data, desk, tmp_path, "A15-004", body)


def test_an_agent_cannot_verify_an_impostor(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    """The impostor case (A15-020): the agent claims VERIFIED for the person named in the request."""
    request, truth = _case(world, "A15-020")

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        await call("case_submit_intake", **intake_args(request, truth))
        r = await call(
            "case_submit_identity",
            ok=True,
            outcome="VERIFIED",
            subject_id=truth.subject_id,
            evidence=[{"kind": "registered_email", "detail": "trust me"}],
            confidence=0.99,
        )
        assert r.is_error and r.text.startswith("refused: R-IDENTITY")
        # Order evidence the requester never wrote is refused too, even when it is the subject's real order.
        real_order = next(o for o in world.orders if o.customer_id == truth.subject_id)
        r = await call(
            "case_submit_identity",
            ok=True,
            outcome="VERIFIED",
            subject_id=truth.subject_id,
            evidence=[{"kind": "order_reference", "detail": "x"}],
            order_reference=real_order.order_id,
            postcode=real_order.ship_postcode,
            confidence=0.99,
        )
        assert r.is_error and "actually wrote" in r.text
        assert d.session.state is S.IDENTITY_CHECK
        assert (await call("crm_get_customer")).is_error

    play(world, data, desk, tmp_path, "A15-020", body)


def test_a_foreign_record_cannot_enter_the_pack(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    request, truth = _case(world, "A15-004")
    foreign = next(o for o in world.orders if o.customer_id not in (truth.subject_id, None))

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        await call("case_submit_intake", **intake_args(request, truth))
        await verify_by_email(call, truth)
        r = await call(
            "case_submit_discovery",
            ok=True,
            subject_id=truth.subject_id,
            records=[{"system": "orders", "table": "orders", "record_id": foreign.order_id, "reason": "x"}],
            identifiers_used=["x"],
            systems_searched=["orders"],
        )
        assert r.is_error and "not returned by any search" in r.text
        assert d.session.state is S.IDENTITY_VERIFIED
        r = await call("orders_read_record", table="orders", record_id=foreign.order_id)
        assert r.is_error and r.text.startswith("refused:")

    play(world, data, desk, tmp_path, "A15-004", body)


def test_a_discovery_list_that_leaves_out_a_returned_record_is_refused(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    # R-SCOPE in code (decision 0018): every record the scoped servers return for the person goes in the copy.
    request, truth = _case(world, "A15-004")
    own = [r for r in truth.records]
    assert len(own) > 1

    def refs(records: list) -> list[dict[str, str]]:  # type: ignore[type-arg]
        return [
            {"system": r.system.value, "table": r.table, "record_id": r.record_id, "reason": "the customer's"}
            for r in records
        ]

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        await call("case_submit_intake", **intake_args(request, truth))
        await verify_by_email(call, truth)
        args = {
            "ok": True,
            "subject_id": truth.subject_id,
            "identifiers_used": ["x"],
            "systems_searched": ["crm"],
        }
        r = await call("case_submit_discovery", records=refs(own[1:]), **args)
        assert r.is_error and r.text.startswith("refused: R-SCOPE: 1 record(s)")
        assert own[0].record_id in r.text and d.session.state is S.IDENTITY_VERIFIED
        r = await call("case_submit_discovery", records=refs(own), **args)
        assert not r.is_error and d.session.state is S.DISCOVERY_DONE

    play(world, data, desk, tmp_path, "A15-004", body)


def test_the_model_cannot_pass_its_own_token(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        r = await call("crm_check_identity", case_token=d.holder.token)
        assert r.is_error and r.text.startswith("refused:")
        assert "a15g." not in r.text
        r = await call("crm_read_record", table="customers")  # missing record_id: checked here, no echo
        assert r.is_error and "record_id" in r.text and "a15g" not in r.text

    play(world, data, desk, tmp_path, "A15-004", body)


def _to_redaction(
    world: World, request: RequestCase, truth: CaseTruth
) -> Callable[[OpenDesk, Callable[..., Awaitable[ToolResult]]], Awaitable[dict[str, str]]]:
    """Intake, identity and a correct discovery; returns ticket text by message ID."""

    async def go(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> dict[str, str]:
        await call("case_submit_intake", **intake_args(request, truth))
        await verify_by_email(call, truth)
        refs = [
            {"system": k.system.value, "table": k.table, "record_id": k.record_id, "reason": "own"}
            for k in truth.records
        ]
        r = await call(
            "case_submit_discovery",
            ok=True,
            subject_id=truth.subject_id,
            records=refs,
            identifiers_used=["x"],
            systems_searched=["crm", "orders", "helpdesk", "weblogs"],
        )
        assert not r.is_error, r.text
        bodies: dict[str, str] = {}
        for k in truth.records:
            if k.table == "tickets":
                ticket = payload((await call("helpdesk_read_ticket", ticket_id=k.record_id)).text)
                bodies |= {m["message_id"]: m["body"] for m in ticket["messages"]}
        return bodies

    return go


def test_redaction_quotes_must_be_exact(world: World, data: DataPaths, desk: Desk, tmp_path: Path) -> None:
    request, truth = _case(world, "A15-107")
    span = next(s for s in truth.redactions if s.category is SpanCategory.THIRD_PARTY)

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        await _to_redaction(world, request, truth)(d, call)
        wrong = span.text.upper()
        r = await call(
            "case_submit_redaction",
            spans=[
                {
                    "system": "helpdesk",
                    "record_id": span.record.record_id,
                    "field": "body",
                    "text": wrong,
                    "category": "third_party",
                    "reason": "relative",
                }
            ],
        )
        assert r.is_error and r.text.startswith("invalid:") and "quote it exactly" in r.text
        assert d.session.state is S.DISCOVERY_DONE

    play(world, data, desk, tmp_path, "A15-107", body)


def test_the_verifier_catches_a_name_left_elsewhere(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    """Hide the relative's name in one message only, after planting it in another one too."""
    request, truth = _case(world, "A15-107")
    span = next(s for s in truth.redactions if s.category is SpanCategory.THIRD_PARTY)

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        bodies = await _to_redaction(world, request, truth)(d, call)
        other = next(m for m in bodies if m != span.record.record_id)
        key = next(k for k in d.session.records if k[2] == other)
        record = d.session.records[key]
        record.fields["body"] += f" PS: {span.text}"
        quoted = {
            "system": "helpdesk",
            "record_id": span.record.record_id,
            "field": "body",
            "text": span.text,
            "category": "third_party",
            "reason": "relative",
        }
        r = await call("case_submit_redaction", spans=[quoted])
        assert r.is_error and r.text.startswith("refused: R-OTHERS") and other in r.text
        r = await call(
            "case_submit_redaction",
            spans=[quoted, {**quoted, "record_id": other}],
        )
        assert not r.is_error, r.text
        assert d.session.state is S.REDACTION_DONE

    play(world, data, desk, tmp_path, "A15-107", body)


def test_with_masking_broken_the_leak_check_fires(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Testing the test: switch masking off and the desk's own verifier must refuse the redaction."""
    request, truth = _case(world, "A15-107")
    span = next(s for s in truth.redactions if s.category is SpanCategory.THIRD_PARTY)
    monkeypatch.setattr(redaction, "mask", lambda text, spans, language: text)

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        await _to_redaction(world, request, truth)(d, call)
        r = await call(
            "case_submit_redaction",
            spans=[
                {
                    "system": "helpdesk",
                    "record_id": span.record.record_id,
                    "field": "body",
                    "text": span.text,
                    "category": "third_party",
                    "reason": "relative",
                }
            ],
        )
        assert r.is_error and r.text.startswith("refused: R-OTHERS")

    play(world, data, desk, tmp_path, "A15-107", body)


def test_the_letter_must_use_the_requesters_language(
    world: World, data: DataPaths, desk: Desk, tmp_path: Path
) -> None:
    request, truth = _case(world, "A15-004")

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        await _to_redaction(world, request, truth)(d, call)
        r = await call("case_submit_redaction", spans=[])
        assert not r.is_error, r.text
        r = await call(
            "case_submit_letter",
            ok=True,
            language="en",
            subject_line="x",
            body="y",
            items_covered=[],
            record_count_claimed=0,
        )
        assert r.is_error and "requester's language" in r.text

    play(world, data, desk, tmp_path, "A15-004", body)


def test_other_rights_go_to_a_person(world: World, data: DataPaths, desk: Desk, tmp_path: Path) -> None:
    request, truth = _case(world, "A15-025")

    async def body(d: OpenDesk, call: Callable[..., Awaitable[ToolResult]]) -> None:
        r = await call(
            "case_submit_intake", **{**intake_args(request, truth), "request_types": ["access", "erasure"]}
        )
        assert r.stop and d.session.state is S.NEEDS_HUMAN
        assert (await call("crm_get_customer")).is_error

    play(world, data, desk, tmp_path, "A15-025", body)
