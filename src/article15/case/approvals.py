"""Signed human approvals.

An approval covers one exact pack: the signature is over the case ID, the SHA-256 hash of the pack, the
reviewer and the time. Changing a single byte of the pack invalidates it, so the release service can never
send something a person did not see.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    pack_hash: str
    reviewer: str
    approved_at: datetime
    special_category_acknowledged: bool = False
    signature: str


def pack_hash(pack: bytes) -> str:
    return hashlib.sha256(pack).hexdigest()


def _message(case_id: str, pack_hash_: str, reviewer: str, approved_at: datetime, special: bool) -> bytes:
    return "|".join([case_id, pack_hash_, reviewer, approved_at.isoformat(), str(special)]).encode()


def sign_approval(
    secret: bytes,
    case_id: str,
    pack_hash_: str,
    reviewer: str,
    approved_at: datetime,
    special_category_acknowledged: bool = False,
) -> Approval:
    if len(secret) < 32:
        raise ValueError("approval secret must be at least 32 bytes")
    msg = _message(case_id, pack_hash_, reviewer, approved_at, special_category_acknowledged)
    return Approval(
        case_id=case_id,
        pack_hash=pack_hash_,
        reviewer=reviewer,
        approved_at=approved_at,
        special_category_acknowledged=special_category_acknowledged,
        signature=hmac.new(secret, msg, hashlib.sha256).hexdigest(),
    )


def verify_approval(secret: bytes, approval: Approval, case_id: str, current_pack_hash: str) -> bool:
    if approval.case_id != case_id or not hmac.compare_digest(approval.pack_hash, current_pack_hash):
        return False
    msg = _message(
        approval.case_id,
        approval.pack_hash,
        approval.reviewer,
        approval.approved_at,
        approval.special_category_acknowledged,
    )
    expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, approval.signature)
