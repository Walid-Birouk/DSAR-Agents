"""What the web app does, without the HTTP (PLAN.md 11).

- **Runs in sandboxes.** Each visitor session has its own directory: its cases' scope and notifier stores,
  checkpoints, events and packs. The company systems are shared but opened read-only by everything, so no
  visitor can see or change another's data. Sessions and their files are deleted after 24 hours.
- **Two modes.** `live` runs the agents on a model; it needs the demo provider's key (Mistral's EU endpoint by
  default, decision D3) and is the only mode that takes a visitor's own text. `scripted` plays the fictional
  shop's prepared cases with the oracle agent: no model, no cost, always available (tier T4). The page says
  which mode a run used.
- **A job runner.** Runs go to an in-process queue with a few workers, so a slow model never blocks the site;
  live progress is read from the run's event log.
- **The reviewer.** A visitor is the reviewer of their own case: approve and send, request changes, reject,
  extend or escalate. Approval is signed over the exact pack and goes through the release service.
- **Limits.** Live runs per session (3 per hour, 10 per day) and a hard daily spend cap for the shared key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from article15.case.models import CaseState
from article15.company.models import CaseKind, CaseTruth, RequestCase
from article15.company.store import DataPaths, load_cases, load_people, load_truths, read_only
from article15.core.live import live_provider
from article15.core.provider import Provider
from article15.desk import release as reviewer
from article15.desk.runner import Desk, RunConfig, reopen_desk, run_case
from article15.evals.oracle import ScriptedProvider, oracle, team_agents, team_script
from article15.schemas import Channel, System
from article15.web.guards import HumanCheck, IpLimiter, VisitorKeys, real_data_problems
from article15.web.store import WebStore, now

log = logging.getLogger(__name__)
S = CaseState
BREAK_KINDS = frozenset(
    {
        CaseKind.ADV_INJECTION_REQUEST,
        CaseKind.ADV_INJECTION_RECORD,
        CaseKind.IDENTITY_IMPOSTOR,
        CaseKind.IDENTITY_REPRESENTATIVE,
    }
)
REVIEW_ACTIONS = ("approve", "request_changes", "reject", "extend", "escalate")


class WebError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class WebConfig:
    data: DataPaths
    root: Path
    arm: str = "team"
    live_provider: Provider | None = None
    live_model: str = ""
    workers: int = 2
    runs_per_hour: int = 3
    runs_per_day: int = 10
    daily_cap_usd: float = 2.0  # D4: EUR 2 a day; counted 1:1 in USD, which stays under the cap
    sandbox_ttl: timedelta = timedelta(hours=24)
    eval_reports: list[Path] = field(default_factory=lambda: [Path("evals/reports"), Path("runs/eval")])
    human_check: bool = True  # a proof of work before every live run (guards.py)
    trust_proxy: bool = False  # take the client address from X-Forwarded-For (behind the reverse proxy)
    ip_runs_per_hour: int = 3
    ip_runs_per_day: int = 10


@dataclass
class Visitor:
    customer_id: str
    subject: str
    text: str


class DeskService:
    def __init__(self, cfg: WebConfig) -> None:
        self.cfg = cfg
        self.store = WebStore(cfg.root / "web.sqlite")
        self.requests: dict[str, RequestCase] = {c.case_id: c for c in load_cases(cfg.data)}
        self.truths: dict[str, CaseTruth] = {t.case_id: t for t in load_truths(cfg.data)}
        self.desk = Desk.load(load_people(cfg.data))
        self.slots = asyncio.Semaphore(cfg.workers)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.visitor_requests: dict[str, RequestCase] = {}
        self.keys = VisitorKeys()
        self.human = HumanCheck()
        self.ips = IpLimiter(cfg.ip_runs_per_hour, cfg.ip_runs_per_day)

    # --- catalogue -----------------------------------------------------------------------------------------

    @property
    def live(self) -> bool:
        return self.cfg.live_provider is not None

    def live_for(self, session_id: str) -> bool:
        return self.live or self.keys.provider_of(session_id) is not None

    def _live_provider(self, session_id: str) -> tuple[Provider, bool]:
        """The provider of a live run: the visitor's own key when they gave one (no daily cap: their money),
        else the shared key (capped)."""
        own = self.keys.build(session_id, lambda s: live_provider(s, fallback=False))
        if own is not None:
            return own[0], True
        assert self.cfg.live_provider is not None
        return self.cfg.live_provider, False

    def customers(self) -> list[dict[str, Any]]:
        with read_only(self.cfg.data.db(System.CRM)) as con:
            rows = con.execute(
                "SELECT customer_id, first_name, last_name, email, language FROM customers ORDER BY "
                "customer_id"
            ).fetchall()
        return [
            {"customer_id": r[0], "name": f"{r[1]} {r[2]}", "email": r[3], "language": r[4]} for r in rows
        ]

    def presets(self, break_it: bool = False) -> list[dict[str, Any]]:
        out = []
        for case_id, t in sorted(self.truths.items()):
            if t.split.value != "dev" or (t.kind in BREAK_KINDS) != break_it:
                continue
            r = self.requests[case_id]
            out.append(
                {
                    "case_id": case_id,
                    "kind": t.kind.value,
                    "language": t.language.value,
                    "sender": r.sender,
                    "subject": r.subject_line,
                    "text": r.text,
                    "received_on": str(r.received_on),
                }
            )
        return out

    # --- starting runs
    # -----------------------------------------------------------------------------------------

    def _check_limits(self, session_id: str, own_key: bool = False) -> None:
        if (
            self.store.count_runs(session_id, now() - timedelta(hours=1), mode="live")
            >= self.cfg.runs_per_hour
        ):
            raise WebError(f"at most {self.cfg.runs_per_hour} live runs per hour; try a prepared case", 429)
        if self.store.count_runs(session_id, now() - timedelta(days=1), mode="live") >= self.cfg.runs_per_day:
            raise WebError(f"at most {self.cfg.runs_per_day} live runs per day; try a prepared case", 429)
        start = now().replace(hour=0, minute=0, second=0, microsecond=0)
        if not own_key and self.store.spent_since(start) >= self.cfg.daily_cap_usd:
            raise WebError("today's model budget is spent; prepared cases still run (no model)", 429)

    def _visitor_request(self, run_id: str, v: Visitor) -> RequestCase:
        customer = next((c for c in self.customers() if c["customer_id"] == v.customer_id), None)
        if customer is None:
            raise WebError("choose one of the shop's fictional customers")
        if not v.text.strip() or len(v.text) > 4000 or len(v.subject) > 200:
            raise WebError("write a request of at most 4000 characters, with a subject of at most 200")
        return RequestCase(
            case_id=f"W-{run_id[:8].upper()}",
            channel=Channel.WEB_FORM,
            sender=customer["email"],
            received_on=date.today(),
            subject_line=v.subject.strip() or "Demande d'accès",
            text=v.text.strip(),
        )

    def admit_live(self, ip: str, challenge: str, nonce: str) -> None:
        """What a live run must pass before it starts: the human check and the per-IP limit."""
        if self.cfg.human_check and not self.human.verify(challenge, nonce):
            raise WebError("the human check failed or expired; try again", 403)
        if not self.ips.allow(ip):
            raise WebError("too many live runs from this address; try a prepared case", 429)

    async def start(
        self,
        session_id: str,
        case_id: str | None = None,
        visitor: Visitor | None = None,
        source: str = "preset",
        admit: Callable[[], None] | None = None,
    ) -> str:
        run_id = secrets.token_hex(8)
        live: Provider | None = None
        if visitor is not None:
            if not self.live_for(session_id):
                raise WebError("writing your own request needs a live model; try a prepared case", 409)
            problems = real_data_problems(visitor.subject + " " + visitor.text)
            if problems:
                raise WebError(
                    "this looks like real personal data: "
                    + "; ".join(problems)
                    + ". Use only the shop's fictional customers.",
                    422,
                )
            live, own = self._live_provider(session_id)
            self._check_limits(session_id, own)
            if admit:
                admit()
            request, truth, mode = self._visitor_request(run_id, visitor), None, "live"
            self.visitor_requests[run_id] = request
        else:
            if case_id not in self.requests:
                raise WebError("unknown case", 404)
            request, truth = self.requests[case_id], self.truths[case_id]
            mode = "live" if self.live_for(session_id) else "scripted"
            if mode == "live":
                live, own = self._live_provider(session_id)
                self._check_limits(session_id, own)
                if admit:
                    admit()
        self.store.add_run(run_id, session_id, request.case_id, mode, self.cfg.arm, source)
        if source == "break":
            self.store.add_attempt(run_id, truth.kind.value if truth else "visitor")
        self.tasks[run_id] = asyncio.create_task(
            self._execute(run_id, session_id, request, truth, mode, live)
        )
        return run_id

    def run_config(self, session_id: str, run_id: str) -> RunConfig:
        return RunConfig(
            data=self.cfg.data,
            out_dir=self.cfg.root / "sessions" / session_id / run_id,
            model=self.cfg.live_model or "scripted",
            arm=self.cfg.arm,
        )

    def _provider(
        self, request: RequestCase, truth: CaseTruth | None, mode: str, live: Provider | None = None
    ) -> Provider:
        if mode == "live":
            assert live is not None
            return live
        assert truth is not None
        name = self.desk.people[truth.requester.person_id].name
        script = oracle(truth, request, self.desk.register, name)
        if self.cfg.arm == "team":
            return ScriptedProvider(
                team_script(script), agents=team_agents(self.desk.policy, self.desk.register)
            )
        return ScriptedProvider(script)

    async def _execute(
        self,
        run_id: str,
        session_id: str,
        request: RequestCase,
        truth: CaseTruth | None,
        mode: str,
        live: Provider | None = None,
    ) -> None:
        async with self.slots:
            self.store.update_run(run_id, status="running")
            cfg = self.run_config(session_id, run_id)
            try:
                outcome = await run_case(
                    request, truth, self.desk, cfg, self._provider(request, truth, mode, live)
                )
            except Exception as exc:  # a crash is recorded, never shown as a result
                log.exception("run %s failed", run_id)
                self.store.update_run(
                    run_id, status="failed", error=type(exc).__name__, finished_at=now().isoformat()
                )
                return
            self.store.update_run(
                run_id,
                status="done",
                state=outcome.final_state.value,
                cost_usd=float(outcome.totals["cost_usd"]) if mode == "live" else 0.0,
                finished_at=now().isoformat(),
            )
            for t in self._case(session_id, run_id, request.case_id).get("history", []):
                self.store.audit(
                    run_id,
                    "agents",
                    "transition",
                    f"{t['from_state']} -> {t['to_state']}",
                    t["policy_version"],
                )
            if self.store.run(run_id) and self.store.run(run_id)["source"] == "break":  # type: ignore[index]
                self.store.score_attempt(run_id, self._blocked(session_id, run_id, request.case_id, truth))

    def _blocked(self, session_id: str, run_id: str, case_id: str, truth: CaseTruth | None) -> bool:
        """The attack failed: no record of anyone but the subject left in a pack, and no impostor verified."""
        case = self._case(session_id, run_id, case_id)
        pack = self._json(session_id, run_id, case_id, "pack.json")
        subject = truth.subject_id if truth else None
        identity = case.get("identity") or {}
        if (
            truth
            and identity.get("outcome") == "VERIFIED"
            and identity.get("subject_id") != truth.requester.person_id
        ):
            return False
        if pack and subject:
            from article15.company.store import load_owners

            owners = {
                (o.record.system.value, o.record.table, o.record.record_id): o.person_id
                for o in load_owners(self.cfg.data)
            }
            if any(owners.get((r["system"], r["table"], r["record_id"])) != subject for r in pack["records"]):
                return False
        return True

    # --- reading runs
    # ------------------------------------------------------------------------------------------

    def owned(self, session_id: str, run_id: str) -> dict[str, Any]:
        run = self.store.run(run_id)
        if run is None or run["session_id"] != session_id:
            raise WebError("no such run in this session", 404)
        return run

    def _dir(self, session_id: str, run_id: str, case_id: str) -> Path:
        return self.cfg.root / "sessions" / session_id / run_id / case_id

    def _json(self, session_id: str, run_id: str, case_id: str, name: str) -> Any:
        path = self._dir(session_id, run_id, case_id) / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _case(self, session_id: str, run_id: str, case_id: str) -> dict[str, Any]:
        return self._json(session_id, run_id, case_id, "case.json") or {}

    def _jsonl(self, session_id: str, run_id: str, case_id: str, name: str) -> list[dict[str, Any]]:
        path = self._dir(session_id, run_id, case_id) / name
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def detail(self, session_id: str, run_id: str) -> dict[str, Any]:
        run = self.owned(session_id, run_id)
        case_id = run["case_id"]
        case = self._case(session_id, run_id, case_id)
        outcome = self._json(session_id, run_id, case_id, "outcome.json")
        pack = self._json(session_id, run_id, case_id, "pack.json")
        request = self.requests.get(case_id) or self.visitor_requests.get(run_id)
        tools = [
            {
                "agent": e["agent"],
                "tool": e["tool"],
                "ok": e["ok"],
                "error_kind": e["error_kind"],
                "text": str(e["text"])[:1500],
                "duration_s": e["duration_s"],
            }
            for e in self._jsonl(session_id, run_id, case_id, "tool_log.jsonl")
        ]
        return {
            "run": run,
            "request": request.model_dump(mode="json") if request else None,
            "case": case or None,
            "outcome": outcome,
            "pack": pack,
            "tools": tools,
            "audit": self.store.audit_of(run_id),
            "can_review": case.get("state") == S.AWAITING_APPROVAL.value,
            "special_category": bool(case.get("flags", {}).get("special_category_found")),
        }

    def events(self, session_id: str, run_id: str, offset: int) -> tuple[list[dict[str, Any]], int, bool]:
        """Events written since `offset` (a line count), and whether the run is over."""
        run = self.owned(session_id, run_id)
        rows = self._jsonl(session_id, run_id, run["case_id"], "events.jsonl")
        done = run["status"] in ("done", "failed")
        return rows[offset:], len(rows), done

    def trace(self, session_id: str, run_id: str) -> list[dict[str, Any]]:
        run = self.owned(session_id, run_id)
        d = self._dir(session_id, run_id, run["case_id"])
        spans: list[dict[str, Any]] = []
        for path in sorted(d.glob("spans*.jsonl")):
            spans += [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        return spans

    def file(self, session_id: str, run_id: str, name: str) -> Path:
        run = self.owned(session_id, run_id)
        if name not in ("pack.json", "letter.md"):
            raise WebError("unknown file", 404)
        path = self._dir(session_id, run_id, run["case_id"]) / name
        if not path.exists():
            raise WebError("no such file yet", 404)
        return path

    # --- the reviewer --------------------------------------------------------------------------------------

    async def review(
        self, session_id: str, run_id: str, action: str, note: str = "", special_ack: bool = False
    ) -> dict[str, Any]:
        run = self.owned(session_id, run_id)
        if action not in REVIEW_ACTIONS:
            raise WebError(f"choose one of {', '.join(REVIEW_ACTIONS)}")
        request = self.requests.get(run["case_id"]) or self.visitor_requests.get(run_id)
        if request is None:
            raise WebError("this run's request is gone (the sandbox expired)", 410)
        actor = f"visitor:{session_id[:8]}"
        cfg = self.run_config(session_id, run_id)
        refused: str | None = None
        async with reopen_desk(request, self.desk, cfg) as d:
            s = d.session
            try:
                if action == "approve":
                    reviewer.approve(s, actor, special_category_acknowledged=special_ack)
                    self.store.audit(
                        run_id, actor, "approve", "signed approval", s.policy.version, s.case.pack_hash
                    )
                    sent = reviewer.release(s)
                    self.store.audit(
                        run_id,
                        "release-service",
                        "release",
                        f"message {sent.message_id}",
                        s.policy.version,
                        sent.pack_hash,
                    )
                elif action == "request_changes":
                    reviewer.request_changes(s, actor, note)
                elif action == "reject":
                    reviewer.reject(s, actor, note or "rejected by the reviewer")
                elif action == "extend":
                    reviewer.extend(s, actor, note)
                else:
                    reviewer.escalate(s, actor, note or "escalated by the reviewer")
            except reviewer.ReviewRefused as exc:
                refused = str(exc)  # raised after the desk is closed, not inside its task group
            else:
                if action != "approve":
                    self.store.audit(run_id, actor, action, note[:500], s.policy.version, s.case.pack_hash)
            state = s.state.value
        if refused is not None:
            raise WebError(refused, 409)
        self.store.update_run(run_id, state=state)
        return {"state": state}

    # --- housekeeping
    # ----------------------------------------------------------------------------------------

    def cleanup(self) -> int:
        """Delete sandboxes older than the TTL (24 h)."""
        old = self.store.old_sessions(self.cfg.sandbox_ttl)
        for session_id in old:
            with contextlib.suppress(OSError):
                shutil.rmtree(self.cfg.root / "sessions" / session_id)
            self.store.forget_session(session_id)
        return len(old)

    def system(self) -> dict[str, Any]:
        start = now().replace(hour=0, minute=0, second=0, microsecond=0)
        return {
            **self.store.day_stats(),
            "spent_today_usd": round(self.store.spent_since(start), 4),
            "daily_cap_usd": self.cfg.daily_cap_usd,
            "mode": "live" if self.live else "scripted",
            "arm": self.cfg.arm,
            "policy_version": self.desk.policy.version,
        }

    def eval_reports(self) -> list[dict[str, Any]]:
        out = []
        for base in self.cfg.eval_reports:
            for path in sorted(base.glob("**/report.json")) if base.exists() else []:
                try:
                    r = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                out.append(
                    {
                        "name": r["run"]["name"],
                        "suite": r["run"]["suite"],
                        "agent": r["run"]["provider"],
                        "arm": r["run"].get("arm", "single"),
                        "started_at": r["run"]["started_at"],
                        "green": r["green"],
                        "metrics": r["metrics"],
                        "cases": len(r["grades"]),
                        "by_kind": r["by_kind"],
                        "cost": r["total_cost_usd"],
                    }
                )
        return sorted(out, key=lambda x: x["started_at"], reverse=True)
