"""Typed handoffs between agents.

Every agent ends its run with one of these results. Required fields are required on purpose: a handoff that
loses the subject's identifiers or the record references is a coordination failure, so it fails validation
instead of passing silently.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentName(StrEnum):
    INTAKE = "intake"
    IDENTITY = "identity"
    DISCOVERY = "discovery"
    REDACTION = "redaction"
    LETTER = "letter"
    VALIDATOR = "validator"
    COORDINATOR = "coordinator"
    ADVISOR = "advisor"


class Channel(StrEnum):
    WEB_FORM = "web_form"
    EMAIL = "email"


class Language(StrEnum):
    FR = "fr"
    EN = "en"


class RequestType(StrEnum):
    ACCESS = "access"
    RECTIFICATION = "rectification"
    ERASURE = "erasure"
    RESTRICTION = "restriction"
    PORTABILITY = "portability"
    OBJECTION = "objection"


class System(StrEnum):
    CRM = "crm"
    HELPDESK = "helpdesk"
    ORDERS = "orders"
    WEBLOGS = "weblogs"


class AgentResult(Strict):
    ok: bool
    error: str | None = None

    @model_validator(mode="after")
    def _error_iff_failed(self) -> AgentResult:
        if self.ok == (self.error is not None):
            raise ValueError("set `error` exactly when ok is false")
        return self


# --- Intake -------------------------------------------------------------------------------------------


class IntakeResult(AgentResult):
    received_on: date
    channel: Channel
    language: Language
    claimed_name: str | None = None
    claimed_emails: list[str] = Field(default_factory=list)
    claimed_phone: str | None = None
    account_references: list[str] = Field(default_factory=list)
    request_types: list[RequestType] = Field(min_length=1)
    on_behalf_of_someone_else: bool = False
    mandate_provided: bool = False
    embedded_instructions_detected: bool = False
    scope_note: str = ""


# --- Identity -----------------------------------------------------------------------------------------


class IdentityOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    NEEDS_MORE_INFO = "NEEDS_MORE_INFO"
    REJECTED = "REJECTED"


class Evidence(Strict):
    kind: str  # e.g. "registered_email_match", "security_question", "order_reference"
    detail: str


class IdentityDecision(AgentResult):
    outcome: IdentityOutcome
    subject_id: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    follow_up_question: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _consistent(self) -> IdentityDecision:
        if self.outcome is IdentityOutcome.VERIFIED and (not self.subject_id or not self.evidence):
            raise ValueError("VERIFIED needs a subject_id and at least one piece of evidence")
        if self.outcome is IdentityOutcome.NEEDS_MORE_INFO and not self.follow_up_question:
            raise ValueError("NEEDS_MORE_INFO needs a follow_up_question")
        return self


# --- Discovery ----------------------------------------------------------------------------------------


class RecordRef(Strict):
    system: System
    table: str
    record_id: str
    reason: (
        str  # why this record belongs to the subject, e.g. "customer_id match", "email found in ticket 812"
    )


class ExcludedCandidate(Strict):
    record: RecordRef
    why_excluded: str  # e.g. "namesake: different email and address"


class DiscoveryResult(AgentResult):
    subject_id: str
    records: list[RecordRef]
    identifiers_used: list[str] = Field(min_length=1)
    excluded_candidates: list[ExcludedCandidate] = Field(default_factory=list)
    systems_searched: list[System]
    systems_failed: list[System] = Field(default_factory=list)


# --- Redaction ----------------------------------------------------------------------------------------


class RedactionCategory(StrEnum):
    THIRD_PARTY = "third_party"
    STAFF = "staff"


class RedactionSpan(Strict):
    system: System
    record_id: str
    field: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    category: RedactionCategory
    reason: str

    @model_validator(mode="after")
    def _ordered(self) -> RedactionSpan:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class RedactionPlan(AgentResult):
    spans: list[RedactionSpan]
    unresolved: list[str] = Field(default_factory=list)  # mentions the agent could not classify


# --- Letter -------------------------------------------------------------------------------------------


class Art15Item(StrEnum):
    """GDPR Art. 15(1)(a)-(h) and 15(2)."""

    PURPOSES = "a_purposes"
    CATEGORIES = "b_categories"
    RECIPIENTS = "c_recipients"
    RETENTION = "d_retention"
    RIGHTS = "e_rights"
    COMPLAINT = "f_complaint"
    SOURCE = "g_source"
    AUTOMATED_DECISIONS = "h_automated_decisions"
    TRANSFER_SAFEGUARDS = "art15_2_transfer_safeguards"


class LetterDraft(AgentResult):
    language: Language
    subject_line: str
    body: str
    items_covered: list[Art15Item]
    record_count_claimed: int = Field(ge=0)


# --- Validation ---------------------------------------------------------------------------------------


class Verdict(StrEnum):
    APPROVED = "APPROVED"
    CONDITIONAL = "CONDITIONAL"
    REJECTED = "REJECTED"


class Issue(Strict):
    code: str  # e.g. "missing_art15_item", "unverified_claim", "redaction_gap"
    detail: str
    blocking: bool


class ValidationReport(AgentResult):
    verdict: Verdict
    issues: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _approved_has_no_blockers(self) -> ValidationReport:
        if self.verdict is Verdict.APPROVED and any(i.blocking for i in self.issues):
            raise ValueError("an APPROVED verdict cannot carry blocking issues")
        return self
