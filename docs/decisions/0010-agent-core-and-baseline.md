# 0010: The agent core and the single-agent baseline

**Decision.** M2 builds the agent core (`src/article15/core/`) and a desk that one agent (the E1 baseline) runs
end to end (`src/article15/desk/`).

- **Stage tools move the case.** The agent hands each typed result to code through one tool per stage:
  `case_submit_intake`, `_identity`, `_discovery`, `_redaction`, `_letter` and `_validation`, plus
  `case_escalate`, `case_read_pack` and `case_get_status`. Each tool:
  - validates the handoff;
  - checks it against what code knows;
  - moves the case through the state machine and its guards.

  A mistake comes back to the model as one line it can act on: `invalid:` (fix it) or `refused: R-...` (a rule
  stops it). Code does the consequential steps:
  - issues the subject grant and swaps in the new token;
  - sends the follow-up question;
  - fetches the records through the scoped servers;
  - applies the redactions;
  - assembles and hashes the pack.

  In M4 the seven agents reuse these tools, each agent only its own.
- **Redaction by quotation.** The agent quotes the exact text to hide, with an optional occurrence number.
  Code finds the offsets and rejects quotes that are not in the record. After masking, it checks that no
  redacted text is still readable anywhere in the pack. Only then can the case pass guard
  `case.redaction_verified`. Special-category data is quoted the same way and flagged, not hidden.
- **DeepSeek thinking mode is off by default.** `A15_THINKING=low|high` turns it on. With thinking off,
  `tool_choice: "required"` forces a tool call every turn. With it on, the core sends `"auto"`, because
  DeepSeek refuses `"required"` in thinking mode, and it sends every earlier `reasoning_content` back, as
  DeepSeek requires.
- **Per-case caps: 60 model calls, 1M input tokens, 15 minutes, USD 0.30.** They are checked before every
  model call. A run that hits a cap stops, and the case goes to NEEDS_HUMAN.
- **A template requester.** Follow-up questions are answered by deterministic French and English templates.
  What the requester says depends on what the generator knows about them (`RequesterProfile`):
  - a genuine customer gives their order number and postcode;
  - an impostor never knows an order;
  - a representative has no mandate.

  The requester answers once, a simulated day later. The case resumes with a fresh identity-stage grant.
- **Only code writes to the requester.** The agent does not get `notify_post_message`. The follow-up question
  comes from the identity decision and is sent by code, so no model can send anything to anyone.
- **In-process MCP for runs.** A run talks to the five servers through the MCP SDK's in-memory transport. The
  protocol is the real one, and the case's simulated clock is passed in. Replays are therefore byte-identical:
  grant expiry and message times come from the case clock, not the machine. `a15 serve` (stdio processes)
  stays for the deployment, which runs on real time (M6/M7).
- **Record and replay.** Every live call can be recorded to a cassette (`a15 run --record DIR`), one per
  case.
  - A cassette stores only the messages added since the previous call, so it grows with the conversation,
    not with its square.
  - Replay matches requests by the SHA-256 of their canonical JSON. A request that was never recorded fails
    and names the first message that differs.
  - A run's outcome has a fingerprint: states, tool calls, pack hash, tokens and cost. A replay must
    reproduce it exactly (`a15 run --replay DIR`; `tests/test_cassettes.py` in CI).
- **The letter's Article 15 information comes from Ferlane's privacy register** (`company/register.yaml`),
  which is rendered into the prompt in French and English. Every entry names its source:
  - **CNIL référentiel "gestion des activités commerciales":** purposes, legal bases, retention periods and
    recipient categories.
  - **CNIL cookies recommendation, consolidated January 2026 (p. 10):** audience-measurement cookies last 13
    months; the data they collect is kept 25 months.
  - **cnil.fr/fr/adresser-une-plainte:** the complaint channel and postal address.

  Ferlane's own choices, made by the project owner:
  - no transfers outside the EEA;
  - no automated decisions;
  - recipients named as categories, not invented companies;
  - web logs described as audience measurement, kept 25 months.

  The privacy contact `privacy@ferlane.example` is a placeholder on the fictional domain.
- **Five cases for the live M2 run**, all from the dev split: A15-004, A15-017, A15-122, A15-045 and A15-107.
  Cases with an address that needs proof of control wait for the M4 state-machine edge (decision 0008).

**Why.**
- The model judges and code decides. A stage tool is the one point where a model's output becomes a fact
  about the case, so every guard, every scope check and every refusal message sits there, once.
- Models count characters badly. Quoting moves the offset arithmetic into code, where it can be tested,
  including with a property test.
- Thinking mode costs output tokens, makes recordings larger and forbids forcing a tool call. Experiment E4
  can measure what it adds.
- Caps of about twice the estimated cost of a case leave room for retries and still stop a runaway loop
  early.
- Template replies cost nothing, are the same on every run and keep the eval labels exact.
- Replay only proves anything if the run is deterministic, which is why the case clock and the in-process
  servers are needed.

**Given up.**
- The single agent judges its own pack (stage 6). The independent validator and its deterministic claims
  cross-check come in M4, for both arms of E1.
- Requester replies are not natural language. A model-played requester can be added later if the evals show
  that templates are too easy.
- Runs do not exercise the process boundary between the desk and the servers. The scope guarantees are
  tested without it (`tests/test_scope.py`).
- A refused request (REJECTED) has its reason recorded, but no refusal letter is drafted yet (Art. 12(4)).
  That comes with the letter work in M4.

**Tested by.**
- `tests/test_desk.py`: an oracle agent, which plays a correct case from the ground truth, drives the real
  desk. The tests check:
  - the pack holds exactly the truth's records;
  - no third-party text is readable;
  - the model never sees a case token;
  - events carry no personal data;
  - follow-ups, impostors and representatives end in the expected state;
  - record and replay give the same fingerprint, and a changed prompt is a replay miss.
- `tests/test_stage_tools.py`: wrong moves are refused, and broken designs turn the checks red:
  - skipped stages;
  - records before verification;
  - an impostor claimed as verified;
  - order evidence the requester never wrote;
  - a foreign record;
  - a token passed by the model;
  - a misquoted span;
  - a name left in another message;
  - masking switched off.
- `tests/test_core.py` and `tests/test_wire.py`:
  - caps;
  - errors returned as results;
  - nudges;
  - agents as tools;
  - replay;
  - pricing windows;
  - the SDK's wire format (the `thinking` switch, `reasoning_content` sent back, the cache fields).
