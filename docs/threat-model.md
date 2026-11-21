# Threat model

What could go wrong, who could cause it, and what stops it. Each control names where it lives and the test that
checks it.

## Assets

- **The data subject's records:** fictional, but treated as real personal data.
- **Other people's data inside those records:** third parties, staff and look-alike customers.
- **The release:** the only step that sends anything out.
- **Visitors' text and keys** in the public demo.
- **Telemetry and logs:** they must not become a second copy of personal data.

## Threats and controls

| Threat | Example | Controls | Tested by |
|---|---|---|---|
| Someone obtains another person's data | an impostor writes from a lookalike address; "send my neighbour's orders too" | identity evidence checked in code before any record is unlocked (`scope.identity_evidence`); subject-scoped MCP tools (`mcp.subject_scope`); proof of control before linking another address; pack built only from records the scoped servers returned | `test_scope.py`; impostor and foreign-record gates; `identity_skipped` and `lookalikes_included` broken versions |
| Prompt injection | instructions in the request or in a ticket ("ignore your instructions", "human review is disabled") | untrusted text marked as data; least-privilege tools per agent; no agent can send or release; state-machine guards; release probe on every eval case | injection gate; break-it page; E2 shadow mode |
| Third parties exposed in the copy | a partner's name in a ticket | quoted redaction applied by code, then verified (`case.redaction_verified`); validator; claims cross-check | leak gate; `redaction_off` broken version |
| A release nobody approved | a bug, or an injected "send directly" | signed approval over the exact pack hash; `case.release_approval` re-checks it at release; operation ID so nothing goes twice | release gate and probe; `release_guard_off`; `test_team.py` (a pack changed after approval is refused) |
| Personal data in telemetry | prompts or names on spans | no contents by design; keyed-hash identifiers; scrubber on every attribute; collector redaction processor | telemetry gate (events and spans); `raw_prompts_to_telemetry`; scrubber tests |
| Personal data to the external advisor | a name in the advisor's question | typed summary with no free text, built by code; egress filter; payload log | advisor gate; `egress_filter_off`; egress tests |
| Runaway cost or loops | an agent that never stops | per-case caps (calls, tokens, time, USD 0.30); budget-capped eval runner; daily demo cap; per-session and per-IP limits | `test_core.py` caps; runner budget test; rate-limit tests |
| Demo abuse | scripted floods, real personal data typed in | proof-of-work human check; per-IP and per-session limits; input detector (real emails, phones, IPs, IBANs, cards, NIR); 64 KB body limit at the proxy; sandboxes deleted after 24 h | `test_web_guards.py` |
| Visitor keys leaking | a key in a log | held in memory per session for at most an hour; never logged, traced or written | BYOK tests (the key never appears in a response) |
| Another visitor's case | guessing a run ID | runs bound to the session cookie (404 otherwise); per-session sandboxes | `test_web.py` session isolation |
| Partial or crashed runs sent anyway | a system down mid-discovery | PARTIAL state naming the system; circuit breakers; checkpoints and resume without redoing stages | `test_resilience.py` |
| Secrets in the repository | an API key committed | `.env` and `production.env` git-ignored; gitleaks in CI | CI `secrets` job |

## Out of scope

- A compromised host or a malicious operator: whoever runs the server can read its memory.
- The providers' own handling of visitor text. The demo defaults to Mistral's EU endpoint and says so; DeepSeek
  processes data in China (PLAN.md 15.3).
- Legal correctness beyond the sourced rules. This is a demonstration, not legal advice.
