from datetime import date, datetime

import pytest

from article15.case.approvals import pack_hash, sign_approval, verify_approval
from article15.case.machine import ALLOWED, GuardContext, Machine, TransitionRefused
from article15.case.models import Case, CaseState
from article15.schemas import (
    Channel,
    DiscoveryResult,
    Evidence,
    IdentityDecision,
    IdentityOutcome,
    Language,
    RecordRef,
    RedactionPlan,
    System,
    ValidationReport,
    Verdict,
)

S = CaseState
SECRET = b"x" * 32
CTX = GuardContext(now=datetime(2026, 9, 1, 12, 0), approval_secret=SECRET, policy_version="1.0.0")
PACK = b"%PDF fake pack"


def verified() -> IdentityDecision:
    return IdentityDecision(
        ok=True,
        outcome=IdentityOutcome.VERIFIED,
        subject_id="cust-0042",
        evidence=[Evidence(kind="registered_email_match", detail="from registered email")],
        confidence=0.97,
    )


def discovery(subject: str = "cust-0042", failed: list[System] | None = None) -> DiscoveryResult:
    return DiscoveryResult(
        ok=True,
        subject_id=subject,
        records=[RecordRef(system=System.CRM, table="customers", record_id="42", reason="customer_id match")],
        identifiers_used=["cust-0042"],
        systems_searched=list(System),
        systems_failed=failed or [],
    )


def ready_case() -> Case:
    """A case that has legitimately reached AWAITING_APPROVAL."""
    m = Machine(CTX)
    c = Case.open("case-1", date(2026, 8, 31))
    c = m.advance(c, S.INTAKE_DONE)
    c = m.advance(c, S.IDENTITY_CHECK)
    c = m.advance(c.model_copy(update={"identity": verified()}), S.IDENTITY_VERIFIED)
    c = m.advance(c.model_copy(update={"discovery": discovery()}), S.DISCOVERY_DONE)
    c = m.advance(
        c.model_copy(update={"redaction": RedactionPlan(ok=True, spans=[]), "redaction_check_passed": True}),
        S.REDACTION_DONE,
    )
    c = m.advance(c, S.DRAFT_READY)
    c = m.advance(
        c.model_copy(
            update={
                "validation": ValidationReport(ok=True, verdict=Verdict.APPROVED),
                "pack_hash": pack_hash(PACK),
                "copy_format": "application/json",
            }
        ),
        S.VALIDATED,
    )
    return m.advance(c, S.AWAITING_APPROVAL)


def test_happy_path_to_release() -> None:
    m = Machine(CTX)
    c = ready_case()
    approval = sign_approval(SECRET, c.case_id, pack_hash(PACK), "reviewer@example.test", CTX.now)
    c = m.advance(c.model_copy(update={"approval": approval}), S.APPROVED)
    c = m.advance(c, S.RELEASED)
    assert c.state is S.RELEASED
    assert [t.to_state for t in c.history][-2:] == [S.APPROVED, S.RELEASED]
    assert all(t.policy_version == "1.0.0" for t in c.history)


def test_cannot_skip_identity() -> None:
    m = Machine(CTX)
    c = m.advance(Case.open("case-2", date(2026, 9, 1)), S.INTAKE_DONE)
    with pytest.raises(TransitionRefused, match="R-STAGE-ORDER"):
        m.advance(c, S.DISCOVERY_DONE)


def test_identity_needs_evidence() -> None:
    m = Machine(CTX)
    c = m.advance(m.advance(Case.open("case-3", date(2026, 9, 1)), S.INTAKE_DONE), S.IDENTITY_CHECK)
    with pytest.raises(TransitionRefused, match="R-IDENTITY"):
        m.advance(c, S.IDENTITY_VERIFIED)


def test_discovery_for_wrong_subject_refused() -> None:
    m = Machine(CTX)
    c = ready_case().model_copy(update={"state": S.IDENTITY_VERIFIED, "discovery": discovery("cust-9999")})
    with pytest.raises(TransitionRefused, match="different subject"):
        m.advance(c, S.DISCOVERY_DONE)


