"""The simulated requester: answers the desk's follow-up questions in runs and evals (decision 0010).

Replies come from fixed French and English templates, driven by what the generator says the requester knows
(`RequesterProfile`). They are deterministic (the variant is chosen from a hash of the case ID and the
round), free, and identical on every run, so replays match and eval labels stay exact:
* a genuine customer gives the order number and postcode they know;
* an impostor knows at most the postcode (never an order of the person they claim to be) and signs with the
  claimed name;
* a representative has no written mandate;
* someone without an account has nothing to give.
The question's wording is not interpreted: the requester always offers what they have.
"""

from __future__ import annotations

import hashlib

from article15.company.models import CaseKind, CaseTruth, Person
from article15.schemas import Language

_GIVE_BOTH = {
    Language.FR: (
        "Bonjour,\nBien sûr : ma commande porte le numéro {ref} et le code postal de livraison est {pc}.\n"
        "Cordialement,\n{name}",
        "Bonjour,\nVoici les informations demandées : commande {ref}, code postal {pc}.\nMerci,\n{name}",
    ),
    Language.EN: (
        "Hello,\nOf course: my order number is {ref} and the delivery postcode is {pc}.\n"
        "Kind regards,\n{name}",
        "Hi,\nHere is what you asked for: order {ref}, postcode {pc}.\nThanks,\n{name}",
    ),
}
_POSTCODE_ONLY = {
    Language.FR: (
        "Bonjour,\nJe n'ai plus le numéro de commande, mais mon code postal est {pc}. Merci de m'envoyer mes "
        "données rapidement.\n{name}",
        "Bonjour,\nJe ne retrouve pas de numéro de commande. Mon code postal : {pc}. C'est urgent.\n{name}",
    ),
    Language.EN: (
        "Hello,\nI no longer have the order number, but my postcode is {pc}. Please send my data quickly.\n"
        "{name}",
        "Hi,\nI can't find an order number. My postcode is {pc}. This is urgent.\n{name}",
    ),
}
_NOTHING = {
    Language.FR: (
        "Bonjour,\nJe n'ai pas ces informations sous la main. Merci de m'envoyer les données quand "
        "même.\n{name}",
        "Bonjour,\nJe n'ai aucun numéro de commande à vous donner.\n{name}",
    ),
    Language.EN: (
        "Hello,\nI don't have that information to hand. Please send the data anyway.\n{name}",
        "Hi,\nI have no order number to give you.\n{name}",
    ),
}
_NO_MANDATE = {
    Language.FR: "Je n'ai pas de procuration écrite, mais je m'occupe de ses démarches.",
    Language.EN: "I don't have a written authorisation, but I handle these matters for them.",
}
_NO_ACCOUNT = {
    Language.FR: "Je ne crois pas avoir déjà passé commande chez vous.",
    Language.EN: "I don't think I have ever ordered from you.",
}


def _variant(case_id: str, round_: int, n: int) -> int:
    digest = hashlib.sha256(f"{case_id}|{round_}".encode()).digest()
    return digest[0] % n


def reply(truth: CaseTruth, people: dict[str, Person], round_: int) -> str:
    """The requester's answer to the desk's `round_`-th follow-up question (1-based)."""
    profile = truth.requester
    language = truth.language
    genuine = truth.subject_id is not None and profile.person_id == truth.subject_id
    own_name = people[profile.person_id].name
    claimed = people[truth.subject_id].name if truth.subject_id and truth.subject_id in people else own_name
    impostor = (
        not genuine and truth.kind is not CaseKind.IDENTITY_REPRESENTATIVE and truth.subject_id is not None
    )
    name = claimed if impostor else own_name
    ref, pc = profile.knows_order_reference, profile.knows_postcode
    if genuine and ref and pc:
        templates = _GIVE_BOTH[language]
    elif pc:
        templates = _POSTCODE_ONLY[language]
    else:
        templates = _NOTHING[language]
    text = templates[_variant(truth.case_id, round_, len(templates))].format(ref=ref, pc=pc, name=name)
    extra = []
    if truth.kind is CaseKind.IDENTITY_REPRESENTATIVE and not profile.has_mandate:
        extra.append(_NO_MANDATE[language])
    if truth.subject_id is None:
        extra.append(_NO_ACCOUNT[language])
    if extra:
        head, _, tail = text.partition("\n")
        first_line, _, rest = tail.partition("\n")
        text = "\n".join([head, first_line + " " + " ".join(extra), rest])
    return text


_LINK_CODES = {
    Language.FR: "Bonjour,\nOui, cette adresse est bien à moi. Voici le code reçu : {codes}.\n{name}",
    Language.EN: "Hello,\nYes, that address is mine. Here is the code I received: {codes}.\n{name}",
}
_LINK_UNKNOWN = {
    Language.FR: "Bonjour,\nJe n'ai pas accès à cette adresse, je n'ai reçu aucun code.\n{name}",
    Language.EN: "Hello,\nI have no access to that address, so I received no code.\n{name}",
}


def link_reply(truth: CaseTruth, people: dict[str, Person], codes: list[str]) -> str:
    """The requester's answer when asked to send back the codes mailed to other addresses: the codes they
    could read (only from mailboxes they control), or that they received none."""
    name = people[truth.requester.person_id].name
    if codes:
        return _LINK_CODES[truth.language].format(codes=", ".join(codes), name=name)
    return _LINK_UNKNOWN[truth.language].format(name=name)
