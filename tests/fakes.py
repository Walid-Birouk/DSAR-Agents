"""Test doubles for the agent core. They live in `article15.evals.oracle`, because the eval harness uses the
oracle too (`a15 eval run --provider oracle` checks the graders on a perfect agent, for free)."""

from article15.evals.oracle import (
    PAYLOAD,
    Script,
    ScriptedProvider,
    Turn,
    oracle,
    payload,
    reactive,
    script_of,
)

__all__ = ["PAYLOAD", "Script", "ScriptedProvider", "Turn", "oracle", "payload", "reactive", "script_of"]