def test_failed_system_forces_partial() -> None:
    m = Machine(CTX)
    c = ready_case().model_copy(
        update={"state": S.IDENTITY_VERIFIED, "discovery": discovery(failed=[System.WEBLOGS])}
    )
    with pytest.raises(TransitionRefused, match="PARTIAL"):
        m.advance(c, S.DISCOVERY_DONE)
    assert m.advance(c, S.PARTIAL).state is S.PARTIAL


def test_release_without_approval_refused() -> None:
    with pytest.raises(TransitionRefused, match="R-RELEASE-APPROVAL"):
        Machine(CTX).advance(ready_case(), S.APPROVED)


def test_approval_for_a_different_pack_refused() -> None:
    c = ready_case()
    stale = sign_approval(SECRET, c.case_id, pack_hash(b"an older pack"), "rev", CTX.now)
    with pytest.raises(TransitionRefused, match="exact pack"):
        Machine(CTX).advance(c.model_copy(update={"approval": stale}), S.APPROVED)


def test_forged_signature_refused() -> None:
    c = ready_case()
    good = sign_approval(SECRET, c.case_id, pack_hash(PACK), "rev", CTX.now)
    forged = good.model_copy(update={"reviewer": "someone else"})
    assert not verify_approval(SECRET, forged, c.case_id, pack_hash(PACK))
    with pytest.raises(TransitionRefused):
        Machine(CTX).advance(c.model_copy(update={"approval": forged}), S.APPROVED)


def test_special_category_needs_acknowledgement() -> None:
    c = ready_case()
    c = c.model_copy(update={"flags": c.flags.model_copy(update={"special_category_found": True})})
    plain = sign_approval(SECRET, c.case_id, pack_hash(PACK), "rev", CTX.now)
    with pytest.raises(TransitionRefused, match="R-SPECIAL"):
        Machine(CTX).advance(c.model_copy(update={"approval": plain}), S.APPROVED)
    ack = sign_approval(
        SECRET, c.case_id, pack_hash(PACK), "rev", CTX.now, special_category_acknowledged=True
    )
    assert Machine(CTX).advance(c.model_copy(update={"approval": ack}), S.APPROVED).state is S.APPROVED


def test_excessive_request_needs_a_person() -> None:
    m = Machine(CTX)
    c = m.advance(Case.open("case-4", date(2026, 9, 1)), S.INTAKE_DONE)
    c = c.model_copy(update={"flags": c.flags.model_copy(update={"possibly_excessive": True})})
    with pytest.raises(TransitionRefused, match="R-EXCESSIVE"):
        m.advance(c, S.IDENTITY_CHECK)


def test_extension_must_be_announced_in_time() -> None:
    c = ready_case().model_copy(update={"state": S.IDENTITY_VERIFIED, "extension_reason": "high volume"})
    late = Machine(GuardContext(now=datetime(2026, 10, 15), approval_secret=SECRET, policy_version="1.0.0"))
    with pytest.raises(TransitionRefused, match="first month"):
        late.advance(c, S.EXTENDED)
    assert Machine(CTX).advance(c, S.EXTENDED).state is S.EXTENDED


def test_shadow_mode_records_instead_of_blocking() -> None:
    m = Machine(CTX, shadow=True)
    c = m.advance(ready_case(), S.APPROVED)  # no approval: would be refused
    assert c.state is S.APPROVED
    assert [f.rule_id for f in m.shadow_log] == ["R-RELEASE-APPROVAL"]


def test_terminal_state_has_no_exits() -> None:
    assert ALLOWED[S.CLOSED] == frozenset()
    assert set(ALLOWED) == set(CaseState)


def test_intake_channel_enums_exist() -> None:
    assert Channel.EMAIL and Language.FR  # schemas importable and stable


def test_validated_needs_the_claims_check_and_an_electronic_copy() -> None:
    c = ready_case()
    m = Machine(CTX)
    draft = next(t for t in c.history if t.to_state is S.DRAFT_READY)
    at_draft = c.model_copy(
        update={"state": S.DRAFT_READY, "history": c.history[: c.history.index(draft) + 1]}
    )
    with pytest.raises(TransitionRefused, match="R-VALIDATION: the claims cross-check found: wrong count"):
        m.advance(at_draft.model_copy(update={"claims_problems": ["wrong count"]}), S.VALIDATED)
    with pytest.raises(TransitionRefused, match="R-COPY"):
        m.advance(at_draft.model_copy(update={"copy_format": "application/x-paper"}), S.VALIDATED)
