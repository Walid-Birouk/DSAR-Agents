# 0012: The multi-agent desk, proof of control, the claims check and shadow mode

**Status: accepted** on 2026-11-23, after starting as provisional choices ([0018](0018-provisional-choices-settled.md)
says what changed).

**Decision.** M4 adds the seven-agent arm of experiment E1 (`src/article15/desk/team.py`, `roles/`) and the
pieces both arms were waiting for.

- **One coordinator and six specialists, as tools** (PLAN.md 5.2).
  - Each specialist has its own role prompt, the policy rules that apply to it (rendered from `policy.yaml`),
    and only its own tools:

    | Specialist | Tools |
    |---|---|
    | intake | `case_submit_intake` |
    | identity | identity checks, the requester's replies, `case_submit_identity` |
    | discovery | the eight record tools, `case_request_address_link`, `case_submit_discovery` |
    | redaction | `helpdesk_read_ticket`, `case_submit_redaction` |
    | letter | `case_submit_letter` (the register is in its prompt) |
    | validator | `case_read_pack`, `case_submit_validation` |

  - The coordinator has the six specialists, `case_get_status` and `case_escalate`. It cannot submit a stage,
    read a record or write to anyone.
  - *(Open choice:)* a successful stage submission ends the specialist's run. Code then adds a one-line status
    to its answer (stage, next step, intake flags, the validator's issues).
  - *(Open choice:)* specialist caps are 16 calls, 400k input tokens, 5 minutes and USD 0.12 each, all inside
    the case's own caps.
- **Slices built by code** (PLAN.md 5.5). Specialists start fresh and get only what their stage needs:
  - Intake and Identity get the request.
  - Discovery gets the verified customer.
  - Redaction gets the tickets to read.
  - Letter gets record counts, the redaction and special-category counts, and the validator's issues.
  - The validator gets the case ID.

  *(Open choice:)* the coordinator sees the request too, marked untrusted, because it decides on escalation
  (repeated or excessive requests, other rights). A test checks that no personal value reaches the slices of
  Discovery, Redaction, Letter or Validator.
- **Proof of control, in the desk** (the M4 edge of decision 0008).
  - Discovery calls `case_request_address_link` with the address and the ticket of the customer's message.
  - Code mails a one-time code to that address. It asks the requester (at their own address) to send it back,
    and the case goes IDENTITY_VERIFIED to AWAITING_REQUESTER.
  - When the reply comes, code (never an agent) tries the codes in it against each pending address, links the
    confirmed ones, and returns the case to IDENTITY_VERIFIED for a new search.
  - The simulated requester can read only their own mailboxes. *(Open choice:)* the reply comes 2 simulated
    hours later, inside the 24-hour subject grant.
- **The claims cross-check** (PLAN.md 7, rule R-VALIDATION) runs when the pack is assembled. It checks:
  - the record count claimed, and every "n records" the letter states;
  - every record ID it names;
  - every Art. 15(1) item, with the register checklist (now `desk/checklist.py`, shared with the graders);
  - the letter's `items_covered` against its text.

  New guard `case.claims_verified` refuses VALIDATED while a problem remains, so an approving validator is
  overruled. `case_read_pack` shows the results. *(Policy change:)* R-VALIDATION now lists both guards.
- **R-COPY** guard `pack.electronic_format`: the copy must be in a commonly used electronic format. *(Open
  choice:)* the copy is JSON now; the PDF download comes with the web app (M6).
- **Shadow mode for E2** (`--shadow`): the state machine's guards log what they would block, and let it pass.
  *Changed on 2026-11-23 (was P14):* shadow mode also covers the stage tools' policy checks, the
  redaction verifier (R-OTHERS, counted once at the transition) and the follow-up limit (R-IDENTITY). The
  scoped servers stay enforced, so E2 measures the checks, not the scoping.
  - *(Open choice:)* the scoped servers and the stage tools' own checks stay enforced, because they are the
    infrastructure being protected, not policy prompts.
  - Grades count refused rule violations and shadowed ones; E2 compares "attempted" and "let through".
- **The release service** (`desk/release.py`): approve (a signed approval of the exact pack), request
  changes, reject, extend, escalate, and release (to the outbox, then CLOSED). Guard `case.release_approval`
  re-checks the signature against the current pack, so a pack changed after approval cannot be sent.
- **Release probe in evals** *(open choice)*. Every case that reaches AWAITING_APPROVAL is then pushed to
  APPROVED and released with no approval, as an injected "human review is disabled" or a bug would. The
  guards must refuse, and the release gate measures it. A fifth broken version, `release_guard_off`, shows the
  gate turning red. The probe is skipped in shadow mode.
- **Experiments**: `a15 eval run --arm single|team [--shadow]`, then
  `a15 eval experiment E1|E2 A/report.json B/report.json`.

**Why.**
- Each agent seeing only its tools and its slice is the point of the multi-agent design. It makes "no agent
  uses a tool outside its set" structural rather than a hope.
- Moving the claims check into a guard makes the validator's verdict advisory where code can decide.
- A release probe turns a gate that would otherwise always read 0 into one that is tested on every run.

**Given up.**
- No model run of either arm yet (no API key): E1 and E2 have their harness, but not their numbers. The oracle
  is green on all 160 cases in both arms.
- The redaction specialist has no entity detector yet. Presidio is planned with the telemetry scrubber (M5).
- A refusal letter for REJECTED cases (Art. 12(4)) is still not drafted; the reason is recorded.
- The DPO advisor (A2A) that the coordinator consults on edge cases comes in M5. Until then the coordinator
  escalates them to a person.

**Tested by.**
- `tests/test_team.py`:
  - the team oracle on the smoke suite;
  - the tool sets as declared;
  - the slices hold no personal data;
  - an address the requester cannot read is never linked;
  - each false claim is caught;
  - shadow mode lets a violation through and counts it;
  - E1 and E2 comparisons;
  - release needs an approval of the exact pack, and special-category acknowledgement;
  - the CLI.
- `tests/test_evals.py`: five broken versions, and the oracle smoke now fully green (proof of control included).
- `tests/test_machine.py`: the new guards.
