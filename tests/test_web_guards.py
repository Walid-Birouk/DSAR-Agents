"""M7: the public demo's protections. The input detector, per-IP limits, the human check, bring your own "
"key."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from article15.company.store import DataPaths
from article15.evals.oracle import ScriptedProvider, script_of
from article15.web.app import create_app
from article15.web.guards import HumanCheck, IpLimiter, VisitorKeys, real_data_problems
from article15.web.service import WebConfig


@pytest.mark.parametrize(
    "text",
    [
        "Bonjour, mes données svp. Sophie, sophie.simon@courriel.example, 06 39 98 12 34",
        "Order FL-100123, postcode 75011, IP 192.0.2.7",
        "Call me on +33 6 39 98 00 01",
    ],
)
def test_fictional_data_passes(text: str) -> None:
    assert real_data_problems(text) == []


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("write to jeanne.martin@gmail.com", "email"),
        ("my number is 06 12 34 56 78", "phone"),
        ("or +33 1 42 68 53 00", "phone"),
        ("from 81.2.69.160", "IP"),
        ("IBAN FR76 3000 6000 0112 3456 7890 189", "IBAN"),
        ("card 4111 1111 1111 1111", "card"),
        ("NIR 1 85 05 78 006 084 36", "social security"),
    ],
)
def test_real_looking_data_is_refused(text: str, problem: str) -> None:
    problems = real_data_problems(text)
    assert problems and any(problem in p for p in problems), problems


def test_ip_limits_slide() -> None:
    now = [0.0]
    lim = IpLimiter(per_hour=3, per_day=4, clock=lambda: now[0])
    assert all(lim.allow("1.2.3.4") for _ in range(3)) and not lim.allow("1.2.3.4")
    assert lim.allow("5.6.7.8")  # another address has its own window
    now[0] = 3601
    assert lim.allow("1.2.3.4") and not lim.allow("1.2.3.4")  # the day's 4 are used
    now[0] = 86400 + 3602
    assert lim.allow("1.2.3.4")


def solve(challenge: str, bits: int) -> str:
    n = 0
    while not HumanCheck.solves(challenge, str(n), bits):
        n += 1
    return str(n)


def test_the_human_check_is_single_use_and_expires() -> None:
    now = [0.0]
    h = HumanCheck(bits=12, clock=lambda: now[0])
    c = h.challenge()
    nonce = solve(str(c["challenge"]), 12)
    assert h.verify(str(c["challenge"]), nonce)
    assert not h.verify(str(c["challenge"]), nonce)  # used
    c2 = h.challenge()
    assert not h.verify(str(c2["challenge"]), "wrong")
    c3 = h.challenge()
    now[0] = 301
    assert not h.verify(str(c3["challenge"]), solve(str(c3["challenge"]), 12))  # expired


def test_visitor_keys_live_in_memory_for_an_hour() -> None:
    now = [0.0]
    keys = VisitorKeys(clock=lambda: now[0])
    with pytest.raises(ValueError):
        keys.put("s", "openai", "x" * 20)
    with pytest.raises(ValueError):
        keys.put("s", "mistral", "short")
    keys.put("s", "mistral", "m" * 32)
    seen: list[Any] = []
    built = keys.build("s", lambda settings: seen.append(settings) or "provider")  # type: ignore[arg-type,func-returns-value]
    assert built is not None and seen[0].api_key == "m" * 32 and seen[0].provider == "mistral"
    now[0] = 3601
    assert keys.provider_of("s") is None and keys.build("s", lambda s: "p") is None  # type: ignore[arg-type,return-value]


def _client(data: DataPaths, root: Path, **cfg: Any) -> TestClient:
    return TestClient(create_app(WebConfig(data=data, root=root, **cfg), frontend=None))


def _wait(c: TestClient, run_id: str) -> dict[str, Any]:
    for _ in range(600):
        d = c.get(f"/api/runs/{run_id}").json()
        if d["run"]["status"] in ("done", "failed"):
            return dict(d)
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_live_runs_need_the_human_check(data: DataPaths, tmp_path: Path) -> None:
    fake = ScriptedProvider(script_of(["..."] * 50))
    with _client(data, tmp_path, live_provider=fake, live_model="m") as c:
        body = {"customer_id": "C0001", "subject": "RGPD", "text": "Je voudrais mes données."}
        assert c.post("/api/runs", json=body).status_code == 403
        ch = c.get("/api/challenge").json()
        ok = c.post(
            "/api/runs",
            json={**body, "challenge": ch["challenge"], "nonce": solve(ch["challenge"], ch["bits"])},
        )
        assert ok.status_code == 200
        _wait(c, ok.json()["run_id"])


def test_real_personal_data_in_a_request_is_refused(data: DataPaths, tmp_path: Path) -> None:
    fake = ScriptedProvider(script_of(["..."] * 50))
    with _client(data, tmp_path, live_provider=fake, live_model="m", human_check=False) as c:
        r = c.post(
            "/api/runs", json={"customer_id": "C0001", "subject": "x", "text": "écrivez à moi@gmail.com"}
        )
        assert r.status_code == 422 and "real personal data" in r.json()["error"]


def test_a_visitor_key_enables_live_runs_and_can_be_dropped(data: DataPaths, tmp_path: Path) -> None:
    with _client(data, tmp_path) as c:
        assert c.get("/api/session").json()["live"] is False
        assert c.post("/api/byok", json={"provider": "openai", "key": "k" * 32}).status_code == 422
        r = c.post("/api/byok", json={"provider": "mistral", "key": "k" * 32})
        assert r.status_code == 200 and c.get("/api/session").json() == {
            "live": True,
            "own_key": "mistral",
            "human_check": True,
        }
        assert "k" * 32 not in r.text
        c.delete("/api/byok")
        assert c.get("/api/session").json()["own_key"] is None
