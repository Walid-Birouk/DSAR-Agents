"""The case file: everything known about one access request."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from article15.advisor.models import Advice
from article15.case.approvals import Approval
from article15.case.deadlines import Deadlines, deadlines_for
from article15.schemas import (
    DiscoveryResult,
    IdentityDecision,
    IntakeResult,
    Language,
    LetterDraft,
    RedactionPlan,
    System,
    ValidationReport,
)


class CaseState(StrEnum):
    RECEIVED = "RECEIVED"
    INTAKE_DONE = "INTAKE_DONE"
    IDENTITY_CHECK = "IDENTITY_CHECK"
    AWAITING_REQUESTER = "AWAITING_REQUESTER"
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
    DISCOVERY_DONE = "DISCOVERY_DONE"
    REDACTION_DONE = "REDACTION_DONE"
    DRAFT_READY = "DRAFT_READY"
    VALIDATED = "VALIDATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    RELEASED = "RELEASED"
    CLOSED = "CLOSED"
    # side exits
    REJECTED = "REJECTED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    EXTENDED = "EXTENDED"
    PARTIAL = "PARTIAL"


class Flags(BaseModel):
    special_category_found: bool = False
    possibly_excessive: bool = False
    other_rights_requested: bool = False
    # Set by code right after identity is verified (case_tools.triage), never by a model:
    repeat_request: bool = False  # earlier access requests in the 12 months before this one (R-EXCESSIVE)
    large_request: bool = False  # a history big enough that a person decides on an extension (R-DEADLINE)


# What an Art. 12(4) letter must mention: the complaint to the authority and the judicial remedy.
REFUSAL_MENTIONS = {
    Language.FR: ("cnil", "recours"),
    Language.EN: ("cnil", "judicial remedy"),
}


class Notice(BaseModel):
    """A letter code writes to the requester (the Art. 12(4) refusal, desk/refusal.py)."""

    kind: str
    language: Language
    subject_line: str
    body: str

    def explains_refusal(self) -> bool:
        """True when it carries what Art. 12(4) requires (checked by guard case.rejection_explained)."""
        text = self.body.lower()
        return all(m in text for m in REFUSAL_MENTIONS[self.language])


class HumanDecision(BaseModel):
    """Recorded when a person settles an edge case (excessive request, other rights, special categories)."""

    by: str
    at: datetime
    decision: str
    reason: str


class Transition(BaseModel):
    from_state: CaseState
    to_state: CaseState
    at: datetime
    policy_version: str
    note: str = ""


class Case(BaseModel):
    case_id: str
    state: CaseState = CaseState.RECEIVED
    received_on: date
    deadlines: Deadlines
    flags: Flags = Field(default_factory=Flags)

    intake: IntakeResult | None = None
    identity: IdentityDecision | None = None
    discovery: DiscoveryResult | None = None
    redaction: RedactionPlan | None = None
    redaction_check_passed: bool = False
    letter: LetterDraft | None = None
    validation: ValidationReport | None = None
    pack_hash: str | None = None
    copy_format: str | None = None  # the media type of the copy of the data (R-COPY)
    claims_problems: list[str] = Field(default_factory=list)  # the claims cross-check (desk/claims.py)
    approval: Approval | None = None

    advice: list[Advice] = Field(default_factory=list)  # from the DPO advisor (advisory only)
    human_decision: HumanDecision | None = None
    extension_reason: str | None = None
    rejection_reason: str | None = None
    refusal: Notice | None = None  # the Art. 12(4) letter, built by code when the case is REJECTED
    partial_systems: list[System] = Field(default_factory=list)
    history: list[Transition] = Field(default_factory=list)

    @classmethod
    def open(cls, case_id: str, received_on: date) -> Case:
        return cls(case_id=case_id, received_on=received_on, deadlines=deadlines_for(received_on))
