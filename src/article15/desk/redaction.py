"""Redaction: the agent quotes, code locates, applies and verifies (decision 0010).

The agent points at text by quoting it exactly (with an optional occurrence number), because models are poor
at counting characters. Code finds the offsets, rejects quotes that are not in the record, masks the spans,
and then verifies that no redacted text is still readable anywhere in the pack. The verifier is what lets the
case pass guard `case.redaction_verified` (rule R-OTHERS).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

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


def _where(fields: Mapping[FieldKey, str], key: FieldKey) -> str:
    """Where the text actually is, so the model can fix the address on its next call: the record's text
    fields, or, for a helpdesk ticket, the messages that are records of their own."""
    system, record_id, _ = key
    own = sorted(f for s, r, f in fields if s is system and r == record_id and f != "ticket_id")
    messages = sorted(
        r for (s, r, f), v in fields.items() if s is System.HELPDESK and f == "ticket_id" and v == record_id
    )
    hints = [f"its text fields are: {', '.join(own)}"] if own else []
    if messages:
        hints.append(
            f"{record_id} is a ticket: each of its messages is a record of its own, so use record_id "
            f"{', '.join(messages)} with field 'body'"
        )
    return "".join(f"; {h}" for h in hints) or "; that record is not in the pack"


def _locate(
    fields: Mapping[FieldKey, str], key: FieldKey, quote: str, occurrence: int | None
) -> tuple[list[tuple[int, int]], str | None]:
    system, record_id, field = key
    if key not in fields:
        return [], f"{system.value} {record_id} has no text field '{field}' in the pack{_where(fields, key)}"
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
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\d])(?:\+33[\s.-]?|0)[1-9](?:[\s.-]?\d{2}){4}(?![\d])")


def readable_staff(masked: Mapping[FieldKey, str], staff_names: Iterable[str]) -> list[str]:
    """Policy v2 (E3): Ferlane's staff names must not be readable in the free text (their structured
    `staff_name` field is masked by code). Returns where one still is."""
    problems = []
    for name in sorted(set(staff_names)):
        for (system, record_id, field), text in masked.items():
            if field != "staff_name" and name in text:
                where = f"{system.value} {record_id} {field}"
                problems.append(f"the staff name '{name}' is still readable in {where}")
    return problems[:MAX_ERRORS]


def _phone_key(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return "0" + digits[2:] if digits.startswith("33") and len(digits) == 11 else digits


def own_contacts(fields: Mapping[FieldKey, str], verified_emails: Iterable[str]) -> tuple[set[str], set[str]]:
    """The customer's own addresses and phone numbers: those the case verified (the grant's addresses), and
    every field of their records that holds just an address or a number (the CRM's email and phone, a linked
    guest order's email). Every record in the pack is the customer's, so those fields are theirs."""
    emails = {e.strip().lower() for e in verified_emails}
    phones: set[str] = set()
    for text in fields.values():
        value = text.strip()
        if EMAIL.fullmatch(value):
            emails.add(value.lower())
        elif PHONE.fullmatch(value):
            phones.add(_phone_key(value))
    return emails, phones


def foreign_contacts(masked: Mapping[FieldKey, str], own_emails: set[str], own_phones: set[str]) -> list[str]:
    """Like foreign_order_numbers, for email addresses and phone numbers: one still readable in free text
    that is not the customer's own is someone else's personal data (rule R-OTHERS). Found by experiment E2,
    where an address planted by a prompt injection stayed readable and no check saw it (decision 0017).

    It errs on the safe side: the customer's own second address, mentioned in a ticket but never confirmed
    by proof of control, is hidden too, because an address planted in the customer's own message looks the
    same."""
    problems = []
    for (system, record_id, field), text in sorted(masked.items()):
        if EMAIL.fullmatch(text.strip()) or PHONE.fullmatch(text.strip()):
            continue  # a field that holds only an address or a number is the customer's own (own_contacts)
        found = [m.group(0) for m in EMAIL.finditer(text) if m.group(0).lower() not in own_emails]
        found += [m.group(0) for m in PHONE.finditer(text) if _phone_key(m.group(0)) not in own_phones]
        for value in sorted(set(found)):
            problems.append(
                f"'{value}' is readable in {system.value} {record_id} {field} but is not a verified "
                "address or number of the customer, so it may be someone else's: hide it (category "
                "third_party)"
            )
    return problems[:MAX_ERRORS]


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
