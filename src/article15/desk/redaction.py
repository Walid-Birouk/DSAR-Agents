"""Redaction: the agent quotes, code locates, applies and verifies (decision 0010).

The agent points at text by quoting it exactly (with an optional occurrence number), because models are poor
at counting characters. Code finds the offsets, rejects quotes that are not in the record, masks the spans,
and then verifies that no redacted text is still readable anywhere in the pack. The verifier is what lets the
case pass guard `case.redaction_verified` (rule R-OTHERS).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import Field

from article15.schemas import (
    Language,
    RedactionCategory,
    RedactionPlan,
    RedactionSpan,
    SpecialCategory,
    SpecialCategoryFlag,
    Strict,
    System,
)

FieldKey = tuple[System, str, str]  # (system, record_id, field)

MARKERS: dict[Language, dict[RedactionCategory, str]] = {
    Language.FR: {
        RedactionCategory.THIRD_PARTY: "[masqué : tiers]",
        RedactionCategory.STAFF: "[masqué : personnel]",
    },
    Language.EN: {
        RedactionCategory.THIRD_PARTY: "[redacted: third party]",
        RedactionCategory.STAFF: "[redacted: staff]",
    },
}
MAX_ERRORS = 8


class QuotedSpan(Strict):
    system: System
    record_id: str
    field: str = Field(description="The field holding the text, e.g. 'body' for a helpdesk message.")
    text: str = Field(min_length=1, description="The exact text to hide, copied character for character.")
    occurrence: int | None = Field(
        default=None,
        ge=1,
        description="Which occurrence in the field (1 = first). Leave empty to hide every occurrence.",
    )
    category: RedactionCategory
    reason: str = Field(min_length=1)


class QuotedSpecial(Strict):
    system: System
    record_id: str
    field: str
    text: str = Field(min_length=1, description="The exact text of the special-category mention.")
    category: SpecialCategory


class RedactionSubmission(Strict):
    spans: list[QuotedSpan]
    special_category: list[QuotedSpecial] = Field(default_factory=list)
    unresolved: list[str] = Field(
        default_factory=list, description="Mentions of people you could not classify, quoted."
    )


def find_all(text: str, quote: str) -> list[int]:
    starts, i = [], text.find(quote)
    while i != -1:
        starts.append(i)
        i = text.find(quote, i + len(quote))
    return starts


def _locate(
    fields: Mapping[FieldKey, str], key: FieldKey, quote: str, occurrence: int | None
) -> tuple[list[tuple[int, int]], str | None]:
    system, record_id, field = key
    if key not in fields:
        return [], f"{system.value} {record_id} has no text field '{field}' in the pack"
    starts = find_all(fields[key], quote)
    if not starts:
        return [], f"'{quote}' is not in {system.value} {record_id} {field} (quote it exactly as written)"
    if occurrence is not None:
        if occurrence > len(starts):
            return [], f"'{quote}' occurs {len(starts)} time(s) in {system.value} {record_id} {field}"
        starts = [starts[occurrence - 1]]
    return [(s, s + len(quote)) for s in starts], None


def resolve(
    submission: RedactionSubmission, fields: Mapping[FieldKey, str]
) -> tuple[RedactionPlan | None, list[str]]:
    errors: list[str] = []
    spans: list[RedactionSpan] = []
    for q in submission.spans:
        found, error = _locate(fields, (q.system, q.record_id, q.field), q.text, q.occurrence)
        if error:
            errors.append(error)
        spans.extend(
            RedactionSpan(
                system=q.system,
                record_id=q.record_id,
                field=q.field,
                start=start,
                end=end,
                category=q.category,
                reason=q.reason,
            )
            for start, end in found
        )
    flags: list[SpecialCategoryFlag] = []
    for s in submission.special_category:
        found, error = _locate(fields, (s.system, s.record_id, s.field), s.text, None)
        if error:
            errors.append(error)
        flags.extend(
            SpecialCategoryFlag(
                system=s.system, record_id=s.record_id, field=s.field, start=a, end=b, category=s.category
            )
            for a, b in found
        )
    if errors:
        return None, errors[:MAX_ERRORS]
    unique = {(s.system, s.record_id, s.field, s.start, s.end): s for s in spans}
    plan = RedactionPlan(
        ok=True,
        spans=sorted(unique.values(), key=lambda s: (s.system.value, s.record_id, s.field, s.start)),
        special_category=flags,
        unresolved=submission.unresolved,
    )
    return plan, []


def mask(text: str, spans: list[RedactionSpan], language: Language) -> str:
    """Replace each span by its marker; overlapping spans are merged (the first one's category wins)."""
    merged: list[tuple[int, int, RedactionCategory]] = []
    for s in sorted(spans, key=lambda s: (s.start, s.end)):
        if merged and s.start <= merged[-1][1]:
            start, end, category = merged[-1]
            merged[-1] = (start, max(end, s.end), category)
        else:
            merged.append((s.start, s.end, s.category))
    out = text
    for start, end, category in reversed(merged):
        out = out[:start] + MARKERS[language][category] + out[end:]
    return out


def masked_fields(
    fields: Mapping[FieldKey, str], plan: RedactionPlan, language: Language
) -> dict[FieldKey, str]:
    by_field: dict[FieldKey, list[RedactionSpan]] = {}
    for s in plan.spans:
        by_field.setdefault((s.system, s.record_id, s.field), []).append(s)
    return {k: mask(v, by_field[k], language) if k in by_field else v for k, v in fields.items()}


def verify(fields: Mapping[FieldKey, str], plan: RedactionPlan, language: Language) -> list[str]:
    """Every redacted text must be gone from every text field of the pack, not only from the field it was
    quoted in. Returns what is still readable (empty when the check passes)."""
    originals = {fields[(s.system, s.record_id, s.field)][s.start : s.end] for s in plan.spans}
    masked = masked_fields(fields, plan, language)
    problems = []
    for quote in sorted(originals):
        for (system, record_id, field), text in masked.items():
            if quote in text:
                problems.append(f"'{quote}' is still readable in {system.value} {record_id} {field}")
    return problems[:MAX_ERRORS]


ORDER_REF = re.compile(r"\bFL-\d{6}\b")


def foreign_order_numbers(masked: Mapping[FieldKey, str], own_orders: set[str]) -> list[str]:
    """Identifiers the agent missed: an order number still readable in the pack that is not one of the
    customer's own orders points to another customer, so it is their personal data (rule R-OTHERS). The
    verifier above only checks what the agent chose to hide; this checks for what it did not."""
    problems = []
    for (system, record_id, field), text in sorted(masked.items()):
        for ref in sorted(set(ORDER_REF.findall(text)) - own_orders):
            problems.append(
                f"order {ref} is readable in {system.value} {record_id} {field} but is not one of the "
                "customer's orders: it points to another customer, so hide it (category third_party)"
            )
    return problems[:MAX_ERRORS]
