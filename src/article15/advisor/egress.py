"""The egress filter: the last check before anything leaves for the advisor (guard `a2a.egress_filter`,
rule R-MINIMISE-EGRESS).

The payload is a `CaseSummary`, which has no free-text field, so the first line of defence is its type. The
filter is the second: it scans the serialised payload for anything that looks like personal data (an email
address, a phone number, a postcode-like or long number run, a record or customer ID) and for any value the
caller says must never leave (the case's names and addresses), and refuses the whole message if it finds one.
Every payload that passes is recorded, so the eval can check what was sent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from article15.advisor.models import CaseSummary

GUARD = "a2a.egress_filter"
RULE = "R-MINIMISE-EGRESS"
PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"),
    "phone": re.compile(r"(\+\d{2}\s?|\b0)\d([\s.-]?\d{2}){4}\b"),
    "record id": re.compile(r"\b(CS\d{4}|C\d{4}|DR\d{3}|FL-\d{6}|L\d{6}|T\d{5}|M\d{6}|E\d{7}|A15-\d{3})\b"),
    "ip address": re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"),
}


class EgressRefused(Exception):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__(f"{RULE}: the message to the advisor was blocked ({'; '.join(reasons)})")


def check_payload(text: str, never: Iterable[str] = ()) -> list[str]:
    """Reasons to block `text`, or [] when it may leave."""
    reasons = [f"looks like {kind}" for kind, p in PATTERNS.items() if p.search(text)]
    lowered = text.lower()
    reasons += ["contains a value of the case's people" for v in never if v and v.lower() in lowered][:1]
    return reasons


def outgoing(summary: CaseSummary, never: Iterable[str] = ()) -> str:
    """The payload to send, after the filter. Raises EgressRefused if it must not leave."""
    text = summary.model_dump_json()
    reasons = check_payload(text, never)
    if reasons:
        raise EgressRefused(reasons)
    return text
