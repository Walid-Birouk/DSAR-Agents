# 0008: Subject-scoped access to the company systems

**Decision.** The MCP servers return only records of one verified person. Code, not an agent, decides who that is
and when the scope may grow (`src/article15/scope/`).

- **Opaque case tokens.** A token is a random grant ID with an HMAC signature (`a15g.<id>.<sig>`). It carries no
  personal data. The identifiers it unlocks sit in a scope store that only the control plane writes and the
  servers read. Grants expire after 24 hours (`A15_GRANT_TTL_HOURS`) and can be revoked per case. In M2 the tool
  layer injects the token, so the model never sees it.
- **Two stages.** An identity-stage grant allows only identity checks (`crm_check_identity`,
  `orders_check_reference`) and messages to the requester. A subject grant unlocks the person's records.
- **Identity evidence checked in code** (guard `scope.identity_evidence`, rule R-IDENTITY). A subject grant is
  issued only if:
  - the request came from the customer's registered email; or
  - the requester gave an order number of theirs together with that order's postcode. Both values must appear in
    text the requester wrote (the request or a reply), so an agent cannot supply them.
- **Structural links only.** A subject grant covers:
  - the customer ID and registered email (orders, tickets, guest checkouts under that email);
  - browser cookies the customer logged in with, provided no other account ever logged in with them.

  Nothing is linked by IP address, because an IP can be shared.
- **Proof of control for anything else** (guard `mcp.subject_scope`, rule R-NO-FOREIGN-DATA). To add an address
  found only in free text:
  - The address must appear in a message the customer wrote, in a ticket already in scope.
  - The address must not be registered to another customer account.
  - The control plane mails a one-time 6-digit code to that address through the notifier. The code is valid for
    7 days, three wrong attempts void it, and a case can link at most 3 extra addresses.
  - Agents read only the requester's replies, never other mailboxes. The address therefore joins the scope only
    if the requester controls that mailbox and sends the code back.
- **Strict, bounded tools.**
  - Tool names carry the server name.
  - Schemas set `additionalProperties: false`, and unknown arguments are rejected with a readable error (the SDK
    drops them silently by default).
  - Every call has a 5 s timeout (`A15_TOOL_TIMEOUT_S`).
  - Errors are one line: `refused:`, `invalid:`, `timeout:` or `unavailable:`.

**Why.**
- "Even a tricked agent can't read anyone else's records" (PLAN.md 5.4) has to hold in code, not in a prompt.
- Linking a second address from free text is the one place where an agent's judgment could open someone else's
  data. For example, an attacker could plant a relative's address in their own ticket. Proof of control closes
  that gap, and a request to confirm an address is proportionate (EDPB Guidelines 01/2022, paras. 73-74).

**Given up.**
- Linking the second address requires a round trip with the requester during discovery. That needs a
  state-machine edge, to be added in M4.
- Anonymous page views from a shared browser are left out, even when they were the subject's.
- Servers share a read-only scope store instead of being fully stateless.

**Tested by.**
- `tests/test_scope.py`:
  - every case's reachable records equal the generator's ground truth exactly, and none belongs to anyone else;
  - person A's token is refused on every record of person B;
  - a relative's address can't be linked without their mailbox;
  - deliberately broken servers turn these tests red.
- `tests/test_mcp_servers.py`: the contract tests.
