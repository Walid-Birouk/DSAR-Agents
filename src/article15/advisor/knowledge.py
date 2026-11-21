"""The advisor's GDPR knowledge: short passages with their sources (PLAN.md Appendix A).

Every passage quotes the regulation or the EDPB guidelines, as checked when the plan was written
(EUR-Lex, OJ L 119, 4.5.2016; EDPB Guidelines 01/2022 on the right of access, version 2.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from article15.advisor.models import Question


@dataclass(frozen=True)
class Passage:
    id: str
    source: str
    text: str


PASSAGES: tuple[Passage, ...] = (
    Passage(
        "art12_3",
        "GDPR Art. 12(3)",
        'Answer "without undue delay and in any event within one month of receipt of the request". The '
        "period "
        '"may be extended by two further months where necessary, taking into account the complexity and '
        "number "
        'of the requests"; the requester is told "within one month of receipt", "together with the reasons '
        "for "
        'the delay".',
    ),
    Passage(
        "art12_5",
        "GDPR Art. 12(5)",
        'Information is provided "free of charge". Where requests are "manifestly unfounded or excessive", '
        '"in '
        'particular because of their repetitive character", the controller may charge a reasonable fee or '
        "refuse "
        'to act, and "shall bear the burden of demonstrating" it.',
    ),
    Passage(
        "edpb_excessive",
        "EDPB Guidelines 01/2022 v2.1",
        'The concepts of manifestly unfounded and excessive requests "have to be interpreted narrowly".',
    ),
    Passage(
        "art12_4",
        "GDPR Art. 12(4)",
        'If no action is taken, inform the requester "without delay and at the latest within one month", '
        "with the "
        'reasons and "the possibility of lodging a complaint with a supervisory authority and seeking a '
        "judicial "
        'remedy".',
    ),
    Passage(
        "art9_1",
        "GDPR Art. 9(1)",
        "Special categories: racial or ethnic origin, political opinions, religious or philosophical "
        "beliefs, "
        "trade union membership, genetic data, biometric data for unique identification, health, sex life or "
        "sexual orientation.",
    ),
    Passage(
        "art15_4",
        "GDPR Art. 15(4); EDPB Guidelines 01/2022 v2.1",
        'The copy "shall not adversely affect the rights and freedoms of others"; this "should not result in '
        'refusing the data subject\'s request altogether", only "leaving out or rendering illegible" the '
        "parts "
        "concerned.",
    ),
)
BY_ID = {p.id: p for p in PASSAGES}
FOR_QUESTION: dict[Question, tuple[str, ...]] = {
    Question.EXCESSIVE: ("art12_5", "edpb_excessive", "art12_4"),
    Question.EXTENSION: ("art12_3",),
    Question.SPECIAL_CATEGORY: ("art9_1", "art15_4"),
    Question.OTHER_RIGHTS: ("art12_3", "art12_4"),
}


def passages_for(question: Question) -> list[Passage]:
    return [BY_ID[i] for i in FOR_QUESTION[question]]
