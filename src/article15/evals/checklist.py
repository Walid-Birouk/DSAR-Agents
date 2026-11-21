"""The Article 15(1) checklist: does the letter actually give each piece of information? (PLAN.md 8.3)

The letter is written by a model, so the check looks for the facts, not for exact sentences. Each item lists
facts; each fact is a set of alternative patterns, and a fact is present when one of them matches. An item is
present when all of its facts are. The facts are the ones in Ferlane's register (company/register.yaml): every
purpose, every category, every recipient category, every retention period, the four rights of Art. 15(1)(e),
the CNIL complaint, the source, automated decisions, and transfers outside the EEA.

Matching ignores case, accents and the kind of apostrophe. The letter's own `items_covered` claim is not
trusted; it is compared with what the text shows.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from article15.schemas import Art15Item, Language

A = Art15Item
Fact = tuple[str, ...]  # alternative regular expressions, matched on normalised text

FACTS: dict[Language, dict[Art15Item, dict[str, Fact]]] = {
    Language.FR: {
        A.PURPOSES: {
            "orders": (r"commande",),
            "accounting": (r"comptab",),
            "account": (r"compte client", r"votre compte"),
            "customer_service": (r"service client", r"reclamation", r"apres-vente"),
            "prospecting": (r"lettre d'information", r"prospection", r"offres par sms"),
            "partner_offers": (r"partenaires?",),
            "audience_measurement": (r"mesure d'audience",),
            "rights_requests": (r"exercice (de|des|de vos) droits", r"demandes? d'exercice"),
        },
        A.CATEGORIES: {
            "identity": (r"identite", r"coordonnees"),
            "preferences": (r"choix de communication", r"preferences", r"consentements?"),
            "rights_requests": (r"demandes anterieures", r"demandes precedentes"),
            "orders": (r"commandes?",),
            "customer_service": (r"service client", r"echanges"),
            "browsing": (r"navigation", r"pages consultees"),
        },
        A.RECIPIENTS: {
            "internal": (r"services internes", r"interne"),
            "processors": (r"sous-traitants?",),
            "partners": (r"partenaires?",),
        },
        A.RETENTION: {
            "10 years": (r"\b10 ans\b", r"\bdix ans\b"),
            "2 years": (r"\b2 ans\b", r"\bdeux ans\b"),
            "3 years": (r"\b3 ans\b", r"\btrois ans\b"),
            "13 months": (r"\b13 mois\b", r"\btreize mois\b"),
            "25 months": (r"\b25 mois\b", r"\bvingt-cinq mois\b"),
        },
        A.RIGHTS: {
            "rectification": (r"rectifi",),
            "erasure": (r"effac", r"suppression de vos donnees"),
            "restriction": (r"limitation",),
            "objection": (r"vous opposer", r"opposition"),
        },
        A.COMPLAINT: {
            "authority": (r"\bcnil\b",),
            "complaint": (r"reclamation", r"plainte"),
        },
        A.SOURCE: {
            "source": (r"viennent de vous", r"proviennent", r"origine", r"source"),
        },
        A.AUTOMATED_DECISIONS: {
            "automated": (r"automatis",),
        },
        A.TRANSFER_SAFEGUARDS: {
            "eea": (r"espace economique europeen", r"\beee\b", r"en dehors de l'union europeenne"),
        },
    },
    Language.EN: {
        A.PURPOSES: {
            "orders": (r"\borders?\b",),
            "accounting": (r"\baccounting\b", r"\bthe accounts\b", r"\baccounts\b"),
            "account": (r"customer account", r"your account"),
            "customer_service": (r"customer (service|care)", r"complaints?", r"after-sales"),
            "prospecting": (r"newsletter", r"marketing", r"sms offers"),
            "partner_offers": (r"partners?",),
            "audience_measurement": (r"audience measurement",),
            "rights_requests": (r"exercise your rights", r"requests? to exercise", r"rights requests?"),
        },
        A.CATEGORIES: {
            "identity": (r"identity", r"contact details"),
            "preferences": (r"communication choices", r"preferences", r"consents?"),
            "rights_requests": (r"earlier requests", r"previous requests", r"past requests"),
            "orders": (r"\borders?\b",),
            "customer_service": (r"customer (service|care)", r"tickets?", r"exchanges"),
            "browsing": (r"brows", r"use of the website", r"pages viewed"),
        },
        A.RECIPIENTS: {
            "internal": (r"internal",),
            "processors": (r"processors?",),
            "partners": (r"partners?",),
        },
        A.RETENTION: {
            "10 years": (r"\b10 years\b", r"\bten years\b"),
            "2 years": (r"\b2 years\b", r"\btwo years\b"),
            "3 years": (r"\b3 years\b", r"\bthree years\b"),
            "13 months": (r"\b13 months\b", r"\bthirteen months\b"),
            "25 months": (r"\b25 months\b", r"\btwenty-five months\b"),
        },
        A.RIGHTS: {
            "rectification": (r"rectif",),
            "erasure": (r"\beras", r"delet"),
            "restriction": (r"restrict",),
            "objection": (r"\bobject\b", r"objection"),
        },
        A.COMPLAINT: {
            "authority": (r"\bcnil\b",),
            "complaint": (r"complaint",),
        },
        A.SOURCE: {
            "source": (r"comes? from you", r"\bsource\b", r"\borigin\b", r"obtained from"),
        },
        A.AUTOMATED_DECISIONS: {
            "automated": (r"automated",),
        },
        A.TRANSFER_SAFEGUARDS: {
            "eea": (r"european economic area", r"\beea\b"),
        },
    },
}


_PUNCTUATION = str.maketrans({"\u2019": "'", "\u2018": "'", "\u00a0": " ", "\u202f": " "})


def normalise(text: str) -> str:
    """Lower case, no accents, straight apostrophes, single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(_PUNCTUATION)
    return re.sub(r"\s+", " ", text.lower())


@dataclass(frozen=True)
class ChecklistResult:
    present: frozenset[Art15Item]
    missing: dict[Art15Item, list[str]]  # item -> the facts not found
    claimed_not_present: frozenset[Art15Item]  # in items_covered, but the text does not show it

    @property
    def complete(self) -> bool:
        return not self.missing


def check_letter(body: str, language: Language, claimed: list[Art15Item] | None = None) -> ChecklistResult:
    text = normalise(body)
    missing: dict[Art15Item, list[str]] = {}
    for item, facts in FACTS[language].items():
        absent = [name for name, alts in facts.items() if not any(re.search(p, text) for p in alts)]
        if absent:
            missing[item] = absent
    present = frozenset(set(Art15Item) - set(missing))
    return ChecklistResult(
        present=present,
        missing=missing,
        claimed_not_present=frozenset(set(claimed or ()) - present),
    )
