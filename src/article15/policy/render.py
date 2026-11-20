"""Render the policy rules that apply to an agent into its prompt.

Rules are never copied into prompts by hand, so two agents can't end up with different versions of a rule.
The output is deterministic (stable order, no timestamps) so it can sit in the cached prefix of the prompt.
"""

from __future__ import annotations

from article15.policy.models import Policy, Rule
from article15.schemas import AgentName

_ENFORCEMENT_NOTE = {
    True: "(judgment rule)",
    False: "(also enforced by the system: an action that breaks it will be refused)",
}


def _render_rule(rule: Rule) -> str:
    return f"- [{rule.id}] {rule.title} {_ENFORCEMENT_NOTE[rule.prompt_only]}\n  {rule.text}"


def render_policy_section(policy: Policy, agent: AgentName) -> str:
    rules = policy.for_agent(agent)
    lines = [f"POLICY {policy.policy_id} v{policy.version} — rules that apply to you:"]
    lines += [_render_rule(r) for r in sorted(rules, key=lambda r: r.id)]
    return "\n".join(lines)
