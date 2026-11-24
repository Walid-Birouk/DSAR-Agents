# 0015: Launch preparation (M7, the parts that need no account)

**Status: accepted** on 2026-11-23, after starting as provisional choices ([0018](0018-provisional-choices-settled.md)
says what changed).

**Decision.**
- **The input detector** (`web/guards.py`, PLAN.md 11.3) refuses any visitor text that holds:
  - an email address that is not on a reserved `.example` domain;
  - a phone number outside ARCEP's fiction ranges;
  - an IP address outside the documentation blocks (the generator's own rules, `company/fiction.py`);
  - anything that looks like an IBAN, a payment card number (Luhn check) or a French social security number.
- **The human check** *(open choice)* is a self-hosted proof of work: SHA-256 with 16 leading zero bits, about a
  second in a browser. Challenges are single-use and expire after 5 minutes. It needs no third-party CAPTCHA,
  no cookie and no tracking, and it is required before every live run.
- **Per-IP limits** apply to live runs: 3 an hour and 10 a day, on top of the per-session limits.
  X-Forwarded-For is trusted only with `A15_TRUST_PROXY=1`, which the Compose file sets behind Caddy.
- **Bring your own key** (PLAN.md 11.3): a Mistral or DeepSeek key, held in memory for the session for an
  hour at most and used only for that visitor's runs. *(Open choice:)* the daily cap does not apply to a
  visitor's own key. The page says which provider receives the text.
- **Deployment** (`Dockerfile`, `deploy/`):
  - **One image** for the web app and the advisor. The frontend is built in a Node stage, and the fictional
    company is generated at build time from its seed.
  - **Containers:** a non-root user (uid 10015), a read-only filesystem and `no-new-privileges`.
  - **Caddy 2.11.9** gives automatic HTTPS, security headers (HSTS, CSP, no framing, no referrer) and a 64 KB
    body limit.
  - **Nightly backups** of the web store and its audit log, kept 14 days.
  - **Image tags** were checked on Docker Hub on 2026-11-22: python 3.12.16-slim-bookworm, node 24.22.3,
    uv 0.11.33. *Not built yet:* the Docker daemon was not running.
- **A load test** (`scripts/loadtest.py`): 20 visitors at once, scripted mode, against the real server.
  - Measured on 2026-11-22 on the development machine.
  - Every visitor completed its case, with no failure.
  - API latency was 279 ms at p50, 920 ms at p95 and 1.2 s at most.
  - From start to finished case: 7.4 s on average, 11.8 s at most.
- **The professional finish of PLAN.md 13:**
  - the docs: `docs/architecture.md`, `threat-model.md`, `running.md` and `eval-report.md`;
  - the repository files: `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md` and issue templates.

**Why.**
- Every protection the plan lists for the public demo can be built and tested before there is a server, so the
  launch itself becomes configuration.

**Not done yet.**
- The live deployment and its HTTPS, and monitoring alerts sent to the operator.
- The public red-team round.
- The full eval on the test split, the 3-run spread, E3 and E4 (they need a model key).
- The video.

**Tested by.**
- `tests/test_web_guards.py`:
  - fictional data passes, and real-looking data of each kind is refused;
  - the per-IP window;
  - the human check, which is single-use and expires;
  - visitor keys, which expire and never appear in a response;
  - live runs need the human check, and real personal data in a request is refused.
- The Playwright suite still passes, with the new controls in place.
