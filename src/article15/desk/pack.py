"""The pack: the letter plus the copy of the person's data, assembled by code (PLAN.md 5.5).

Agents hand over record references, not content. Code lists what the scoped servers return for the verified
person (`scope_index`), checks every reference against it, and fetches the full records through the same
scoped tools, so nothing outside the subject's scope can enter a pack even if an agent names it. The pack is
serialised deterministically and its SHA-256 is what a human approval signs (decision 0006).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from article15.case.approvals import pack_hash
from article15.core.mcp_tools import McpHub
from article15.desk.redaction import FieldKey, masked_fields
from article15.schemas import Language, LetterDraft, RecordRef, RedactionPlan, System

PACK_VERSION = 1
WEBLOG_PAGE = 500
SYSTEM_ORDER = {s: i for i, s in enumerate((System.CRM, System.ORDERS, System.HELPDESK, System.WEBLOGS))}

RecordKey = tuple[System, str, str]  # (system, table, record_id)


@dataclass(frozen=True)
class PackRecord:
    system: System
    table: str
    record_id: str
    fields: dict[str, Any]

    @property
    def key(self) -> RecordKey:
        return (self.system, self.table, self.record_id)


def sort_key(key: RecordKey) -> tuple[int, str, str]:
    return (SYSTEM_ORDER[key[0]], key[1], key[2])


async def scope_index(hub: McpHub) -> set[RecordKey]:
    """Every record the servers return for the case's verified person."""
    found: set[RecordKey] = set()
    for tool in ("crm_get_customer", "orders_search", "helpdesk_search_tickets"):
        result = await hub.call(tool)
        found |= {(System(r["system"]), r["table"], r["record_id"]) for r in result["records"]}
    offset = 0
    while True:
        page = await hub.call("weblogs_search_events", {"offset": offset, "limit": WEBLOG_PAGE})
        found |= {(System(r["system"]), r["table"], r["record_id"]) for r in page["records"]}
        offset += len(page["records"])
        if not page["truncated"] or not page["records"]:
            return found


async def fetch_records(hub: McpHub, keys: Iterable[RecordKey]) -> dict[RecordKey, PackRecord]:
    """Full records for `keys`, read through the scoped tools."""
    wanted = sorted(set(keys), key=sort_key)
    out: dict[RecordKey, PackRecord] = {}
    tickets: dict[str, dict[str, Any]] = {}
    message_ticket: dict[str, str] = {}
    if any(k[0] is System.HELPDESK for k in wanted):
        listing = await hub.call("helpdesk_search_tickets")
        for r in listing["records"]:
            if r["table"] == "tickets":
                ticket = await hub.call("helpdesk_read_ticket", {"ticket_id": r["record_id"]})
                tickets[r["record_id"]] = ticket
                for m in ticket["messages"]:
                    message_ticket[m["message_id"]] = r["record_id"]
    for key in wanted:
        system, table, record_id = key
        if system is System.HELPDESK:
            if table == "tickets":
                t = tickets[record_id]
                fields = {k: v for k, v in t.items() if k != "messages"}
            else:
                t = tickets[message_ticket[record_id]]
                m = next(m for m in t["messages"] if m["message_id"] == record_id)
                fields = {"ticket_id": t["ticket_id"], **m}
        elif system is System.WEBLOGS:
            fields = (await hub.call("weblogs_read_record", {"record_id": record_id}))["fields"]
        else:
            tool = f"{system.value}_read_record"
            fields = (await hub.call(tool, {"table": table, "record_id": record_id}))["fields"]
        out[key] = PackRecord(system, table, record_id, dict(fields))
    return out


def text_fields(records: Iterable[PackRecord]) -> dict[FieldKey, str]:
    """Every string field of the pack's records: what redaction can target and what the verifier scans."""
    return {
        (r.system, r.record_id, name): value
        for r in records
        for name, value in r.fields.items()
        if isinstance(value, str)
    }


def build_pack(
    *,
    case_id: str,
    policy_id: str,
    policy_version: str,
    generated_at: datetime,
    subject_id: str,
    language: Language,
    letter: LetterDraft,
    refs: list[RecordRef],
    records: dict[RecordKey, PackRecord],
    plan: RedactionPlan,
) -> dict[str, Any]:
    chosen = [records[(r.system, r.table, r.record_id)] for r in refs]
    chosen.sort(key=lambda r: sort_key(r.key))
    masked = masked_fields(text_fields(chosen), plan, language)
    copy = [
        {
            "system": r.system.value,
            "table": r.table,
            "record_id": r.record_id,
            "fields": {
                name: masked.get((r.system, r.record_id, name), value) for name, value in r.fields.items()
            },
        }
        for r in chosen
    ]
    counts: dict[str, int] = {}
    for r in chosen:
        counts[r.system.value] = counts.get(r.system.value, 0) + 1
    return {
        "pack_version": PACK_VERSION,
        "case_id": case_id,
        "policy": {"id": policy_id, "version": policy_version},
        "generated_at": generated_at.isoformat(),
        "subject_id": subject_id,
        "language": language.value,
        "letter": {"subject_line": letter.subject_line, "body": letter.body},
        "record_counts": counts,
        "records": copy,
        "redactions": [
            {
                "system": s.system.value,
                "record_id": s.record_id,
                "field": s.field,
                "category": s.category.value,
            }
            for s in plan.spans
        ],
        "special_category": [
            {
                "system": f.system.value,
                "record_id": f.record_id,
                "field": f.field,
                "category": f.category.value,
            }
            for f in plan.special_category
        ],
    }


def pack_bytes(pack: dict[str, Any]) -> bytes:
    return (json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def hash_pack(pack: dict[str, Any]) -> str:
    return pack_hash(pack_bytes(pack))
