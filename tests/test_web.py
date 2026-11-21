"""M6: the web API. Sessions and sandboxes, prepared runs, live progress, the reviewer, limits, the audit "
"log."""

from __future__ import annotations

import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from article15.company.models import CaseKind, World
from article15.company.store import DataPaths
from article15.evals.oracle import ScriptedProvider, script_of
from article15.web.app import create_app
from article15.web.service import WebConfig


def _client(data: DataPaths, root: Path, **cfg: Any) -> TestClient:
    return TestClient(create_app(WebConfig(data=data, root=root, **cfg), frontend=None))


def _wait(c: TestClient, run_id: str, timeout: float = 60) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = c.get(f"/api/runs/{run_id}").json()
        if d["run"]["status"] in ("done", "failed"):
            return dict(d)
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def _case(world: World, kind: CaseKind, special: bool | None = None) -> str:
    for t in sorted(world.truths, key=lambda t: t.case_id):
        fits = special is None or bool(t.special_category) == special
        if t.split.value == "dev" and t.kind is kind and fits and not t.links_needing_proof:
            return t.case_id
    raise AssertionError(kind)


@pytest.fixture
def client(data: DataPaths, tmp_path: Path) -> TestClient:
    with _client(data, tmp_path) as c:
        yield c


def test_a_prepared_case_runs_and_the_visitor_approves_and_sends_it(world: World, client: TestClient) -> None:
    case_id = _case(world, CaseKind.STANDARD, special=False)
    assert client.get("/api/system").json()["mode"] == "scripted"
    run_id = client.post("/api/runs", json={"case_id": case_id}).json()["run_id"]
    assert "a15_session" in client.cookies
    d = _wait(client, run_id)
    assert d["run"]["state"] == "AWAITING_APPROVAL" and d["can_review"] and d["pack"]["records"]
    assert any(t["agent"] == "coordinator" for t in d["tools"]) and any(
        t["agent"] == "discovery" for t in d["tools"]
    )
    assert client.get(f"/api/runs/{run_id}/files/pack.json").status_code == 200
    assert client.get(f"/api/runs/{run_id}/trace").json()
    r = client.post(f"/api/runs/{run_id}/review", json={"action": "approve"})
    assert r.status_code == 200 and r.json()["state"] == "CLOSED"
    audit = client.get(f"/api/runs/{run_id}").json()["audit"]
    actions = [a["action"] for a in audit]
    assert "approve" in actions and "release" in actions and actions.count("transition") >= 8
    assert all(a["pack_hash"] for a in audit if a["action"] in ("approve", "release"))
    again = client.post(f"/api/runs/{run_id}/review", json={"action": "approve"})
    assert again.status_code == 409  # already sent: nothing goes twice


def test_special_category_data_needs_the_reviewers_acknowledgement(world: World, client: TestClient) -> None:
    case_id = next(
        t.case_id
        for t in sorted(world.truths, key=lambda t: t.case_id)
        if t.special_category
        and t.split.value == "dev"
        and t.kind is CaseKind.STANDARD
        and not t.links_needing_proof
    )
    run_id = client.post("/api/runs", json={"case_id": case_id}).json()["run_id"]
    d = _wait(client, run_id)
    assert d["special_category"]
    refused = client.post(f"/api/runs/{run_id}/review", json={"action": "approve"})
    assert refused.status_code == 409 and "R-SPECIAL" in refused.json()["error"]
    ok = client.post(
        f"/api/runs/{run_id}/review", json={"action": "approve", "special_category_acknowledged": True}
    )
    assert ok.json()["state"] == "CLOSED"


def test_other_review_actions(world: World, client: TestClient) -> None:
    case_id = _case(world, CaseKind.STANDARD, special=False)
    run_id = client.post("/api/runs", json={"case_id": case_id}).json()["run_id"]
    _wait(client, run_id)
    assert client.post(f"/api/runs/{run_id}/review", json={"action": "request_changes"}).status_code == 409
    assert client.post(f"/api/runs/{run_id}/review", json={"action": "fly"}).status_code == 400
    r = client.post(f"/api/runs/{run_id}/review", json={"action": "reject", "note": "not convinced"})
    assert r.json()["state"] == "REJECTED"


def test_runs_belong_to_their_session(world: World, data: DataPaths, client: TestClient) -> None:
    run_id = client.post("/api/runs", json={"case_id": _case(world, CaseKind.IDENTITY_IMPOSTOR)}).json()[
        "run_id"
    ]
    _wait(client, run_id)
    assert [r["run_id"] for r in client.get("/api/runs").json()] == [run_id]
    other = TestClient(client.app)
    assert other.get(f"/api/runs/{run_id}").status_code == 404
    assert other.get("/api/runs").json() == []


def test_live_progress_is_streamed(world: World, client: TestClient) -> None:
    run_id = client.post("/api/runs", json={"case_id": _case(world, CaseKind.STANDARD)}).json()["run_id"]
    with client.stream("GET", f"/api/runs/{run_id}/events") as r:
        body = "".join(r.iter_text())
    assert "event: step" in body and "event: done" in body and "case.transition" in body


def test_a_visitors_own_text_needs_a_live_model(world: World, client: TestClient) -> None:
    r = client.post("/api/runs", json={"customer_id": "C0001", "subject": "RGPD", "text": "Mes données svp"})
    assert r.status_code == 409


def test_live_runs_are_rate_limited(data: DataPaths, tmp_path: Path) -> None:
    provider = ScriptedProvider(script_of(["..."] * 50))
    with _client(
        data, tmp_path, live_provider=provider, live_model="m", runs_per_hour=1, human_check=False
    ) as c:
        first = c.post(
            "/api/runs", json={"customer_id": "C0001", "subject": "RGPD", "text": "Mes données svp"}
        )
        assert first.status_code == 200
        _wait(c, first.json()["run_id"])
        second = c.post("/api/runs", json={"customer_id": "C0001", "subject": "RGPD", "text": "encore"})
        assert second.status_code == 429


def test_a_break_attempt_is_scored(world: World, client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"case_id": _case(world, CaseKind.ADV_INJECTION_REQUEST), "source": "break"}
    ).json()["run_id"]
    _wait(client, run_id)
    stats = client.get("/api/system").json()
    assert stats["attempts"] == 1 and stats["attempts_blocked"] == 1
    assert client.get("/api/presets?kind=break").json()


def test_the_audit_log_is_append_only_and_sandboxes_expire(
    world: World, data: DataPaths, tmp_path: Path
) -> None:
    with _client(data, tmp_path, sandbox_ttl=timedelta(seconds=0)) as c:
        run_id = c.post("/api/runs", json={"case_id": _case(world, CaseKind.STANDARD)}).json()["run_id"]
        _wait(c, run_id)
        con = sqlite3.connect(tmp_path / "web.sqlite")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("UPDATE audit SET actor = 'someone else'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM audit")
        con.close()
        service = c.app.state.service  # type: ignore[attr-defined]
        assert service.cleanup() == 1
        assert not (tmp_path / "sessions").exists() or not any((tmp_path / "sessions").iterdir())
        assert c.get(f"/api/runs/{run_id}").status_code == 404
