"""Ferlane's privacy register: the Article 15(1) information every reply must give (see register.yaml)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from article15.schemas import Art15Item, Language, System

REGISTER_PATH = Path(__file__).with_name("register.yaml")


class LegalBasis(StrEnum):
    CONTRACT = "contract"
    LEGAL_OBLIGATION = "legal_obligation"
    CONSENT = "consent"
    LEGITIMATE_INTEREST = "legitimate_interest"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Text(_Model):
    source: str
    fr: str
    en: str

    @field_validator("source", "fr", "en")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return " ".join(v.split())

    def get(self, language: Language) -> str:
        return self.fr if language is Language.FR else self.en


class Bilingual(_Model):
    fr: str
    en: str

    def get(self, language: Language) -> str:
        return self.fr if language is Language.FR else self.en


class Purpose(Text):
    id: str
    legal_basis: LegalBasis
    retention: Text


class Category(Text):
    id: str
    systems: list[System] = Field(min_length=1)


class Recipient(Text):
    id: str


class Controller(_Model):
    name: str
    privacy_contact: str


class Register(_Model):
    version: int
    controller: Controller
    legal_bases: dict[LegalBasis, Bilingual]
    purposes: list[Purpose] = Field(min_length=1)
    categories: list[Category] = Field(min_length=1)
    recipients: list[Recipient] = Field(min_length=1)
    transfers: Text
    automated_decisions: Text
    data_source: Text
    rights: Text
    complaint: Text

    def covers(self) -> set[Art15Item]:
        """The Art. 15 items this register can answer (all of them, or loading fails)."""
        return set(ART15_SECTIONS)

    def render(self) -> str:
        """The COMPANY REGISTER section of a prompt, in French and English.

        Deterministic, so it can sit in the cached prefix."""
        lines = [
            f"COMPANY REGISTER v{self.version} — {self.controller.name}'s processing, for the Article 15 "
            "information in the letter. Use the wording of the requester's language. "
            f"Privacy contact: {self.controller.privacy_contact}."
        ]
        for language in (Language.FR, Language.EN):
            lines.append(f"\n[{language.value}]")
            lines.append(f"{_label(Art15Item.PURPOSES)} and {_label(Art15Item.RETENTION)}:")
            for p in self.purposes:
                basis = self.legal_bases[p.legal_basis].get(language)
                lines.append(f"- {p.get(language)} ({basis}). Retention: {p.retention.get(language)}")
            lines.append(f"{_label(Art15Item.CATEGORIES)}:")
            lines += [f"- {c.get(language)}" for c in self.categories]
            lines.append(f"{_label(Art15Item.RECIPIENTS)}:")
            lines += [f"- {r.get(language)}" for r in self.recipients]
            for item, text in (
                (Art15Item.RIGHTS, self.rights),
                (Art15Item.COMPLAINT, self.complaint),
                (Art15Item.SOURCE, self.data_source),
                (Art15Item.AUTOMATED_DECISIONS, self.automated_decisions),
                (Art15Item.TRANSFER_SAFEGUARDS, self.transfers),
            ):
                lines.append(f"{_label(item)}: {text.get(language)}")
        return "\n".join(lines)


ART15_SECTIONS: dict[Art15Item, str] = {
    Art15Item.PURPOSES: "purposes",
    Art15Item.CATEGORIES: "categories",
    Art15Item.RECIPIENTS: "recipients",
    Art15Item.RETENTION: "purposes[].retention",
    Art15Item.RIGHTS: "rights",
    Art15Item.COMPLAINT: "complaint",
    Art15Item.SOURCE: "data_source",
    Art15Item.AUTOMATED_DECISIONS: "automated_decisions",
    Art15Item.TRANSFER_SAFEGUARDS: "transfers",
}
_ART15_LABELS: dict[Art15Item, str] = {
    Art15Item.PURPOSES: "Art. 15(1)(a) purposes",
    Art15Item.CATEGORIES: "Art. 15(1)(b) categories of data",
    Art15Item.RECIPIENTS: "Art. 15(1)(c) recipients",
    Art15Item.RETENTION: "Art. 15(1)(d) retention",
    Art15Item.RIGHTS: "Art. 15(1)(e) rights",
    Art15Item.COMPLAINT: "Art. 15(1)(f) complaint",
    Art15Item.SOURCE: "Art. 15(1)(g) source",
    Art15Item.AUTOMATED_DECISIONS: "Art. 15(1)(h) automated decisions",
    Art15Item.TRANSFER_SAFEGUARDS: "Art. 15(2) transfers outside the EEA",
}


def _label(item: Art15Item) -> str:
    return f"{_ART15_LABELS[item]} [{item.value}]"


def load_register(path: Path = REGISTER_PATH) -> Register:
    register = Register.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    missing = set(LegalBasis) - set(register.legal_bases)
    if missing:
        raise ValueError(f"legal bases without wording: {sorted(missing)}")
    if set(ART15_SECTIONS) != set(Art15Item):
        raise ValueError("every Art. 15 item needs a register section")
    return register
