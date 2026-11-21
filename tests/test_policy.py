"""Policy file, renderer and linter.

Includes deliberately broken policies: a linter that has never been seen to fail proves nothing.
"""

import pytest

from article15.policy.lint import Level, lint
from article15.policy.models import Effect, Policy, Rule, load_policy
from article15.policy.render import render_policy_section
from article15.schemas import AgentName


def errors(policy: Policy, guards: dict[str, str] | None = None) -> list[str]:
    return [str(i) for i in lint(policy, guards) if i.level is Level.ERROR]


def with_rules(policy: Policy, *rules: Rule) -> Policy:
    return policy.model_copy(update={"rules": [*policy.rules, *rules]})


@pytest.fixture
def policy() -> Policy:
    return load_policy()


def test_shipped_policy_has_no_errors(policy: Policy) -> None:
    assert errors(policy) == []


def test_every_agent_prompt_renders(policy: Policy) -> None:
    for agent in AgentName:
        text = render_policy_section(policy, agent)
        assert text.startswith(f"POLICY dsar-access v{policy.version}")
        for rule in policy.for_agent(agent):
            assert f"[{rule.id}]" in text


def test_render_is_deterministic(policy: Policy) -> None:
    assert render_policy_section(policy, AgentName.COORDINATOR) == render_policy_section(
        policy, AgentName.COORDINATOR
    )


def test_release_rule_reaches_coordinator(policy: Policy) -> None:
    assert "[R-RELEASE-APPROVAL]" in render_policy_section(policy, AgentName.COORDINATOR)
    assert "[R-RELEASE-APPROVAL]" not in render_policy_section(policy, AgentName.REDACTION)


# --- broken policies must be caught -------------------------------------------------------------------


def rule(**overrides: object) -> Rule:
    base: dict[str, object] = dict(
        id="R-TEST",
        title="t",
        source="s",
        action="test_action",
        effect=Effect.REQUIRED,
        applies_to=[AgentName.LETTER],
        enforced_by=["prompt-only"],
        text="x",
    )
    return Rule.model_validate({**base, **overrides})


def test_catches_contradiction(policy: Policy) -> None:
    # Same action as R-RELEASE-APPROVAL but a different effect: the "approval required here, safe there" bug.
    broken = with_rules(policy, rule(id="R-RELEASE-AUTO", action="release_pack", effect=Effect.REQUIRED))
    assert any("contradictory effects" in e for e in errors(broken))


def test_catches_unknown_guard(policy: Policy) -> None:
    broken = with_rules(policy, rule(enforced_by=["case.does_not_exist"]))
    assert any("unknown guard" in e for e in errors(broken))


def test_catches_duplicate_ids(policy: Policy) -> None:
    broken = with_rules(policy, rule(id="R-DEADLINE", action="other"))
    assert any("duplicate rule ID" in e for e in errors(broken))


def test_catches_orphan_guard(policy: Policy) -> None:
    rules = [r for r in policy.rules if r.id != "R-SPECIAL"]
    assert any("not in the policy" in e for e in errors(policy.model_copy(update={"rules": rules})))


def test_catches_guard_mapped_to_wrong_rule(policy: Policy) -> None:
    from article15.policy.lint import code_guards

    for name in ("case.release_approval", "mcp.subject_scope"):
        guards = code_guards()
        guards[name] = "R-DEADLINE"
        assert any(f"guard {name} is registered for R-DEADLINE" in e for e in errors(policy, guards))


def test_scope_guards_are_registered(policy: Policy) -> None:
    from article15.policy.lint import code_guards

    guards = code_guards()
    assert guards["mcp.subject_scope"] == "R-NO-FOREIGN-DATA"
    assert guards["scope.identity_evidence"] == "R-IDENTITY"
    assert errors(policy, guards) == []


def test_catches_prompt_only_mixed_with_guard(policy: Policy) -> None:
    broken = with_rules(policy, rule(enforced_by=["prompt-only", "case.release_approval"]))
    assert errors(broken)


def test_planned_guards_are_warnings_not_errors(policy: Policy) -> None:
    warnings = [i for i in lint(policy) if i.level is Level.WARNING]
    assert {w.where for w in warnings} == {"R-COPY", "R-MINIMISE-EGRESS"}


def test_rule_validation_rejects_blank_text() -> None:
    with pytest.raises(ValueError):
        rule(text="   ")
