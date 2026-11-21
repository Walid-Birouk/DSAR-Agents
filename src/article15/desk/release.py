"""The reviewer's actions and the release service (PLAN.md 5.6). Plain code, not an agent: no model can send.

A person reviewing a pack at AWAITING_APPROVAL can:
- approve it: code signs an approval over the case ID, the SHA-256 of the exact pack, the reviewer and the
  time (and whether they acknowledged special-category data);
- request changes, with a note: the case goes back to DRAFT_READY for the agents;
- reject it, with a reason;
- extend the deadline, with a reason (only within the first month);
- escalate it to another person.

`release` then sends the pack to the requester's outbox. It goes through guard `case.release_approval`, which
re-checks the signature against the pack as it is now, so a pack changed after approval cannot be sent.
"""

from __future__ import annotations

from dataclasses import dataclass

from article15.case.approvals import sign_approval
from article15.case.machine import GuardFailure
from article15.case.models import CaseState
from article15.desk.pack import pack_bytes
from article15.desk.session import CaseSession, Pause, refusal

S = CaseState


class ReviewRefused(Exception):
    def __init__(self, failures: list[GuardFailure] | str) -> None:
        self.failures = failures if isinstance(failures, list) else []
        super().__init__(failures if isinstance(failures, str) else refusal(failures))


@dataclass(frozen=True)
class Released:
    case_id: str
    pack_hash: str
    message_id: int


def _require(session: CaseSession, state: CaseState) -> None:
    if session.state is not state:
        raise ReviewRefused(f"the case is at {session.state}, not {state}")


def _advance(session: CaseSession, target: CaseState, note: str) -> None:
    failures = session.advance(target, note)
    if failures:
        raise ReviewRefused(failures)


def approve(session: CaseSession, reviewer: str, special_category_acknowledged: bool = False) -> None:
    _require(session, S.AWAITING_APPROVAL)
    assert session.case.pack_hash is not None
    approval = sign_approval(
        session.approval_secret,
        session.case.case_id,
        session.case.pack_hash,
        reviewer,
        session.clock(),
        special_category_acknowledged,
    )
    session.update(approval=approval)
    failures = session.advance(S.APPROVED, f"approved by {reviewer}")
    if failures:
        session.update(approval=None)
        raise ReviewRefused(failures)


def request_changes(session: CaseSession, reviewer: str, note: str) -> None:
    _require(session, S.AWAITING_APPROVAL)
    if not note.strip():
        raise ReviewRefused("a change request needs a note for the agents")
    _advance(session, S.DRAFT_READY, f"changes requested by {reviewer}: {note}")
    session.pause = None


def reject(session: CaseSession, reviewer: str, reason: str) -> None:
    _require(session, S.AWAITING_APPROVAL)
    session.update(rejection_reason=reason)
    _advance(session, S.REJECTED, f"rejected by {reviewer}")
    session.pause = Pause.REJECTED


def extend(session: CaseSession, reviewer: str, reason: str) -> None:
    _require(session, S.AWAITING_APPROVAL)
    session.update(extension_reason=reason)
    _advance(session, S.EXTENDED, f"extended by {reviewer}")


def escalate(session: CaseSession, reviewer: str, reason: str) -> None:
    _require(session, S.AWAITING_APPROVAL)
    _advance(session, S.NEEDS_HUMAN, f"escalated by {reviewer}: {reason}")
    session.pause = Pause.WITH_A_PERSON


def release(session: CaseSession) -> Released:
    """Send the approved pack to the requester's outbox, then close the case."""
    if session.pack is None or session.case.letter is None or session.case.pack_hash is None:
        raise ReviewRefused("there is no pack to release")
    _advance(session, S.RELEASED, "released")
    letter = session.case.letter
    note = session.notifier.send(
        session.case.case_id,
        to=session.request.sender,
        subject=letter.subject_line,
        body=letter.body + f"\n\n[attachment: pack.json, {len(pack_bytes(session.pack))} bytes]",
        at=session.clock(),
        op_id=f"release:{session.case.pack_hash}",
    )
    _advance(session, S.CLOSED, "reply sent")
    return Released(session.case.case_id, session.case.pack_hash, note.message_id)
