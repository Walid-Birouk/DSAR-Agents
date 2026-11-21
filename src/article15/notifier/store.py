"""Message store behind the notifier.

"Sending" writes to this store; nothing leaves the machine. Each message is addressed to one mailbox, so a
mailbox shows only what was sent to that address. This is how proof of control works in the simulation: a
one-time code sent to an address can be read only by whoever controls that mailbox (the simulated requester in
the evals, the visitor in the demo), never by the agents, whose tools read only the requester's replies.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from article15.schemas import Strict


class Direction(StrEnum):
    TO_REQUESTER = "to_requester"
    FROM_REQUESTER = "from_requester"


class Kind(StrEnum):
    MESSAGE = "message"
    LINK_CODE = "link_code"  # a one-time code proving control of an address


class Note(Strict):
    message_id: int
    case_id: str
    direction: Direction
    kind: Kind
    address: str  # recipient for outgoing messages, sender for replies
    subject: str
    body: str
    sent_at: datetime
    op_id: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    kind TEXT NOT NULL,
    address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    op_id TEXT  -- set on outgoing messages: one operation sends at most once, even across a crash and resume
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_op ON messages (case_id, op_id);
CREATE INDEX IF NOT EXISTS idx_messages_case ON messages (case_id, direction);
CREATE INDEX IF NOT EXISTS idx_messages_address ON messages (address);
"""


class NotifierStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def _insert(
        self,
        case_id: str,
        direction: Direction,
        kind: Kind,
        address: str,
        subject: str,
        body: str,
        at: datetime,
        op_id: str | None = None,
    ) -> Note:
        with self._connect() as con:
            if op_id is not None:
                row = con.execute(
                    "SELECT * FROM messages WHERE case_id = ? AND op_id = ?", (case_id, op_id)
                ).fetchone()
                if row is not None:  # already sent by an earlier attempt: do not send twice
                    return Note.model_validate(dict(row))
            cur = con.execute(
                "INSERT INTO messages (case_id, direction, kind, address, subject, body, sent_at, op_id) "
                "VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, direction.value, kind.value, address.lower(), subject, body, at.isoformat(), op_id),
            )
            message_id = cur.lastrowid
        assert message_id is not None
        return Note(
            message_id=message_id,
            case_id=case_id,
            direction=direction,
            kind=kind,
            address=address.lower(),
            subject=subject,
            body=body,
            sent_at=at,
        )

    def send(
        self,
        case_id: str,
        to: str,
        subject: str,
        body: str,
        at: datetime,
        kind: Kind = Kind.MESSAGE,
        op_id: str | None = None,
    ) -> Note:
        return self._insert(case_id, Direction.TO_REQUESTER, kind, to, subject, body, at, op_id)

    def receive(self, case_id: str, sender: str, body: str, at: datetime, subject: str = "") -> Note:
        """A reply from the requester (posted by the simulated requester or the demo visitor)."""
        return self._insert(case_id, Direction.FROM_REQUESTER, Kind.MESSAGE, sender, subject, body, at)

    def _select(self, where: str, args: tuple[str, ...]) -> list[Note]:
        with self._connect() as con:
            rows = con.execute(f"SELECT * FROM messages WHERE {where} ORDER BY message_id", args).fetchall()
        return [Note.model_validate(dict(row)) for row in rows]

    def replies(self, case_id: str) -> list[Note]:
        return self._select("case_id = ? AND direction = ?", (case_id, Direction.FROM_REQUESTER.value))

    def sent(self, case_id: str) -> list[Note]:
        return self._select("case_id = ? AND direction = ?", (case_id, Direction.TO_REQUESTER.value))

    def mailbox(self, address: str) -> list[Note]:
        """Everything delivered to one address. Only the mailbox's owner may read this."""
        return self._select("address = ? AND direction = ?", (address.lower(), Direction.TO_REQUESTER.value))
