"""Policy linter, run in CI.

Errors fail the build:
  * duplicate rule IDs;
  * a rule enforced by a guard that is neither registered nor planned;
  * a registered guard that points to a rule that does not exist (an orphan guard);
  * two rules with different effects for the same action (a contradiction);
  * a rule that applies to no agent prompt that can render.
Warnings list guards that are planned for a later milestone but not built yet.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from article15.case.machine import registered_guards
from article15.policy.models import PROMPT_ONLY, Policy
from article15.policy.render import render_policy_section
from article15.schemas import AgentName
from article15.scope.authority import SCOPE_GUARDS

# Guards that belong to components built in later milestones. Each moves into the registry when built.
PLANNED_GUARDS: dict[str, str] = {
    "pack.electronic_format": "M4: pack assembled as PDF + JSON for electronic requests",
    "a2a.egress_filter": "M5: blocks personal data in messages to the advisor",
}


def code_guards() -> dict[str, str]:
    """Every guard built so far, guard name -> rule ID: the case state machine plus subject scope."""
    return {**{n: g.rule_id for n, g in registered_guards().items()}, **SCOPE_GUARDS}


class Level(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class LintIssue:
    level: Level
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.where}: {self.message}"


def lint(policy: Policy, guards: dict[str, str] | None = None) -> list[LintIssue]:
    """`guards` maps guard name -> rule ID; defaults to the guards registered in the code."""
    registry = guards if guards is not None else code_guards()
    issues: list[LintIssue] = []
    rule_ids = [r.id for r in policy.rules]

    for rid in {r for r in rule_ids if rule_ids.count(r) > 1}:
        issues.append(LintIssue(Level.ERROR, rid, "duplicate rule ID"))

    for rule in policy.rules:
        for name in rule.enforced_by:
            if name == PROMPT_ONLY:
                if len(rule.enforced_by) > 1:
                    issues.append(
                        LintIssue(Level.ERROR, rule.id, "'prompt-only' cannot be combined with guards")
                    )
            elif name in registry:
                if registry[name] != rule.id:
                    issues.append(
                        LintIssue(
                            Level.ERROR,
                            rule.id,
                            f"guard {name} is registered for {registry[name]}, not {rule.id}",
                        )
                    )
            elif name in PLANNED_GUARDS:
                issues.append(
                    LintIssue(Level.WARNING, rule.id, f"guard {name} not built yet ({PLANNED_GUARDS[name]})")
                )
            else:
                issues.append(LintIssue(Level.ERROR, rule.id, f"unknown guard {name}"))

    for name, rid in registry.items():
        if rid not in rule_ids:
            issues.append(
                LintIssue(Level.ERROR, name, f"guard enforces rule {rid}, which is not in the policy")
            )

    effects: dict[str, set[str]] = defaultdict(set)
    for rule in policy.rules:
        effects[rule.action].add(rule.effect)
    for action, found in effects.items():
        if len(found) > 1:
            issues.append(
                LintIssue(Level.ERROR, action, f"contradictory effects for one action: {sorted(found)}")
            )

    for agent in AgentName:
        text = render_policy_section(policy, agent)
        for rule in policy.for_agent(agent):
            if f"[{rule.id}]" not in text:
                issues.append(LintIssue(Level.ERROR, rule.id, f"not rendered for {agent}"))

    return issues
