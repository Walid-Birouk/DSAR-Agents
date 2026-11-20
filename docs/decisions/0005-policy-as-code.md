# 0005: One policy file drives prompts and code guards

**Decision.** Every operational and legal rule lives once, in `src/article15/policy/policy.yaml`.
- A renderer writes the rules that apply to an agent into its prompt.
- Code guards enforce the same rules.
- A linter in CI checks that:
  - every enforced rule has a guard, and every guard has a rule;
  - no two rules contradict each other for the same action;
  - every prompt section renders.

**Why.** Rules copied by hand into several prompts drift apart, and a model can skip a rule written only in a
prompt. With one source, prompts can't disagree, and the guards catch what the model misses. Experiment E2 measures
how often that happens.

**Given up.** Free-form prompt editing. Changing a rule means changing the policy file and passing the eval gate.
