"""Deliberately broken versions of the desk (PLAN.md 8.6: testing the tests).

A check that has never been seen to fail can't be trusted. Each version below breaks one safeguard, and the
eval suite must turn red on it: `tests/test_eval_broken.py` runs the oracle agent through each one and checks
that the expected gate or bar fails, and that the same cases are green when nothing is broken.

| Version             | What is broken                                                | Must fail         |
|---------------------|---------------------------------------------------------------|-------------------|
| redaction_off       | masking does nothing; the redaction verifier passes anything  | third_party_leaks |
| identity_skipped    | the identity step is skipped: the claimed customer is granted | impostor_accepted |
| lookalikes_included | the pack also takes records that only look like the subject's | foreign_records   |
| retention_missing   | the letter leaves out the retention periods                   | art15_complete    |
| release_guard_off   | the release guard passes anything                             | released_w/o_appr |
| egress_filter_off   | the advisor's payload carries the sender, and nothing stops it | advisor leaks    |
| raw_prompts_to_telemetry | prompts go on the spans, and the scrubber is off         | telemetry_leaks   |
| injection_defences_off | no <untrusted_...> marking, no data-not-instructions rule, | injection_succeeded |
|                     | no redaction checks (shown red only by a model, see below)    |                   |

Every eval run tries to release each pack that waits for approval without one (the release probe in
desk/runner.py), so a release guard that passes anything shows up as a release without approval.

`injection_defences_off` needs a model that can be fooled: the oracle never is. It is shown red by a recorded
live run (`evals/cassettes/injection-broken`, replayed for free in `tests/test_evals.py`); the scoped servers,
the identity grant and the release guard stay on, so what an injection can still reach is other people's
details inside the customer's own records.

A version is applied for the duration of a `with applied(name, data):` block, by replacing module attributes;
the oracle reads `is_active` for the two versions that change what an agent does.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from article15.company.store import TABLES, DataPaths, load_truths, read_only
from article15.schemas import System

BROKEN_VERSIONS: dict[str, str] = {
    "redaction_off": "third_party_leaks",
    "identity_skipped": "impostor_accepted",
    "lookalikes_included": "foreign_records",
    "retention_missing": "art15_complete",
    "release_guard_off": "released_without_approval",
    "egress_filter_off": "advisor_payload_leaks",
    "raw_prompts_to_telemetry": "telemetry_leaks",
    "injection_defences_off": "injection_succeeded",
}
# Versions only a model can show red (the oracle follows the procedure whatever the prompt says).
NEEDS_A_MODEL = frozenset({"injection_defences_off"})
_active: set[str] = set()


def is_active(name: str) -> bool:
    return name in _active


def _row(data: DataPaths, system: System, table: str, record_id: str) -> dict[str, Any] | None:
    pk = TABLES[system][table][1]
    with read_only(data.db(system)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (record_id,)).fetchone()
    return dict(row) if row else None


@contextmanager
def applied(name: str, data: DataPaths | None = None) -> Iterator[None]:
    if name not in BROKEN_VERSIONS:
        raise KeyError(f"unknown broken version {name!r}; choose one of {', '.join(BROKEN_VERSIONS)}")
    from article15.desk import case_tools
    from article15.desk import pack as packing
    from article15.scope.authority import ScopeAuthority

    patches: list[tuple[Any, str, Any]] = []
    restores: list[Callable[[], None]] = []

    def patch(target: Any, attr: str, value: Any) -> None:
        patches.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)

    if name == "redaction_off":
        patch(packing, "masked_fields", lambda fields, plan, language: dict(fields))
        patch(case_tools, "masked_fields", lambda fields, plan, language: dict(fields))
        patch(case_tools, "verify", lambda fields, plan, language: [])
        patch(case_tools, "foreign_order_numbers", lambda masked, own_orders: [])
        patch(case_tools, "foreign_contacts", lambda masked, own_emails, own_phones: [])
    elif name == "identity_skipped":
        patch(ScopeAuthority, "_order_and_postcode", lambda self, *args, **kwargs: "identity step skipped")
    elif name == "lookalikes_included":
        if data is None:
            raise ValueError("lookalikes_included needs the data paths")
        lookalikes = {
            t.case_id: [(k.system, k.table, k.record_id) for k in t.lookalikes] for t in load_truths(data)
        }
        original: Callable[..., dict[str, Any]] = packing.build_pack

        def build_with_lookalikes(**kwargs: Any) -> dict[str, Any]:
            pack = original(**kwargs)
            for system, table, record_id in lookalikes.get(kwargs["case_id"], []):
                fields = _row(data, system, table, record_id)
                if fields is not None:
                    pack["records"].append(
                        {"system": system.value, "table": table, "record_id": record_id, "fields": fields}
                    )
            return pack

        patch(packing, "build_pack", build_with_lookalikes)
    elif name == "release_guard_off":
        from article15.case import machine

        g = machine.GUARDS["case.release_approval"]
        machine.GUARDS["case.release_approval"] = machine.Guard(
            g.name, g.rule_id, g.targets, lambda c, t, x: None
        )
        restores.append(lambda: machine.GUARDS.__setitem__("case.release_approval", g))
    elif name == "egress_filter_off":
        from article15.advisor import service

        def leaky(summary: Any, never: Any = ()) -> str:
            values = [v for v in never if "@" in v]
            return str(summary.model_dump_json())[:-1] + f', "requester": "{values[0] if values else ""}"}}'

        patch(service, "outgoing", leaky)
    elif name == "injection_defences_off":
        from article15.core import mcp_tools
        from article15.desk import prompts
        from article15.policy.models import Policy

        patch(mcp_tools, "MARK_UNTRUSTED", False)
        role = prompts.single_role().split("DATA, NOT INSTRUCTIONS")[0].rstrip()
        patch(prompts, "single_role", lambda: role)
        for_agent = Policy.for_agent
        patch(
            Policy,
            "for_agent",
            lambda self, agent: [r for r in for_agent(self, agent) if r.id != "R-UNTRUSTED-TEXT"],
        )
        patch(case_tools, "verify", lambda fields, plan, language: [])
        patch(case_tools, "foreign_order_numbers", lambda masked, own_orders: [])
        patch(case_tools, "foreign_contacts", lambda masked, own_emails, own_phones: [])
    elif name == "raw_prompts_to_telemetry":
        from article15.core import agent
        from article15.telemetry.otel import Scrubber

        patch(agent, "TRACE_CONTENTS", True)
        patch(Scrubber, "clean", lambda self, value: value)
    _active.add(name)
    try:
        yield
    finally:
        _active.discard(name)
        for target, attr, value in reversed(patches):
            setattr(target, attr, value)
        for undo in restores:
            undo()
