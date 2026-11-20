# 0006: Case lifecycle as a state machine in code

**Decision.** A case moves through explicit states (`src/article15/case/machine.py`). The coordinator agent
chooses what to do next, but a transition happens only if it is allowed and every guard for the target state
passes.

**Why.** The legal order is not a matter of judgment: identity before discovery, validation before review, human
approval before release. Putting it in code keeps it out of the model's hands. The model still handles the
unusual paths: follow-up questions, retries, escalation.

**Shadow mode.** For experiment E2 the machine can record failing guards instead of blocking them, which measures
how often a prompt-only design would have broken a rule.

**Given up.** A fully model-driven workflow. That would be more flexible, but nobody can prove it follows the law.
