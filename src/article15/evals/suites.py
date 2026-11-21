"""Eval suites: which cases a run covers (PLAN.md 8.1, 8.7).

- `smoke`: 20 dev cases, stratified so that every kind of case appears. The live smoke run that must come
  with every prompt, policy or model change (8.7). It starts with the five M2 cases, so their recordings are
  reused.
- `dev` / `test`: the two halves of the split. Prompts are tuned on dev; only test is reported.
- `full`: all 160 cases.
- `golden`: the 20 cases the project owner checks by hand (8.1), from both halves.

Selection is deterministic: the same generated data always gives the same suite.
"""

from __future__ import annotations

from collections.abc import Iterable

from article15.company.models import CaseKind, CaseTruth, Split
from article15.desk.runner import M2_CASES

K = CaseKind

# How many dev cases of each kind the smoke suite takes (20 in all). Every kind appears at least once;
# standard cases and injections get more because most regressions show there.
SMOKE_QUOTA: dict[CaseKind, int] = {
    K.STANDARD: 5,
    K.IDENTITY_OTHER_EMAIL: 2,
    K.IDENTITY_IMPOSTOR: 1,
    K.IDENTITY_REPRESENTATIVE: 1,
    K.IDENTITY_NO_ACCOUNT: 1,
    K.EDGE_REPEAT: 1,
    K.EDGE_OTHER_RIGHTS: 1,
    K.EDGE_EXTENSION: 1,
    K.ADV_INJECTION_REQUEST: 3,
    K.ADV_INJECTION_RECORD: 2,
    K.ADV_MALFORMED: 1,
    K.ADV_OVERSIZED: 1,
}
SMOKE_SIZE = sum(SMOKE_QUOTA.values())
SUITES = ("smoke", "dev", "test", "full", "golden", "m2")


def smoke(truths: Iterable[CaseTruth]) -> list[str]:
    dev = sorted(
        (t for t in truths if t.split is Split.DEV), key=lambda t: (t.case_id not in M2_CASES, t.case_id)
    )
    chosen: list[str] = []
    for kind, quota in SMOKE_QUOTA.items():
        of_kind = [t.case_id for t in dev if t.kind is kind]
        if len(of_kind) < quota:
            raise ValueError(
                f"the dev split has {len(of_kind)} {kind} case(s); the smoke suite needs {quota}"
            )
        chosen += of_kind[:quota]
    return sorted(chosen)


def select(name: str, truths: Iterable[CaseTruth]) -> list[str]:
    """The case IDs of suite `name`, sorted."""
    truths = list(truths)
    if name == "smoke":
        return smoke(truths)
    if name == "m2":
        return sorted(M2_CASES)
    if name == "full":
        return sorted(t.case_id for t in truths)
    if name == "golden":
        return sorted(t.case_id for t in truths if t.golden)
    if name in ("dev", "test"):
        return sorted(t.case_id for t in truths if t.split is Split(name))
    raise KeyError(f"unknown suite {name!r}; choose one of {', '.join(SUITES)}")
