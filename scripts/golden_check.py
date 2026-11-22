"""Independent check of the golden 20 labels (PLAN.md 8.1), as a second opinion next to the review sheet.

It does not use the generator's own ownership file (truth/owners.jsonl). For each golden case it derives,
from the raw company tables, the people file and the policy text:

* the records of the subject: customer row, consents and prior requests by customer ID; orders by customer
  ID or by a guest email the person owns; their lines; tickets by customer ID or requester email; their
  messages; web events by account ID or by a cookie the person used while logged in;
* the due date: same date next month, clamped to the month's last day, moved to the next working day past a
  weekend or French public holiday (R-DEADLINE);
* other people's data in the subject's messages: the names of other people and of staff, and email
  addresses and phone numbers that are not the subject's;
* special-category mentions, by keyword;

and prints every difference from the truth. The identity and resolution labels are judgment calls; it prints
the facts they rest on (sender registered or not, what the requester knows) for the reviewer.

    uv run python scripts/golden_check.py
"""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import holidays

DATA = Path("data/generated")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.]?\d{2}){4}")
SPECIAL = re.compile(
    r"allerg|santé|maladie|enceinte|grossesse|handicap|diabè|médic|hôpital|religi|syndica|"
    r"health|pregnan|disab|diabet|hospital|medical|union member",
    re.IGNORECASE,
)


def due_date(received: date) -> date:
    y, m = (received.year + 1, 1) if received.month == 12 else (received.year, received.month + 1)
    d = date(y, m, min(received.day, calendar.monthrange(y, m)[1]))
    fr = holidays.country_holidays("FR", years=[y, y + 1])
    while d.weekday() >= 5 or d in fr:
        d += timedelta(days=1)
    return d


def rows(db: str, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    c = sqlite3.connect(DATA / f"{db}.sqlite")
    c.row_factory = sqlite3.Row
    return list(c.execute(sql, args))


def records_of(subject: str, mailboxes: list[str]) -> tuple[set[tuple[str, str, str]], list[sqlite3.Row]]:
    q = ",".join("?" * len(mailboxes)) or "''"
    out: set[tuple[str, str, str]] = {("crm", "customers", subject)}
    out |= {
        ("crm", "consents", r[0])
        for r in rows("crm", "select consent_id from consents where customer_id=?", (subject,))
    }
    out |= {
        ("crm", "prior_requests", r[0])
        for r in rows("crm", "select request_id from prior_requests where customer_id=?", (subject,))
    }
    orders = [
        r[0]
        for r in rows(
            "orders",
            f"select order_id from orders where customer_id=? or guest_email in ({q})",
            (subject, *mailboxes),
        )
    ]
    out |= {("orders", "orders", o) for o in orders}
    for o in orders:
        out |= {
            ("orders", "order_lines", r[0])
            for r in rows("orders", "select line_id from order_lines where order_id=?", (o,))
        }
    tickets = [
        r[0]
        for r in rows(
            "helpdesk",
            f"select ticket_id from tickets where customer_id=? or requester_email in ({q})",
            (subject, *mailboxes),
        )
    ]
    out |= {("helpdesk", "tickets", t) for t in tickets}
    messages: list[sqlite3.Row] = []
    for t in tickets:
        ms = rows("helpdesk", "select * from messages where ticket_id=?", (t,))
        messages += ms
        out |= {("helpdesk", "messages", m["message_id"]) for m in ms}
    # Web events: those of the account, and those of a cookie the person used while logged in (the same
    # browser before or after login). A different cookie on the same IP is a look-alike, not theirs.
    out |= {
        ("weblogs", "events", r[0])
        for r in rows(
            "weblogs",
            "select event_id from events where account_id=? or (account_id is null and cookie_id in "
            "(select cookie_id from events where account_id=?))",
            (subject, subject),
        )
    }
    return out, messages


def main() -> None:
    people = {
        p["person_id"]: p for p in map(json.loads, (DATA / "truth/people.jsonl").open(encoding="utf-8"))
    }
    cases = {c["case_id"]: c for c in map(json.loads, (DATA / "cases.jsonl").open(encoding="utf-8"))}
    truths = [t for t in map(json.loads, (DATA / "truth/truths.jsonl").open(encoding="utf-8")) if t["golden"]]
    staff = {
        r[0]
        for r in rows("helpdesk", "select distinct staff_name from messages where staff_name is not null")
    }
    for t in truths:
        cid, req, case = t["case_id"], t["requester"], cases[t["case_id"]]
        issues: list[str] = []
        received = date.fromisoformat(case["received_on"])
        if (d := due_date(received)).isoformat() != t["due"]:
            issues.append(f"due date: computed {d}, truth {t['due']}")
        subject = t["subject_id"]
        registered = None
        if subject:
            cust = rows("crm", "select * from customers where customer_id=?", (subject,))[0]
            registered = cust["email"]
            person = people[subject]
            mine, messages = records_of(subject, person["mailboxes"])
            truth_recs = {(r["system"], r["table"], r["record_id"]) for r in t["records"]}
            if truth_recs:
                for r in sorted(mine - truth_recs):
                    issues.append(f"record of the subject missing from the truth: {'/'.join(r)}")
                for r in sorted(truth_recs - mine):
                    issues.append(f"record in the truth not derived from the tables: {'/'.join(r)}")
            red = {(r["record"]["record_id"], r["text"]) for r in t["redactions"]}
            others = [p["name"] for k, p in people.items() if k != subject] + sorted(staff)
            own = set(person["mailboxes"]) | {registered}
            for m in messages if truth_recs else []:
                body = m["body"] or ""
                found = {n for n in others if n in body}
                found |= {e for e in EMAIL.findall(body) if e not in own}
                found |= {
                    p
                    for p in PHONE.findall(body)
                    if re.sub(r"\D", "", p) != re.sub(r"\D", "", cust["phone"] or "")
                }
                for f in sorted(found - {x for (mid, x) in red if mid == m["message_id"]}):
                    issues.append(f"possible third-party data not redacted: {m['message_id']} {f!r}")
                if SPECIAL.search(body) and not any(
                    s["record"]["record_id"] == m["message_id"] for s in t["special_category"]
                ):
                    issues.append(
                        f"possible special-category mention not flagged: {m['message_id']} {body[:90]!r}"
                    )
            for mid, x in sorted(red):
                if not any(m["message_id"] == mid and x in (m["body"] or "") for m in messages):
                    issues.append(f"redaction text not found in the subject's message: {mid} {x!r}")
        sender = case["sender"]
        who = "the subject" if req["person_id"] == subject else str(req["person_id"])
        facts = (
            f"sender {sender} ({'registered' if sender == registered else 'not registered'}); "
            f"requester is {who}; knows order {bool(req.get('knows_order_reference'))}, "
            f"postcode {bool(req.get('knows_postcode'))}, mandate {req.get('has_mandate')}; "
            f"identity {t['expected_identity']} -> {t['expected_final_identity']}; "
            f"resolution {t['expected_resolution']} {t['edge_reason'] or ''}"
        )
        print(f"{cid}: {'OK' if not issues else str(len(issues)) + ' difference(s)'} | {facts}")
        for i in issues:
            print("   -", i)


if __name__ == "__main__":
    main()
