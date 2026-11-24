"""The guided story on the web app's first page (decision 0016).

One featured case, told in four steps:

1. **The request:** a short email, and where the customer's data really is, traps included.
2. **The naive copy:** what the same case gives when two safeguards are switched off. It is not written by
   hand: it is the output of the oracle agent run through the real broken versions `redaction_off` and
   `lookalikes_included` (evals/broken.py), so it shows exactly what those safeguards stop.
3. **The desk:** a replay of the recorded live run of the case (DeepSeek V4.1 Flash, after the order-number
   guard), in the visitor's own sandbox. The model slips once and a code guard catches it; nothing is
   invented.
4. **The reviewer:** the visitor approves or rejects, with the same reviewer actions as every other run.

The broken versions work by replacing module attributes, so they must never run in the web server's process
while other runs are in flight: the naive copy is built in a child process (`python -m article15.web.story`)
and kept in the web root.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from article15.company.models import CaseTruth, RequestCase
from article15.company.store import DataPaths, load_cases, load_owners, load_people, load_truths
from article15.core.model import ModelRequest, ModelResponse
from article15.core.replay import ReplayProvider

FEATURED = "A15-027"
REPO = Path(__file__).resolve().parents[3]
CASSETTES = REPO / "evals" / "cassettes" / "golden"
# The recorded run took about 29 s; replaying one model call every 1.2 s keeps the steps readable.
REPLAY_STEP_S = 1.2
NAIVE_BREAKS = ("redaction_off", "lookalikes_included")
# English glosses of the featured case's French texts, for visitors who read no French.
GLOSSES: dict[str, dict[str, str]] = {
    "A15-027": {
        "request": "Hello, in accordance with Article 15 of the GDPR, I would like a copy of all the "
        "personal data you hold about me. Regards, Pierre Morin",
        "M000033": "Hello, is the “Braided jute rug 120x170” machine washable? By the way, I received a "
        "parcel by mistake, from order FL-100514, addressed to Julie Faure. Thanks in advance, Pierre",
    }
}
NAIVE_VERSION = 1  # bump when the shape of the naive copy changes, so a kept copy is rebuilt


class PacedReplay:
    """A recorded run, replayed at a pace a person can follow."""

    def __init__(self, path: Path, step_s: float = REPLAY_STEP_S) -> None:
        self.inner = ReplayProvider(path, name="replay")
        self.name = "replay"
        self.step_s = step_s

    async def complete(self, request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(self.step_s)
        return await self.inner.complete(request)


def cassette(case_id: str = FEATURED, base: Path = CASSETTES) -> Path:
    return base / f"{case_id}.jsonl"


def recorded_outcome(case_id: str = FEATURED, base: Path = CASSETTES) -> dict[str, Any]:
    path = base / f"{case_id}.outcome.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# --- the naive copy ----------------------------------------------------------------------------------------


def _key(r: dict[str, Any]) -> tuple[str, str, str]:
    return (r["system"], r["table"], r["record_id"])


async def _naive_pack(data: DataPaths, request: RequestCase, truth: CaseTruth, out: Path) -> dict[str, Any]:
    from article15.desk.runner import Desk, RunConfig, run_case
    from article15.evals.broken import applied
    from article15.evals.oracle import ScriptedProvider, oracle

    desk = Desk.load(load_people(data))
    script = oracle(truth, request, desk.register, desk.people[truth.requester.person_id].name)
    cfg = RunConfig(data=data, out_dir=out, model="scripted", telemetry=False, release_probe=False)
    with ExitStack() as stack:
        for name in NAIVE_BREAKS:
            stack.enter_context(applied(name, data))
        await run_case(request, truth, desk, cfg, ScriptedProvider(script))
    pack_path = out / request.case_id / "pack.json"
    if not pack_path.exists():
        raise RuntimeError("the naive run assembled no copy")
    return dict(json.loads(pack_path.read_text(encoding="utf-8")))


def build_naive(data: DataPaths, case_id: str = FEATURED) -> dict[str, Any]:
    """Run the case with the safeguards off and say what reached the copy that should not have."""
    request = {c.case_id: c for c in load_cases(data)}[case_id]
    truth = {t.case_id: t for t in load_truths(data)}[case_id]
    people = {p.person_id: p for p in load_people(data)}
    owners = {
        (o.record.system.value, o.record.table, o.record.record_id): o.person_id for o in load_owners(data)
    }
    with tempfile.TemporaryDirectory() as tmp:
        pack = asyncio.run(_naive_pack(data, request, truth, Path(tmp)))
    foreign = []
    for r in pack["records"]:
        owner = owners.get(_key(r))
        if owner != truth.subject_id:
            person = people.get(owner or "")
            foreign.append(
                {
                    "system": r["system"],
                    "table": r["table"],
                    "record_id": r["record_id"],
                    "owner": person.name if person and person.customer_id else None,
                    "owner_is_customer": bool(person and person.customer_id),
                    "fields": r["fields"],
                }
            )
    texts = {(r["system"], r["record_id"]): r["fields"] for r in pack["records"]}
    readable = []
    for span in truth.redactions:
        if span.category.value != "third_party":
            continue  # policy v1 hides other people; staff names are policy v2 (experiment E3)
        value = str(texts.get((span.record.system.value, span.record.record_id), {}).get(span.field, ""))
        if span.text in value:
            readable.append(
                {
                    "system": span.record.system.value,
                    "record_id": span.record.record_id,
                    "field": span.field,
                    "text": span.text,
                    "note": span.note,
                    "context": value,
                }
            )
    return {
        "version": NAIVE_VERSION,
        "case_id": case_id,
        "broken_versions": list(NAIVE_BREAKS),
        "records_total": len(pack["records"]),
        "records_own": len(pack["records"]) - len(foreign),
        "foreign_records": foreign,
        "readable_mentions": readable,
    }


def naive(data: DataPaths, root: Path, case_id: str = FEATURED) -> dict[str, Any]:
    """The naive copy, built once in a child process and kept in the web root."""
    path = root / "story" / f"{case_id}.naive.json"
    if path.exists():
        kept = json.loads(path.read_text(encoding="utf-8"))
        if kept.get("version") == NAIVE_VERSION:
            return dict(kept)
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "article15.web.story", str(data.root), case_id, str(path)],
        check=True,
        cwd=REPO,
        timeout=120,
    )
    return dict(json.loads(path.read_text(encoding="utf-8")))


# --- the story ---------------------------------------------------------------------------------------------


def story(data: DataPaths, root: Path, case_id: str = FEATURED, base: Path = CASSETTES) -> dict[str, Any]:
    request = {c.case_id: c for c in load_cases(data)}[case_id]
    truth = {t.case_id: t for t in load_truths(data)}[case_id]
    people = {p.person_id: p for p in load_people(data)}
    counts: dict[str, int] = {"crm": 0, "orders": 0, "helpdesk": 0, "weblogs": 0}
    for k in truth.records:
        counts[k.system.value] = counts.get(k.system.value, 0) + 1
    outcome = recorded_outcome(case_id, base)
    return {
        "case_id": case_id,
        "subject": people[truth.requester.person_id].name,
        "request": request.model_dump(mode="json"),
        "due": str(truth.due),
        "records_by_system": counts,
        "records_total": len(truth.records),
        "gloss": GLOSSES.get(case_id, {}),
        "naive": naive(data, root, case_id),
        "recorded": {
            "available": cassette(case_id, base).exists(),
            "model": outcome.get("model"),
            "elapsed_s": outcome.get("elapsed_s"),
            "cost_usd": (outcome.get("totals") or {}).get("cost_usd"),
            "model_calls": (outcome.get("totals") or {}).get("calls"),
        },
    }


def main(argv: list[str]) -> int:
    data_root, case_id, out = argv
    result = build_naive(DataPaths(Path(data_root)), case_id)
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
