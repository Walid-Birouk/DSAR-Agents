"""Policy as code: one versioned file drives both the agents' prompts and the code guards."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from article15.schemas import AgentName

DEFAULT_POLICY_PATH = Path(__file__).with_name("policy.yaml")
PROMPT_ONLY = "prompt-only"
SINGLE_AGENT_ROLES = frozenset(AgentName) - {AgentName.SINGLE, AgentName.ADVISOR}


class Effect(StrEnum):
    REQUIRED = "required"
    FORBIDDEN = "forbidden"
    REQUIRES_HUMAN_DECISION = "requires_human_decision"
    REQUIRES_HUMAN_APPROVAL = "requires_human_approval"


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^R-[A-Z0-9-]+$")
    title: str
    source: str
    action: str
    effect: Effect
    applies_to: list[AgentName] = Field(min_length=1)
    enforced_by: list[str] = Field(min_length=1)
    text: str

    @field_validator("text", "source", "title")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @property
    def prompt_only(self) -> bool:
        return self.enforced_by == [PROMPT_ONLY]


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    rules: list[Rule] = Field(min_length=1)

    def for_agent(self, agent: AgentName) -> list[Rule]:
        """The rules an agent's prompt carries. The single-agent baseline (E1) does every job except the
        advisor's, so it carries the rules of all of them."""
        roles = SINGLE_AGENT_ROLES if agent is AgentName.SINGLE else frozenset({agent})
        return [r for r in self.rules if roles & set(r.applies_to)]

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> Policy:
    return Policy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
