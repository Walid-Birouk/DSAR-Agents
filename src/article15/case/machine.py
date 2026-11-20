"""The case state machine.

Agents decide *what to do next*; this module decides *what is allowed*. A transition happens only if it is
listed in ALLOWED and every guard registered for the target state passes. Each guard enforces a rule from
policy/policy.yaml (its `rule_id`), so the policy linter can check that no enforced rule lacks a guard.

In shadow mode (used by experiment E2) failing guards are recorded but do not block, which measures how
often the agents would have broken a rule.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from article15.case.approvals import verify_approval
from article15.case.models import Case, CaseState, Transition
from article15.schemas import IdentityOutcome, Verdict

S = CaseState

ALLOWED: dict[CaseState, frozenset[CaseState]] = {
    S.RECEIVED: frozenset({S.INTAKE_DONE, S.NEEDS_HUMAN}),
    S.INTAKE_DONE: frozenset({S.IDENTITY_CHECK, S.NEEDS_HUMAN}),
    S.IDENTITY_CHECK: frozenset({S.IDENTITY_VERIFIED, S.AWAITING_REQUESTER, S.REJECTED, S.NEEDS_HUMAN}),
    S.AWAITING_REQUESTER: frozenset({S.IDENTITY_CHECK, S.REJECTED, S.EXTENDED}),
    S.IDENTITY_VERIFIED: frozenset({S.DISCOVERY_DONE, S.PARTIAL, S.NEEDS_HUMAN, S.EXTENDED}),
    S.DISCOVERY_DONE: frozenset({S.REDACTION_DONE, S.NEEDS_HUMAN, S.EXTENDED}),
    S.PARTIAL: frozenset({S.IDENTITY_VERIFIED, S.NEEDS_HUMAN, S.EXTENDED}),
    S.REDACTION_DONE: frozenset({S.DRAFT_READY, S.NEEDS_HUMAN}),
    S.DRAFT_READY: frozenset({S.VALIDATED, S.REDACTION_DONE, S.NEEDS_HUMAN}),
    S.VALIDATED: frozenset({S.AWAITING_APPROVAL}),
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.DRAFT_READY, S.REJECTED, S.EXTENDED, S.NEEDS_HUMAN}),
    S.APPROVED: frozenset({S.RELEASED}),
    S.RELEASED: frozenset({S.CLOSED}),
    S.NEEDS_HUMAN: frozenset(
        {
            S.INTAKE_DONE,
            S.IDENTITY_CHECK,
            S.IDENTITY_VERIFIED,
            S.DRAFT_READY,
            S.REJECTED,
            S.EXTENDED,
            S.CLOSED,
        }
    ),
    S.EXTENDED: frozenset(
        {S.IDENTITY_CHECK, S.IDENTITY_VERIFIED, S.DISCOVERY_DONE, S.AWAITING_APPROVAL, S.NEEDS_HUMAN}
    ),
    S.REJECTED: frozenset({S.CLOSED}),
    S.CLOSED: frozenset(),
}


@dataclass(frozen=True)
class GuardContext:
    now: datetime
    approval_secret: bytes
    policy_version: str


@dataclass(frozen=True)
class GuardFailure:
    guard: str
    rule_id: str
    reason: str


GuardFn = Callable[[Case, CaseState, GuardContext], str | None]
"""Returns None when the transition is fine, otherwise the reason it is refused."""


@dataclass(frozen=True)
class Guard:
    name: str
    rule_id: str
    targets: frozenset[CaseState]
    check: GuardFn


GUARDS: dict[str, Guard] = {}


def guard(name: str, rule_id: str, *targets: CaseState) -> Callable[[GuardFn], GuardFn]:
    def register(fn: GuardFn) -> GuardFn:
        GUARDS[name] = Guard(name, rule_id, frozenset(targets), fn)
        return fn

    return register


# --- guards -------------------------------------------------------------------------------------------


@guard("case.edge_cases_need_a_person", "R-EXCESSIVE", S.IDENTITY_CHECK)
def _edge_cases(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    if (case.flags.possibly_excessive or case.flags.other_rights_requested) and case.human_decision is None:
        return "possibly excessive request or other rights: a person must decide before processing continues"
    return None


@guard("case.identity_verified", "R-IDENTITY", S.IDENTITY_VERIFIED)
def _identity(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    d = case.identity
    if d is None or d.outcome is not IdentityOutcome.VERIFIED or not d.subject_id or not d.evidence:
        return "identity is not verified with evidence"
    return None


@guard("case.discovery_complete", "R-SCOPE", S.DISCOVERY_DONE)
def _discovery(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    r = case.discovery
    if r is None or not r.ok:
        return "no successful discovery result"
    if case.identity is None or r.subject_id != case.identity.subject_id:
        return "discovery was run for a different subject than the verified one"
    if r.systems_failed:
        return f"systems failed: {', '.join(r.systems_failed)}; the case must go to PARTIAL"
    return None


@guard("case.redaction_verified", "R-OTHERS", S.REDACTION_DONE)
def _redaction(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    if case.redaction is None or not case.redaction.ok:
        return "no successful redaction plan"
    if case.redaction.unresolved:
        return "some mentions of people are unresolved"
    if not case.redaction_check_passed:
        return "the redaction verifier has not passed"
    return None


@guard("case.validation_approved", "R-VALIDATION", S.VALIDATED)
def _validation(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    v = case.validation
    if v is None or v.verdict is not Verdict.APPROVED:
        return "the validator has not approved the pack"
    if case.pack_hash is None:
        return "the pack has not been assembled"
    return None


@guard("case.release_approval", "R-RELEASE-APPROVAL", S.APPROVED, S.RELEASED)
def _release(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    if case.approval is None or case.pack_hash is None:
        return "no human approval"
    if not verify_approval(ctx.approval_secret, case.approval, case.case_id, case.pack_hash):
        return "the approval does not match this exact pack"
    return None


@guard("case.special_category_acknowledged", "R-SPECIAL", S.APPROVED)
def _special(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    if case.flags.special_category_found and not (
        case.approval and case.approval.special_category_acknowledged
    ):
        return "special-category data was found; the reviewer must acknowledge it explicitly"
    return None


@guard("case.extension_in_time", "R-DEADLINE", S.EXTENDED)
def _extension(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    if not case.extension_reason:
        return "an extension needs a recorded reason"
    if ctx.now.date() > case.deadlines.extension_notice_by:
        return "an extension must be announced within the first month"
    return None


@guard("case.rejection_explained", "R-NO-ACTION", S.REJECTED)
def _rejection(case: Case, target: CaseState, ctx: GuardContext) -> str | None:
    if not case.rejection_reason:
        return "a rejection needs a recorded reason (the requester must be told why)"
    return None


# --- transitions --------------------------------------------------------------------------------------


class TransitionRefused(Exception):
    def __init__(self, case_id: str, target: CaseState, failures: list[GuardFailure]):
        self.failures = failures
        reasons = "; ".join(f"{f.rule_id}: {f.reason}" for f in failures)
        super().__init__(f"case {case_id}: transition to {target} refused ({reasons})")


@dataclass
class Machine:
    ctx: GuardContext
    shadow: bool = False
    shadow_log: list[GuardFailure] = field(default_factory=list)

    def check(self, case: Case, target: CaseState) -> list[GuardFailure]:
        if target not in ALLOWED[case.state]:
            return [
                GuardFailure(
                    "case.allowed_transitions",
                    "R-STAGE-ORDER",
                    f"{case.state} -> {target} is not an allowed transition",
                )
            ]
        failures = []
        for g in GUARDS.values():
            if target in g.targets and (reason := g.check(case, target, self.ctx)) is not None:
                failures.append(GuardFailure(g.name, g.rule_id, reason))
        return failures

    def advance(self, case: Case, target: CaseState, note: str = "") -> Case:
        failures = self.check(case, target)
        if failures:
            if not self.shadow:
                raise TransitionRefused(case.case_id, target, failures)
            self.shadow_log.extend(failures)
        record = Transition(
            from_state=case.state,
            to_state=target,
            at=self.ctx.now,
            policy_version=self.ctx.policy_version,
            note=note,
        )
        return case.model_copy(update={"state": target, "history": [*case.history, record]})


def registered_guards() -> dict[str, Guard]:
    return {
        **GUARDS,
        "case.allowed_transitions": Guard(
            "case.allowed_transitions", "R-STAGE-ORDER", frozenset(), lambda c, t, x: None
        ),
    }
