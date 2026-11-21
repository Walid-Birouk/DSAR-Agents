"""What crosses the boundary to the DPO advisor, and what comes back (PLAN.md 5.4, rule R-MINIMISE-EGRESS).

The question is a typed, minimised case summary: an enum, counts, dates and flags. There is no free-text field
at all, so no name, address or record text can be put into it; and code, not a model, fills it in from the
case file. The egress filter (egress.py) checks the serialised payload once more before it leaves.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from article15.schemas import RequestType, Strict


class Question(StrEnum):
    EXCESSIVE = "possibly_excessive"  # repeated or manifestly unfounded (Art. 12(5))
    EXTENSION = "extension"  # complex or numerous: may the deadline be extended? (Art. 12(3))
    SPECIAL_CATEGORY = "special_category"  # health or other Art. 9(1) data in the copy
    OTHER_RIGHTS = "other_rights"  # erasure, rectification... alongside access


class CaseSummary(Strict):
    question: Question
    received_on: date
    due: date
    request_types: list[RequestType]
    earlier_requests_last_12_months: int = Field(ge=0)
    days_since_last_request: int | None = Field(default=None, ge=0)
    records_found: int = Field(ge=0)
    systems_with_records: int = Field(ge=0, le=4)
    special_category_mentions: int = Field(ge=0)
    on_behalf_of_someone_else: bool = False


class Recommendation(StrEnum):
    PROCEED = "proceed"  # no ground to treat the request differently
    PERSON_DECIDES = "person_decides"  # a ground may exist; a person must decide, with reasons recorded
    EXTENSION_JUSTIFIED = "extension_justified"
    FLAG_FOR_REVIEW = "flag_for_review"


class Advice(Strict):
    recommendation: Recommendation
    reasons: list[str] = Field(min_length=1)
    sources: list[str] = Field(min_length=1)  # the legal passages relied on (knowledge.py IDs)
    advisor: str = "rules"  # "rules" (deterministic) or the model that wrote it
