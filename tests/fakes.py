"""Test doubles for the agent core: a scripted provider, and an oracle that plays a correct case from truth.

The oracle drives the real desk (state machine, scope, MCP servers, pack) the way a perfect agent would, so
the whole pipeline can be tested offline and for free, and broken desks can be shown to fail.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Any

from article15.company.models import CaseTruth, RequestCase, SpanCategory
from article15.company.register import Register
from article15.core.model import ModelRequest, ModelResponse, ToolCall, Usage
from article15.core.pricing import cost_usd, price_for
from article15.schemas import Art15Item, IdentityOutcome, Language

Turn = list[tuple[str, dict[str, Any]]] | str
"""One model turn: tool calls (name, arguments), or plain text."""
Script = Generator[Turn, list[str], None]
"""Receives the texts the model saw since its last turn (tool results, then new user messages)."""

PAYLOAD = re.compile(r"<untrusted_data source=\"[^\"]+\">\n(.*)\n</untrusted_data>", re.S)


def payload(text: str) -> Any:
    m = PAYLOAD.search(text)
    assert m, f"not a tool payload: {text[:200]}"
    return json.loads(m.group(1))


def _seen_since_last_turn(messages: list[dict[str, Any]]) -> list[str]:
    last = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    return [str(m.get("content") or "") for m in messages[last + 1 :] if m["role"] in ("tool", "user")]


class ScriptedProvider:
    """Plays a script turn by turn. Usage is derived from the request size, so accounting can be tested."""

    def __init__(self, script: Script, name: str = "scripted", reasoning: str | None = None) -> None:
        self.script = script
        self.name = name
        self.reasoning = reasoning
        self.requests: list[ModelRequest] = []
        self._started = False
        self._calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        seen = _seen_since_last_turn(request.messages)
        try:
            turn = self.script.send(seen) if self._started else next(self.script)
        except StopIteration:
            turn = "I have nothing more to do."
        self._started = True
        calls: list[ToolCall] = []
        if not isinstance(turn, str):
            for name, args in turn:
                self._calls += 1
                calls.append(ToolCall(id=f"call_{self._calls}", name=name, arguments=json.dumps(args)))
        size = len(request.canonical()) // 4
        previous = len(self.requests[-2].canonical()) // 4 if len(self.requests) > 1 else 0
        hit = min(previous, size)
        usage = Usage(
            input_tokens=size,
            cache_hit_tokens=hit,
            cache_miss_tokens=size - hit,
            output_tokens=sum(len(c.arguments) for c in calls) // 4 + 10,
        )
        price = price_for("deepseek", "deepseek-flash", datetime(2026, 11, 21, 12, tzinfo=UTC))
        return ModelResponse(
            provider=self.name,
            model=request.model,
            content=turn if isinstance(turn, str) else None,
            reasoning_content=self.reasoning,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else "stop",
            usage=usage,
            cost_usd=cost_usd(usage, price),
        )


def script_of(turns: list[Turn]) -> Script:
    """A fixed list of turns that ignores what the tools return."""

    def gen() -> Script:
        # Not `yield from turns`: that forwards send() to the list iterator, which has no send().
        for turn in turns:  # noqa: UP028
            yield turn

    return gen()


def reactive(fn: Callable[[list[str]], Turn | None]) -> Script:
    """A script computed from what the model saw; stops when fn returns None."""

    def gen() -> Script:
        seen: list[str] = []
        while (turn := fn(seen)) is not None:
            seen = yield turn

    return gen()


# --- the oracle --------------------------------------------------------------------------------------------


def _letter_body(register: Register, language: Language, counts: dict[str, int], redacted: bool) -> str:
    total = sum(counts.values())
    per_system = ", ".join(f"{s}: {n}" for s, n in sorted(counts.items()))
    lines = [
        "Bonjour," if language is Language.FR else "Hello,",
        (
            f"Vous trouverez ci-joint la copie de vos données personnelles : {total} enregistrements "
            f"({per_system})."
            if language is Language.FR
            else f"Please find attached a copy of your personal data: {total} records ({per_system})."
        ),
    ]
    lines += [f"- {p.get(language)} — {p.retention.get(language)}" for p in register.purposes]
    lines += [f"- {c.get(language)}" for c in register.categories]
    lines += [f"- {r.get(language)}" for r in register.recipients]
    lines += [
        t.get(language)
        for t in (
            register.rights,
            register.complaint,
            register.data_source,
            register.automated_decisions,
            register.transfers,
        )
    ]
    if redacted:
        lines.append(
            "Les données concernant d'autres personnes ont été masquées."
            if language is Language.FR
            else "Data about other people has been hidden."
        )
    return "\n".join(lines)


def oracle(truth: CaseTruth, request: RequestCase, register: Register, sender_name: str) -> Script:
    """A perfect single agent for standard and identity cases, reading only what its tools return, plus the
    truth for its judgments (who the subject is, what to redact)."""

    def gen() -> Script:
        language = truth.language
        seen = yield [
            (
                "case_submit_intake",
                {
                    "ok": True,
                    "received_on": str(request.received_on),
                    "channel": request.channel.value,
                    "language": language.value,
                    "claimed_name": sender_name,
                    "request_types": [t.value for t in truth.request_types],
                    "embedded_instructions_detected": truth.embedded_instructions,
                },
            )
        ]
        seen = yield [("crm_check_identity", {})]
        check = payload(seen[0])
        ref, pc = truth.requester.knows_order_reference, truth.requester.knows_postcode
        in_request = bool(ref and pc and ref in request.text and pc in request.text)
        verified: dict[str, Any] | None = None
        if check["sender_registered"] and check["sender_customer_id"] == truth.subject_id:
            verified = {"evidence": [{"kind": "registered_email", "detail": "request sent from it"}]}
        elif in_request:
            verified = {"order_reference": ref, "postcode": pc}
        if verified is None:
            question = (
                "Pourriez-vous nous indiquer le numéro d'une commande récente et son code postal de "
                "livraison ?"
                if language is Language.FR
                else "Could you give us the number of a recent order and its delivery postcode?"
            )
            seen = yield [
                (
                    "case_submit_identity",
                    {
                        "ok": True,
                        "outcome": "NEEDS_MORE_INFO",
                        "follow_up_question": question,
                        "confidence": 0.5,
                    },
                )
            ]
            seen = yield [("notify_read_replies", {})]
            genuine = truth.requester.person_id == truth.subject_id
            if genuine and ref and pc:
                verified = {"order_reference": ref, "postcode": pc}
            elif truth.expected_final_identity is IdentityOutcome.NEEDS_MORE_INFO:
                seen = yield [
                    (
                        "case_submit_identity",
                        {
                            "ok": True,
                            "outcome": "NEEDS_MORE_INFO",
                            "follow_up_question": "Merci de nous transmettre un mandat écrit."
                            if language is Language.FR
                            else "Please send us a written mandate.",
                            "confidence": 0.4,
                        },
                    )
                ]
                return
            else:
                seen = yield [
                    (
                        "case_submit_identity",
                        {
                            "ok": True,
                            "outcome": "REJECTED",
                            "rejection_reason": "identity could not be established",
                            "confidence": 0.8,
                        },
                    )
                ]
                return
        if "order_reference" in verified:
            seen = yield [("orders_check_reference", {"order_reference": ref, "postcode": pc})]
            verified["evidence"] = [{"kind": "order_reference", "detail": f"{ref} with postcode {pc}"}]
        seen = yield [
            (
                "case_submit_identity",
                {
                    "ok": True,
                    "outcome": "VERIFIED",
                    "subject_id": truth.subject_id,
                    "confidence": 0.95,
                    **verified,
                },
            )
        ]
        refs: list[dict[str, Any]] = []
        seen = yield [("crm_get_customer", {}), ("orders_search", {}), ("helpdesk_search_tickets", {})]
        for text in seen:
            refs += payload(text)["records"]
        offset = 0
        while True:
            seen = yield [("weblogs_search_events", {"offset": offset, "limit": 500})]
            page = payload(seen[0])
            refs += page["records"]
            offset += len(page["records"])
            if not page["truncated"]:
                break
        wanted = {(k.system.value, k.table, k.record_id) for k in truth.records}
        chosen = [r for r in refs if (r["system"], r["table"], r["record_id"]) in wanted]
        records = [
            {
                "system": r["system"],
                "table": r["table"],
                "record_id": r["record_id"],
                "reason": "the customer's",
            }
            for r in chosen
        ]
        seen = yield [
            (
                "case_submit_discovery",
                {
                    "ok": True,
                    "subject_id": truth.subject_id,
                    "records": records,
                    "identifiers_used": [f"customer {truth.subject_id}"],
                    "systems_searched": ["crm", "orders", "helpdesk", "weblogs"],
                },
            )
        ]
        tickets = [r["record_id"] for r in chosen if r["table"] == "tickets"]
        if tickets:
            seen = yield [("helpdesk_read_ticket", {"ticket_id": t}) for t in tickets]
        in_pack = {r["record_id"] for r in chosen}
        spans = [
            {
                "system": s.record.system.value,
                "record_id": s.record.record_id,
                "field": s.field,
                "text": s.text,
                "category": "third_party",
                "reason": s.note,
            }
            for s in truth.redactions
            if s.category is SpanCategory.THIRD_PARTY and s.record.record_id in in_pack
        ]
        special = [
            {
                "system": m.record.system.value,
                "record_id": m.record.record_id,
                "field": m.field,
                "text": m.text,
                "category": m.category,
            }
            for m in truth.special_category
            if m.record.record_id in in_pack
        ]
        seen = yield [("case_submit_redaction", {"spans": spans, "special_category": special})]
        counts: dict[str, int] = {}
        for r in chosen:
            counts[r["system"]] = counts.get(r["system"], 0) + 1
        seen = yield [
            (
                "case_submit_letter",
                {
                    "ok": True,
                    "language": language.value,
                    "subject_line": "Votre demande d'accès"
                    if language is Language.FR
                    else "Your access request",
                    "body": _letter_body(register, language, counts, bool(spans)),
                    "items_covered": [i.value for i in Art15Item],
                    "record_count_claimed": len(chosen),
                },
            )
        ]
        seen = yield [("case_read_pack", {})]
        seen = yield [("case_submit_validation", {"ok": True, "verdict": "APPROVED"})]

    return gen()
