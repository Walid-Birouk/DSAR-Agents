"""Subject-scoped access: who a case may read, and the proofs needed to widen that.

The control plane (ScopeAuthority) issues grants; the MCP servers resolve a case token to its grant
(ScopeResolver) and return only records inside it. See docs/decisions/0008-subject-scoped-access.md.
"""
