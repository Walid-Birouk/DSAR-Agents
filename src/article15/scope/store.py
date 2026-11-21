"""Scope store: grants, the identifiers each grant unlocks, and proof-of-control links.

Written only by the control plane (ScopeAuthority). MCP servers open it read-only.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from article15.company.store import connect_read_only


class Stage(StrEnum):
    IDENTITY = "identity"  # before verification: identity checks and messages to the requester only
    SUBJECT = "subject"  # after verification: the verified person's records


class IdentifierKind(StrEnum):
    EMAIL = "email"
    COOKIE = "cookie"


class ChallengeStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    BURNED = "burned"  # too many wrong codes
    EXPIRED = "expired"
    REPLACED = "replaced"  # a newer code was sent


@dataclass(frozen=True)
class GrantRow:
    grant_id: str
    case_id: str
    stage: Stage
    subject_id: str | None
    sender: str
    issued_at: datetime
    expires_at: datetime
    revoked: bool
    evidence: str


@dataclass(frozen=True)
class ChallengeRow:
    challenge_id: str
    case_id: str
    subject_id: str
    email: str
    code_hash: str
    issued_at: datetime
    expires_at: datetime
    attempts: int
    status: ChallengeStatus
    evidence: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, stage TEXT NOT NULL, subject_id TEXT,
    sender TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0, evidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grants_case ON grants (case_id);
CREATE TABLE IF NOT EXISTS grant_identifiers (
    grant_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (grant_id, kind, value)
);
CREATE TABLE IF NOT EXISTS case_links (
    case_id TEXT NOT NULL, subject_id TEXT NOT NULL, email TEXT NOT NULL, confirmed_at TEXT NOT NULL,
    evidence TEXT NOT NULL, PRIMARY KEY (case_id, subject_id, email)
);
CREATE TABLE IF NOT EXISTS challenges (
    challenge_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, subject_id TEXT NOT NULL, email TEXT NOT NULL,
    code_hash TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER NOT NULL,
    status TEXT NOT NULL, evidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_challenges_case ON challenges (case_id, email);
"""


def _grant(row: sqlite3.Row) -> GrantRow:
    return GrantRow(
        grant_id=row["grant_id"],
        case_id=row["case_id"],
        stage=Stage(row["stage"]),
        subject_id=row["subject_id"],
        sender=row["sender"],
        issued_at=datetime.fromisoformat(row["issued_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        revoked=bool(row["revoked"]),
        evidence=row["evidence"],
    )


def _challenge(row: sqlite3.Row) -> ChallengeRow:
    return ChallengeRow(
        challenge_id=row["challenge_id"],
        case_id=row["case_id"],
        subject_id=row["subject_id"],
        email=row["email"],
        code_hash=row["code_hash"],
        issued_at=datetime.fromisoformat(row["issued_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        attempts=row["attempts"],
        status=ChallengeStatus(row["status"]),
        evidence=row["evidence"],
    )


class ScopeStore:
    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as con:
                con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            con = connect_read_only(self.path)
        else:
            con = sqlite3.connect(self.path, timeout=5)
            con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    # --- reads (servers and authority) -------------------------------------------------------------------

    def load(self, grant_id: str) -> tuple[GrantRow, list[tuple[IdentifierKind, str]], list[str]] | None:
        """A grant, its identifiers and the case's proven links, read in one connection (every tool call)."""
        with self._connect() as con:
            row = con.execute("SELECT * FROM grants WHERE grant_id = ?", (grant_id,)).fetchone()
            if row is None:
                return None
            grant = _grant(row)
            ids = con.execute(
                "SELECT kind, value FROM grant_identifiers WHERE grant_id = ? ORDER BY kind, value",
                (grant_id,),
            ).fetchall()
            links = con.execute(
                "SELECT email FROM case_links WHERE case_id = ? AND subject_id = ? ORDER BY email",
                (grant.case_id, grant.subject_id or ""),
            ).fetchall()
        return grant, [(IdentifierKind(r["kind"]), r["value"]) for r in ids], [r["email"] for r in links]

    def linked_emails(self, case_id: str, subject_id: str) -> list[str]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT email FROM case_links WHERE case_id = ? AND subject_id = ? ORDER BY email",
                (case_id, subject_id),
            ).fetchall()
        return [r["email"] for r in rows]

    def latest_challenge(self, case_id: str, email: str) -> ChallengeRow | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM challenges WHERE case_id = ? AND email = ? "
                "ORDER BY issued_at DESC, rowid DESC",
                (case_id, email),
            ).fetchone()
        return _challenge(row) if row else None

    # --- writes (authority only) -------------------------------------------------------------------------

    def insert_grant(self, grant: GrantRow, identifiers: list[tuple[IdentifierKind, str, str]]) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO grants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    grant.grant_id,
                    grant.case_id,
                    grant.stage.value,
                    grant.subject_id,
                    grant.sender,
                    grant.issued_at.isoformat(),
                    grant.expires_at.isoformat(),
                    int(grant.revoked),
                    grant.evidence,
                ),
            )
            con.executemany(
                "INSERT INTO grant_identifiers VALUES (?, ?, ?, ?)",
                [(grant.grant_id, kind.value, value, source) for kind, value, source in identifiers],
            )

    def revoke_case(self, case_id: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE grants SET revoked = 1 WHERE case_id = ?", (case_id,))

    def insert_challenge(self, challenge: ChallengeRow) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE challenges SET status = ? WHERE case_id = ? AND email = ? AND status = ?",
                (
                    ChallengeStatus.REPLACED.value,
                    challenge.case_id,
                    challenge.email,
                    ChallengeStatus.PENDING.value,
                ),
            )
            con.execute(
                "INSERT INTO challenges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    challenge.challenge_id,
                    challenge.case_id,
                    challenge.subject_id,
                    challenge.email,
                    challenge.code_hash,
                    challenge.issued_at.isoformat(),
                    challenge.expires_at.isoformat(),
                    challenge.attempts,
                    challenge.status.value,
                    challenge.evidence,
                ),
            )

    def update_challenge(self, challenge_id: str, attempts: int, status: ChallengeStatus) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE challenges SET attempts = ?, status = ? WHERE challenge_id = ?",
                (attempts, status.value, challenge_id),
            )

    def add_link(self, case_id: str, subject_id: str, email: str, at: datetime, evidence: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO case_links VALUES (?, ?, ?, ?, ?)",
                (case_id, subject_id, email, at.isoformat(), evidence),
            )
