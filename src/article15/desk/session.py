"""One case under way: the case file, the state machine, the scope authority and what code has fetched.

Agents never change the case directly. They call stage tools (case_tools.py), which call `advance`, which
goes through the state machine and its guards. A refused transition comes back as the guard failures, so the
tool can tell the model which rule stopped it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from article15.case.machine import GuardContext, GuardFailure, Machine, TransitionRefused
from article15.case.models import Case, CaseState
from article15.company.models import RequestCase
from article15.company.register import Register
from article15.core.events import EventSink, NullSink
from article15.core.mcp_tools import McpHub
from article15.desk.clock import SimClock
from article15.desk.pack import PackRecord, RecordKey
from article15.notifier.store import NotifierStore
from article15.policy.models import Policy
from article15.scope.authority import ScopeAuthority

MAX_FOLLOW_UPS = 2
MAX_VALIDATION_ROUNDS = 2


class Pause(StrEnum):
    """Why the agents stopped working on the case, when they did so on purpose."""

    AWAITING_REQUESTER = "awaiting_requester"
    AWAITING_APPROVAL = "awaiting_approval"
    WITH_A_PERSON = "with_a_person"
    REJECTED = "rejected"
    PARTIAL = "partial"


@dataclass
class CaseSession:
    request: RequestCase
    case: Case
    policy: Policy
    register: Register
    authority: ScopeAuthority
    notifier: NotifierStore
    hub: McpHub
    clock: SimClock
    approval_secret: bytes
    identity_token: str
    sink: EventSink = field(default_factory=NullSink)
    shadow: bool = False  # experiment E2: guards log instead of block
    scope: set[RecordKey] | None = None
    records: dict[RecordKey, PackRecord] = field(default_factory=dict)
    pack: dict[str, Any] | None = None
    pause: Pause | None = None
    follow_ups_sent: int = 0
    validation_rounds: int = 0
    shadow_log: list[GuardFailure] = field(default_factory=list)

    @property
    def state(self) -> CaseState:
        return self.case.state

    @property
    def request_text(self) -> str:
        return f"{self.request.subject_line}\n{self.request.text}"

    def machine(self) -> Machine:
        return Machine(
            GuardContext(
                now=self.clock(),
                approval_secret=self.approval_secret,
                policy_version=self.policy.version,
            ),
            shadow=self.shadow,
            shadow_log=self.shadow_log,
        )

    def update(self, **fields: Any) -> None:
        self.case = self.case.model_copy(update=fields)

    def advance(self, target: CaseState, note: str = "") -> list[GuardFailure]:
        """Move the case to `target`. Returns the guard failures when the move is refused (else [])."""
        before = self.case.state
        try:
            self.case = self.machine().advance(self.case, target, note)
        except TransitionRefused as exc:
            self.sink.emit(
                "case.transition",
                case_id=self.case.case_id,
                from_state=before.value,
                to_state=target.value,
                status="refused",
                rules=",".join(sorted({f.rule_id for f in exc.failures})),
            )
            return exc.failures
        self.sink.emit(
            "case.transition",
            case_id=self.case.case_id,
            from_state=before.value,
            to_state=target.value,
            status="ok",
            policy_version=self.policy.version,
        )
        return []

    def can_reach(self, target: CaseState) -> bool:
        return not self.machine().check(self.case, target)


def refusal(failures: list[GuardFailure]) -> str:
    return "; ".join(f"{f.rule_id}: {f.reason}" for f in failures)
