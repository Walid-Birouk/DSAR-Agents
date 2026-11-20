from datetime import date

import pytest
from pydantic import ValidationError

from article15.schemas import (
    Channel,
    IdentityDecision,
    IdentityOutcome,
    IntakeResult,
    Issue,
    Language,
    RedactionCategory,
    RedactionSpan,
    RequestType,
    System,
    ValidationReport,
    Verdict,
)


def test_verified_needs_subject_and_evidence() -> None:
    with pytest.raises(ValidationError, match="VERIFIED needs"):
        IdentityDecision(ok=True, outcome=IdentityOutcome.VERIFIED, confidence=0.9)


def test_needs_more_info_needs_a_question() -> None:
    with pytest.raises(ValidationError, match="follow_up_question"):
        IdentityDecision(ok=True, outcome=IdentityOutcome.NEEDS_MORE_INFO, confidence=0.5)


def test_error_set_exactly_when_failed() -> None:
    with pytest.raises(ValidationError):
        IdentityDecision(
            ok=False, outcome=IdentityOutcome.REJECTED, confidence=0.1
        )  # failed without an error
    with pytest.raises(ValidationError):
        IdentityDecision(ok=True, error="boom", outcome=IdentityOutcome.REJECTED, confidence=0.1)


def test_intake_requires_a_request_type() -> None:
    with pytest.raises(ValidationError):
        IntakeResult(
            ok=True,
            received_on=date(2026, 9, 1),
            channel=Channel.EMAIL,
            language=Language.FR,
            request_types=[],
        )
    ok = IntakeResult(
        ok=True,
        received_on=date(2026, 9, 1),
        channel=Channel.EMAIL,
        language=Language.FR,
        request_types=[RequestType.ACCESS],
    )
    assert ok.request_types == [RequestType.ACCESS]


def test_extra_fields_rejected() -> None:
    # A zombie parameter from a model must fail loudly, not be silently ignored.
    with pytest.raises(ValidationError):
        IntakeResult.model_validate(
            {
                "ok": True,
                "received_on": "2026-09-01",
                "channel": "email",
                "language": "fr",
                "request_types": ["access"],
                "force": True,
            }
        )


def test_span_order() -> None:
    with pytest.raises(ValidationError):
        RedactionSpan(
            system=System.HELPDESK,
            record_id="1",
            field="body",
            start=10,
            end=10,
            category=RedactionCategory.THIRD_PARTY,
            reason="partner name",
        )


def test_approved_cannot_have_blockers() -> None:
    with pytest.raises(ValidationError):
        ValidationReport(
            ok=True, verdict=Verdict.APPROVED, issues=[Issue(code="redaction_gap", detail="x", blocking=True)]
        )
