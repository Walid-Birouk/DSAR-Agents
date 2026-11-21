"""The web app's store (SQLite): visitor sessions, runs, the audit log, red-team attempts and spend.

The audit log is append-only (PLAN.md 9): every stage change, decision, approval and release of a run, with
who,
when, the policy version and the pack hash. Nothing updates or deletes its rows; a trigger refuses it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    mode TEXT NOT NULL,          -- scripted | live
    arm TEXT NOT NULL,
    source TEXT NOT NULL,        -- preset | visitor | break
    status TEXT NOT NULL,        -- queued | running | done | failed
    state TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs (session_id, created_at);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    pack_hash TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TABLE IF NOT EXISTS attempts (
    run_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    blocked INTEGER
);
"""


def now() -> datetime:
    return datetime.now(UTC)


class WebStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    # --- sessions and runs -------------------------------------------------------------------------------

    def add_session(self, session_id: str) -> None:
        with self._connect() as con:
            con.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)", (session_id, now().isoformat()))

    def has_session(self, session_id: str) -> bool:
        with self._connect() as con:
            return (
                con.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
                is not None
            )

    def add_run(self, run_id: str, session_id: str, case_id: str, mode: str, arm: str, source: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO runs (run_id, session_id, case_id, mode, arm, source, status, created_at) "
                "VALUES "
                "(?, ?, ?, ?, ?, ?, 'queued', ?)",
                (run_id, session_id, case_id, mode, arm, source, now().isoformat()),
            )

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as con:
            con.execute(f"UPDATE runs SET {cols} WHERE run_id = ?", (*fields.values(), run_id))

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def runs_of(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM runs WHERE session_id = ? ORDER BY created_at DESC", (session_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def count_runs(self, session_id: str, since: datetime, mode: str | None = None) -> int:
        q = "SELECT COUNT(*) FROM runs WHERE session_id = ? AND created_at >= ?"
        args: list[Any] = [session_id, since.isoformat()]
        if mode:
            q += " AND mode = ?"
            args.append(mode)
        with self._connect() as con:
            return int(con.execute(q, args).fetchone()[0])

    def spent_since(self, since: datetime) -> float:
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM runs WHERE created_at >= ?", (since.isoformat(),)
            )
            return float(row.fetchone()[0])

    def day_stats(self) -> dict[str, Any]:
        start = now().replace(hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, state FROM runs WHERE created_at >= ?", (start.isoformat(),)
            ).fetchall()
            att = con.execute("SELECT COUNT(*), COALESCE(SUM(blocked), 0) FROM attempts").fetchone()
        done = [r for r in rows if r["status"] == "done"]
        good = [r for r in done if r["state"] in ("AWAITING_APPROVAL", "CLOSED", "REJECTED", "NEEDS_HUMAN")]
        return {
            "runs_today": len(rows),
            "finished_today": len(done),
            "success_rate": len(good) / len(done) if done else None,
            "attempts": int(att[0]),
            "attempts_blocked": int(att[1]),
        }

    def old_sessions(self, older_than: timedelta) -> list[str]:
        cutoff = (now() - older_than).isoformat()
        with self._connect() as con:
            return [
                r[0] for r in con.execute("SELECT session_id FROM sessions WHERE created_at < ?", (cutoff,))
            ]

    def forget_session(self, session_id: str) -> None:
        """Sandbox deletion after 24 hours. The audit rows stay (they hold no personal data)."""
        with self._connect() as con:
            con.execute(
                "DELETE FROM attempts WHERE run_id IN (SELECT run_id FROM runs WHERE session_id = ?)",
                (session_id,),
            )
            con.execute("DELETE FROM runs WHERE session_id = ?", (session_id,))
            con.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    # --- the audit log and red-team attempts -------------------------------------------------------------

    def audit(
        self,
        run_id: str,
        actor: str,
        action: str,
        detail: str,
        policy_version: str,
        pack_hash: str | None = None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO audit (run_id, at, actor, action, detail, policy_version, pack_hash) VALUES "
                "(?, ?, ?, ?, ?, ?, ?)",
                (run_id, now().isoformat(), actor, action, detail, policy_version, pack_hash),
            )

    def audit_of(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM audit WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_attempt(self, run_id: str, kind: str) -> None:
        with self._connect() as con:
            con.execute("INSERT OR IGNORE INTO attempts VALUES (?, ?, NULL)", (run_id, kind))

    def score_attempt(self, run_id: str, blocked: bool) -> None:
        with self._connect() as con:
            con.execute("UPDATE attempts SET blocked = ? WHERE run_id = ?", (int(blocked), run_id))
