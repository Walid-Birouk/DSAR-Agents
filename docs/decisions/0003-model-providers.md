# 0003: DeepSeek for development, Mistral EU for the public demo

**Decision.**
- One OpenAI-compatible provider layer with three settings: DeepSeek, Mistral and replay.
- Development and eval runs use DeepSeek (`deepseek-flash`, with `deepseek-v4-pro` where evals show it is
  needed).
- The public demo uses Mistral's EU endpoint (`api.eu.mistral.ai`).

**Why.**
- DeepSeek is very cheap: about $0.02-0.05 per case. Development data is fully synthetic, so where it is
  processed does not matter.
- DeepSeek's privacy policy (updated 10 Feb 2026) says data is processed and stored in the People's Republic of
  China.
- Public-demo visitors type free text, and a project about careful handling of personal data should keep that
  text in the EU. Mistral's EU endpoint runs in EU/EFTA data centres at a similar price.
- Replay makes CI free and deterministic.

**Watch-outs from the DeepSeek docs.**
- Invalid JSON output comes back as blank content, so every structured output is validated against its schema
  and retried.
- Strict tool schemas are in beta.

**Given up.** A single provider everywhere. Experiment E4 reports the quality and cost difference between the
two.
