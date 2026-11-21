# 0009: Ferlane, a seeded fictional company

**Decision.** The test company is **Ferlane**, an invented French online shop for home textiles and decor
(domain `ferlane.example`). A seeded generator (`a15 generate`, default seed 15) builds:
- its four systems, as one SQLite database each;
- 160 request cases;
- their correct answers.

**Details.**
- **Contact data that can't be real.** Generated contact details come only from reserved ranges
  (`src/article15/company/fiction.py`):
  - Emails use the reserved `.example` top-level domain (RFC 2606, RFC 6761).
  - IP addresses come from the IPv4 documentation blocks (RFC 5737).
  - Phone numbers use the six blocks ARCEP reserves for audiovisual works: roots 01 99 00, 02 61 91, 03 53 01,
    04 65 71, 05 36 49 and 06 39 98. The source is ARCEP's national numbering plan, version of 1 January 2026,
    p. 55/67, section "Numéros pour œuvres audiovisuelles".
  - A test checks every generated value.
- **Templates, not an LLM, write the free text.** Seeded French and English templates record the exact character
  span of every third-party name, other customer's order number, staff signature and health remark. Redaction can
  therefore be graded against exact spans. Typos are applied only between those spans.
- **Truth kept apart.** System databases hold only what the company would hold. The answers live in `truth/`,
  which no MCP server opens. They are:
  - who each record is really about;
  - the expected identity decision and resolution;
  - the records, redactions and special-category mentions.
- **Reproducible.** The same seed gives the same world, checked by a SHA-256 digest in `manifest.json` and in
  the tests.
- **MCP SDK.** `mcp==2.3.0` (current release, checked 2026-11-21). Version 2 renamed `FastMCP` to `MCPServer`.

**Why.** Exact ground truth is what lets most eval metrics be exact checks instead of opinions (PLAN.md 8.3).
Reserved ranges mean a leak test can never be confused by, or expose, a real person's details.

**Given up.** Templated text is less varied than real customer writing. The adversarial cases and the golden-20
review partly make up for it, and the eval report states the limit.
