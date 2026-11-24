"""Decision 0018: the Art. 12(4) refusal letter, the letter's own-approval check, the PDF copy, the redaction
hint, and the tools that bring recordings up to date after a code change (re-baseline, continue)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import anyio
import pytest

from article15.case.machine import GuardContext, Machine, TransitionRefused
from article15.case.models import Case, CaseState, Notice
from article15.company.register import load_register
from article15.core.model import ModelRequest, ModelResponse
from article15.core.replay import ContinuingProvider, RecordingProvider, ReplayProvider, trim_cassette
from article15.desk.checklist import normalise
from article15.desk.claims import OWN_APPROVAL
from article15.desk.pdf import pack_pdf
from article15.desk.redaction import _where
from article15.desk.refusal import RefusalKind, build_refusal
from article15.schemas import IdentityDecision, IdentityOutcome, IntakeResult, Language, System

S = CaseState


def _case(on_behalf: bool = False, verified: bool = False) -> Case:
    case = Case.open("A15-900", date(2026, 3, 2))
    intake = IntakeResult.model_validate(
        {
            "ok": True,
            "received_on": "2026-03-02",
            "channel": "email",
            "language": "fr",
            "request_types": ["access"],
            "on_behalf_of_someone_else": on_behalf,
        }
    )
    identity = IdentityDecision.model_validate(
        {
            "ok": True,
            "outcome": "VERIFIED",
            "subject_id": "C0001",
            "confidence": 0.9,
            "evidence": [{"kind": "k", "detail": "d"}],
        }
        if verified
        else {
            "ok": True,
            "outcome": "REJECTED",
            "confidence": 0.9,
            "rejection_reason": "account C0001 exists, sender unknown",
        }
    )
    return case.model_copy(update={"intake": intake, "identity": identity})


# --- the refusal letter ---------------------------------------------------------------------------------


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize(
    ("on_behalf", "verified", "kind"),
    [
        (False, False, RefusalKind.IDENTITY),
        (True, False, RefusalKind.NO_MANDATE),
        (False, True, RefusalKind.REVIEWER),
    ],
)
def test_every_refusal_letter_carries_what_article_12_4_requires(
    language: Language, on_behalf: bool, verified: bool, kind: RefusalKind
) -> None:
    case = _case(on_behalf, verified)
    notice = build_refusal(
        case, load_register(), language, by_reviewer=verified, reason="Une raison écrite pour la personne."
    )
    assert notice.kind == kind.value and notice.explains_refusal()
    assert "CNIL" in notice.body and "3 Place de Fontenoy" in notice.body
    # The model's own reason ("account C0001 exists") never reaches a requester who may be an impostor.
    assert "C0001" not in notice.body


def test_a_reviewer_refusal_needs_a_reason_for_the_requester() -> None:
    with pytest.raises(ValueError):
        build_refusal(_case(verified=True), load_register(), Language.FR, by_reviewer=True, reason="  ")


def test_the_rejection_guard_refuses_without_the_letter_and_with_a_letter_that_omits_the_remedy() -> None:
    machine = Machine(
        GuardContext(now=datetime(2026, 3, 3, tzinfo=UTC), approval_secret=b"x" * 32, policy_version="1")
    )
    case = _case().model_copy(update={"state": S.IDENTITY_CHECK, "rejection_reason": "not established"})
    with pytest.raises(TransitionRefused, match=r"Art\. 12\(4\) letter"):
        machine.advance(case, S.REJECTED)
    weak = Notice(
        kind="x", language=Language.EN, subject_line="s", body="We cannot act on it. Contact the CNIL."
    )
    with pytest.raises(TransitionRefused):
        machine.advance(case.model_copy(update={"refusal": weak}), S.REJECTED)
    good = build_refusal(case, load_register(), Language.EN)
    assert machine.advance(case.model_copy(update={"refusal": good}), S.REJECTED).state is S.REJECTED


# --- the letter must not speak of its own approval ------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Cette réponse vous est adressée sous réserve de la validation d'une personne habilitée avant envoi.",
        "Elle sera examinée par une personne habilitée avant l'envoi de cette réponse.",
        "Elles sont soumises à la vérification d'une personne habilitée avant l'envoi de cette réponse.",
        "This reply will be reviewed by our team before it is sent.",
        "This draft letter is pending approval.",
    ],
)
def test_the_claims_check_catches_a_letter_that_describes_a_review_still_to_come(sentence: str) -> None:
    assert OWN_APPROVAL.search(normalise(sentence))


@pytest.mark.parametrize(
    "sentence",
    [
        # true when the letter is sent: what was done before sending
        "Les données d'autres personnes ont été masquées avant l'envoi de la copie.",
        "Nous ne prenons aucune décision fondée exclusivement sur un traitement automatisé.",
        "You may ask us to review this decision.",
    ],
)
def test_the_claims_check_leaves_true_sentences_alone(sentence: str) -> None:
    assert not OWN_APPROVAL.search(normalise(sentence))


# --- the PDF copy ---------------------------------------------------------------------------------------


def _pack() -> dict:  # type: ignore[type-arg]
    return {
        "case_id": "A15-900",
        "language": "fr",
        "letter": {
            "subject_line": "Votre demande (art. 15)",
            "body": "Bonjour,\nVoici la copie — 2 éléments (œuvre).\n",
        },
        "record_counts": {"crm": 1, "helpdesk": 1},
        "records": [
            {"system": "crm", "table": "customers", "record_id": "C0001", "fields": {"first_name": "Élodie"}},
            {
                "system": "helpdesk",
                "table": "messages",
                "record_id": "M000001",
                "fields": {"body": "Texte (avec parenthèses) et \\ barre " + "long " * 80, "note": None},
            },
        ],
        "redactions": [],
        "special_category": [],
    }


def test_the_pdf_is_valid_deterministic_and_tied_to_the_pack_hash() -> None:
    from article15.desk.pack import hash_pack

    pack = _pack()
    pdf = pack_pdf(pack)
    assert pdf == pack_pdf(pack)
    assert pdf.startswith(b"%PDF-1.4") and pdf.endswith(b"%%EOF\n")
    # every cross-reference offset points at the object it names
    start = int(re.search(rb"startxref\n(\d+)", pdf).group(1))  # type: ignore[union-attr]
    table = pdf[start:].split(b"trailer")[0].splitlines()[3:]
    for n, row in enumerate(table, start=1):
        offset = int(row.split()[0])
        assert pdf[offset:].startswith(f"{n} 0 obj".encode()), n
    assert hash_pack(pack).encode() in pdf
    assert b"\\(avec parenth" in pdf  # parentheses escaped
    assert b"\\311lodie" in pdf  # "É" in WinAnsiEncoding (octal 311)


# --- the redaction hint ---------------------------------------------------------------------------------


def test_a_wrong_redaction_address_names_where_the_text_is() -> None:
    fields = {
        (System.HELPDESK, "M1", "body"): "x",
        (System.HELPDESK, "M1", "ticket_id"): "T1",
        (System.HELPDESK, "M2", "body"): "y",
        (System.HELPDESK, "M2", "ticket_id"): "T1",
    }
    assert "use record_id M1, M2 with field 'body'" in _where(fields, (System.HELPDESK, "T1", "body"))
    assert "its text fields are: body" in _where(fields, (System.HELPDESK, "M1", "text"))
    assert "not in the pack" in _where(fields, (System.CRM, "C9", "note"))


# --- bringing recordings up to date -----------------------------------------------------------------------


class _Echo:
    name = "live"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(provider="live", model="m", content=f"live {self.calls}", finish_reason="stop")


def _req(*texts: str) -> ModelRequest:
    return ModelRequest(model="m", messages=[{"role": "user", "content": t} for t in texts])


def test_continue_replays_the_unchanged_prefix_then_goes_live(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    first = _Echo()
    rec = RecordingProvider(first, path, label="t")
    for r in (_req("a"), _req("a", "b"), _req("a", "b", "c")):
        anyio.run(rec.complete, r)
    live = _Echo()
    cont = ContinuingProvider(path, live)
    assert anyio.run(cont.complete, _req("a")).content == "live 1"  # the recorded answer, no live call
    assert anyio.run(cont.complete, _req("a", "B")).content == "live 1"  # changed: goes live
    assert anyio.run(cont.complete, _req("a", "b", "c")).content == "live 2"  # and stays live
    assert live.calls == 2 and cont.live_from == 1


def test_trimming_keeps_a_cassette_that_replays_its_first_calls(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    rec = RecordingProvider(_Echo(), path, label="t")
    for r in (_req("a"), _req("a", "b"), _req("a", "b", "c")):
        anyio.run(rec.complete, r)
    assert trim_cassette(path, 2) == 1
    replay = ReplayProvider(path)
    assert anyio.run(replay.complete, _req("a")).content == "live 1"
    assert anyio.run(replay.complete, _req("a", "b")).content == "live 2"
    assert replay.unused() == 0 and replay.used_prefix() == 2
    assert IdentityOutcome.VERIFIED  # (keeps the import used)
