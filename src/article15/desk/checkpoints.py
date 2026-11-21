"""Checkpoints and the case lock (PLAN.md 10).

- **Checkpoints at every stage boundary.** Each successful transition saves a snapshot of the case session:
  the case file, the counters, the pending and confirmed addresses, which records were fetched, the pack and
  the case clock. A run that dies (a killed process, a crash) is resumed from the last snapshot. Finished
  stages are not redone: the case is already past them, and the state machine refuses to repeat them.
- **Idempotent side effects.** Everything the desk sends carries an operation ID (the follow-up question, the
  address confirmation, the release), so a stage resumed after it had already sent something does not send it
  twice (notifier/store.py).
- **One worker per case.** A lock with an expiry: a second worker cannot take a case that is being worked on,
  and a crashed worker's lock runs out so another can resume the case.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from article15.case.models import Case
from article15.desk.session import CaseSession, Pause

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    case_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    state TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    PRIMARY KEY (case_id, seq)
);
CREATE TABLE IF NOT EXISTS locks (
    case_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: Case
    follow_ups_sent: int
    pending_links: list[str]
    linked: list[str]
    validation_rounds: int
    pause: Pause | None
    record_keys: list[tuple[str, str, str]]
    pack: dict[str, Any] | None
    clock: datetime

    @classmethod
    def of(cls, s: CaseSession) -> Snapshot:
        return cls(
            case=s.case,
            follow_ups_sent=s.follow_ups_sent,
            pending_links=list(s.pending_links),
            linked=list(s.linked),
            validation_rounds=s.validation_rounds,
            pause=s.pause,
            record_keys=[(k[0].value, k[1], k[2]) for k in s.records],
            pack=s.pack,
            clock=s.clock(),
        )


class _Db:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()


class CheckpointStore(_Db):
    def save(self, session: CaseSession) -> None:
        snap = Snapshot.of(session)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM checkpoints WHERE case_id = ?", (snap.case.case_id,)
            ).fetchone()
            con.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)",
                (
                    snap.case.case_id,
                    row[0] + 1,
                    snap.case.state.value,
                    snap.clock.isoformat(),
                    snap.model_dump_json(),
                ),
            )
            con.execute("COMMIT")

    def latest(self, case_id: str) -> Snapshot | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT snapshot FROM checkpoints WHERE case_id = ? ORDER BY seq DESC LIMIT 1", (case_id,)
            ).fetchone()
        return Snapshot.model_validate(json.loads(row["snapshot"])) if row else None

    def states(self, case_id: str) -> list[str]:
        with self._connect() as con:
            rows = con.execute("SELECT state FROM checkpoints WHERE case_id = ? ORDER BY seq", (case_id,))
            return [r["state"] for r in rows]


class CaseLock(_Db):
    """A lease: `acquire` succeeds when the case is free, already ours, or its holder's lease has run out."""

    def acquire(
        self, case_id: str, owner: str, now: datetime, ttl: timedelta = timedelta(minutes=20)
    ) -> bool:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT owner, expires_at FROM locks WHERE case_id = ?", (case_id,)).fetchone()
            if row is not None and row["owner"] != owner and datetime.fromisoformat(row["expires_at"]) > now:
                con.execute("ROLLBACK")
                return False
            con.execute(
                "INSERT INTO locks VALUES (?, ?, ?) ON CONFLICT(case_id) DO UPDATE SET owner = "
                "excluded.owner, "
                "expires_at = excluded.expires_at",
                (case_id, owner, (now + ttl).isoformat()),
            )
            con.execute("COMMIT")
            return True

    def release(self, case_id: str, owner: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM locks WHERE case_id = ? AND owner = ?", (case_id, owner))

    def holder(self, case_id: str) -> str | None:
        with self._connect() as con:
            row = con.execute("SELECT owner FROM locks WHERE case_id = ?", (case_id,)).fetchone()
        return row["owner"] if row else None
