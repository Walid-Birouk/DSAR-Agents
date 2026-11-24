"""The refusal letter: what the requester is told when the desk does not act on a request (GDPR Art. 12(4)).

Article 12(4) says that a controller who does not act on a request must tell the requester, within one month,
why, and that they may complain to a supervisory authority and seek a judicial remedy. The letter is built by
code from fixed French and English texts and the company register, never written by a model, for two reasons:
- **It may go to an impostor.** The model's own reasons often say whether the claimed person is a customer
  ("the address is registered on account C0178"). This letter never confirms or denies that an account exists.
  It says what was missing and how to go ahead.
- **It must always carry the Art. 12(4) facts.** Guard `case.rejection_explained` refuses REJECTED unless a
  letter with them exists.

The reason is chosen from what code knows, not from the model's wording:
- the requester writes for someone else (intake's `on_behalf_of_someone_else`): a written mandate is missing;
- identity was not established: how to prove it;
- a reviewer refused a verified request: the reviewer's own reason, written for the requester.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from article15.case.models import Case, Notice
from article15.company.register import Register
from article15.schemas import IdentityOutcome, Language


class RefusalKind(StrEnum):
    NO_MANDATE = "no_mandate"
    IDENTITY = "identity_not_established"
    REVIEWER = "reviewer_decision"


SUBJECT = {
    Language.FR: "Votre demande d'accès à vos données ({case_id})",
    Language.EN: "Your request for access to your data ({case_id})",
}

OPENING = {
    Language.FR: "Bonjour,\n\nNous avons bien reçu le {received} votre demande d'accès aux données "
    "personnelles "
    "(article 15 du RGPD). Nous ne pouvons pas y donner suite en l'état, pour la raison suivante.",
    Language.EN: "Hello,\n\nWe received your request for access to personal data (GDPR Article 15) on "
    "{received}. We cannot act on it as it stands, for the following reason.",
}

REASON = {
    RefusalKind.NO_MANDATE: {
        Language.FR: "Vous écrivez pour le compte d'une autre personne, et nous n'avons pas reçu "
        "d'autorisation écrite de sa part. Pour protéger ses données, nous ne pouvons les communiquer qu'à "
        "elle-même ou à une "
        "personne qu'elle a mandatée par écrit.",
        Language.EN: "You are writing on behalf of another person, and we have not received a written "
        "authorisation from them. To protect their data, we can only send it to them or to someone they have "
        "authorised in writing.",
    },
    RefusalKind.IDENTITY: {
        Language.FR: "Les éléments reçus ne nous permettent pas de confirmer que vous êtes la personne "
        "concernée par les données demandées. Pour protéger les données de nos clients, nous ne communiquons "
        "une copie qu'une fois l'identité confirmée.",
        Language.EN: "The information we received does not let us confirm that you are the person the data "
        "is about. To protect our customers' data, we only send a copy once that is confirmed.",
    },
    RefusalKind.REVIEWER: {
        Language.FR: "{reason}",
        Language.EN: "{reason}",
    },
}

HOW_TO_PROCEED = {
    RefusalKind.NO_MANDATE: {
        Language.FR: "Pour aller plus loin, la personne concernée peut nous écrire elle-même depuis "
        "l'adresse "
        "e-mail de son compte, ou vous remettre une autorisation écrite et signée que vous nous transmettrez "
        "avec une nouvelle demande.",
        Language.EN: "To go ahead, the person concerned can write to us from their account's email address, "
        "or give you a signed written authorisation to send us with a new request.",
    },
    RefusalKind.IDENTITY: {
        Language.FR: "Pour aller plus loin, écrivez-nous depuis l'adresse e-mail enregistrée sur votre "
        "compte, "
        "ou indiquez-nous le numéro d'une de vos commandes avec son code postal de livraison. Nous ne vous "
        "demanderons pas de copie de pièce d'identité.",
        Language.EN: "To go ahead, write to us from the email address registered on your account, or give us "
        "the number of one of your orders with its delivery postcode. We will not ask for a copy of an "
        "identity "
        "document.",
    },
    RefusalKind.REVIEWER: {
        Language.FR: "Si vous pensez que cette décision est erronée, répondez simplement à ce message.",
        Language.EN: "If you think this decision is wrong, simply reply to this message.",
    },
}

REMEDY = {
    Language.FR: "{complaint} Vous pouvez également former un recours devant les juridictions compétentes "
    "(article 79 du RGPD).",
    Language.EN: "{complaint} You may also seek a judicial remedy before the competent courts (GDPR Article "
    "79).",
}

CLOSING = {
    Language.FR: "Cordialement,\nLe service protection des données de {controller}\n{contact}",
    Language.EN: "Kind regards,\n{controller}'s data protection desk\n{contact}",
}


def refusal_kind(case: Case, by_reviewer: bool) -> RefusalKind:
    if by_reviewer and case.identity is not None and case.identity.outcome is IdentityOutcome.VERIFIED:
        return RefusalKind.REVIEWER
    if case.intake is not None and case.intake.on_behalf_of_someone_else:
        return RefusalKind.NO_MANDATE
    return RefusalKind.IDENTITY


def _spoken(d: date, language: Language) -> str:
    return d.strftime("%d/%m/%Y") if language is Language.FR else d.isoformat()


def build_refusal(
    case: Case, register: Register, language: Language, *, by_reviewer: bool = False, reason: str = ""
) -> Notice:
    """The Art. 12(4) letter for `case`. `reason` is used only for a reviewer's refusal of a verified request
    (it is written by a person, for the requester)."""
    kind = refusal_kind(case, by_reviewer)
    if kind is RefusalKind.REVIEWER and not reason.strip():
        raise ValueError("a reviewer's refusal needs a reason written for the requester")
    controller = register.controller
    parts = [
        OPENING[language].format(received=_spoken(case.received_on, language)),
        REASON[kind][language].format(reason=reason.strip()),
        HOW_TO_PROCEED[kind][language],
        REMEDY[language].format(complaint=register.complaint.get(language)),
        CLOSING[language].format(controller=controller.name, contact=controller.privacy_contact),
    ]
    return Notice(
        kind=kind.value,
        language=language,
        subject_line=SUBJECT[language].format(case_id=case.case_id),
        body="\n\n".join(parts),
    )
