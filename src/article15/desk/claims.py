"""The claims cross-check: code checks what the letter says against the pack (PLAN.md 7, rule R-VALIDATION).

The validator agent judges the pack, but its judgment is not the last word. When the pack is assembled, code
compares the letter with what the tools actually returned:
- the record count the letter claims, and every "<n> records" it states, against the copy;
- every record ID the letter names, which must be in the copy;
- every Article 15(1) item, with the checklist of the register's facts (checklist.py), and the items the
  letter says it covers against what its text shows.

The problems go into the case file, and guard `case.claims_verified` refuses VALIDATED while any remain, so a
validator that approves a faulty pack is overruled.
"""

from __future__ import annotations

import re
from typing import Any

from article15.desk.checklist import check_letter, normalise
from article15.schemas import LetterDraft, System

RECORD_ID = re.compile(r"\b(CS\d{4}|C\d{4}|DR\d{3}|FL-\d{6}|L\d{6}|T\d{5}|M\d{6}|E\d{7})\b")
STATED_TOTAL = re.compile(r"\b(\d+) (?:enregistrements?|elements?|records?|items?)\b")
COPY_FORMAT = "application/json"  # a commonly used electronic format (Art. 15(3)); PDF comes with the web app


def check_claims(letter: LetterDraft, pack: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    in_copy = len(pack["records"])
    if letter.record_count_claimed != in_copy:
        problems.append(
            f"record_count_claimed is {letter.record_count_claimed}, but the copy holds {in_copy}"
        )
    text = normalise(f"{letter.subject_line}\n{letter.body}")
    stated = sorted({int(n) for n in STATED_TOTAL.findall(text)} - {in_copy})
    counts = pack.get("record_counts", {})
    per_system = set(counts.values())
    if len(counts) < len(System):
        per_system.add(0)  # "web logs: 0 records" is true of a system with nothing in the copy
    wrong = [n for n in stated if n not in per_system]
    if wrong:
        problems.append(
            f"the letter states {', '.join(map(str, wrong))} records; the copy holds {in_copy} "
            f"({', '.join(f'{s} {n}' for s, n in sorted(pack.get('record_counts', {}).items()))})"
        )
    ids = {r["record_id"] for r in pack["records"]}
    unknown = sorted(set(RECORD_ID.findall(f"{letter.subject_line}\n{letter.body}")) - ids)
    if unknown:
        problems.append(f"the letter names record(s) that are not in the copy: {', '.join(unknown[:10])}")
    result = check_letter(letter.body, letter.language, letter.items_covered)
    for item, facts in sorted(result.missing.items()):
        problems.append(f"Art. 15 item {item.value} is missing or incomplete (not found: {', '.join(facts)})")
    if result.claimed_not_present:
        claimed = ", ".join(sorted(i.value for i in result.claimed_not_present))
        problems.append(f"items_covered lists {claimed}, which the text does not give")
    return problems
