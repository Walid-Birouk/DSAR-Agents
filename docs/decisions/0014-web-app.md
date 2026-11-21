# 0014: The web app

**Status: provisional.** Made on 2026-11-21/22 while the project owner was away, to be confirmed or changed
(see [pending-review.md](pending-review.md), P30 to P39). The choices the plan leaves open are marked
*(open choice)*.

**Decision.** M6 builds the web app of PLAN.md 11 on the stack of decision 0007: a FastAPI backend
(`src/article15/web/`, `a15 web`) and a React app (`frontend/`).

- **Backend.**
  - **Anonymous sessions:** an HttpOnly, SameSite=Lax cookie, with no accounts. A run belongs to the session
    that started it, and any other session gets a 404.
  - **Sandboxes** *(open choice)*: every session has its own directory for the scope and notifier stores,
    checkpoints, events, spans and packs. The company databases are shared, because everything opens them
    read-only, so nobody can change them. Sandboxes are deleted after 24 hours by an hourly sweep.
  - **Jobs** *(open choice)*: an in-process queue with 2 workers, not arq/RQ with Redis. A single server needs
    nothing more, and Redis can come with the deployment if load needs it.
  - **Live progress:** server-sent events tail the run's event log, which holds no contents. The details (tool
    calls, the letter, the redactions) are the visitor's own fictional case, read after the run.
  - **The reviewer** is the visitor, for their own case. They can approve and send (a signed approval of the
    exact pack, then the release service; one action, *open choice*), request changes, reject, extend or
    escalate. The case is reopened from its checkpoint for each action.
  - **The audit log** is append-only: SQLite triggers refuse UPDATE and DELETE. It records every transition,
    approval and release, with the actor, the time, the policy version and the pack hash.
  - **Two modes** *(open choice)*:
    - `live`: the demo provider, Mistral's EU endpoint by default (D3, `A15_WEB_PROVIDER`), with no fallback.
      It is the only mode that takes a visitor's own text.
    - `scripted`: the oracle plays the prepared cases, with no model and no cost. It is always available,
      and it is the tier T4 fallback until recorded live runs exist. The page says which mode is on.
  - **Limits:**
    - Live runs per session: 3 an hour and 10 a day.
    - The daily spend cap of D4, counted 1:1 in USD against EUR 2, which keeps it under the cap.
    - Visitor text: 4000 characters at most.

    Per-IP limits, the human check and the personal-data detector on input come with M7.
  - **Break it:** the adversarial prepared cases (injections, impostors, representatives). An attempt is
    *blocked* when no record of anyone but the subject reached a pack and no impostor was verified. The
    public counter shows attempts and blocks.
- **Frontend:** React 19.3.1, Vite 8.3.8, Tailwind 4.3.5, TypeScript 7.0.2 and react-router 7.18.6, with
  versions checked on npm on 2026-11-21. It has seven pages:
  - Try it: a prepared request, or your own when live;
  - the run view: live steps, every tool call, the advisor's advice, the reply, the copy, what was hidden and
    why, the special-category notice, the reviewer panel and the audit log;
  - the trace: the span tree with timings, tokens and cost, and the rules that fired;
  - Break it;
  - Eval results;
  - My runs;
  - System.

  The AI-system notice, the fictional-data notice and the privacy note are on every page, and there are no
  tracking cookies.
- **Accessibility:**
  - semantic landmarks and a skip link;
  - labelled controls, a visible focus ring, and `aria-live` for progress;
  - scrollable regions reachable by keyboard;
  - reduced motion respected.

  axe-core finds no serious or critical WCAG 2 A/AA violation on any page.

**Why.**
- Reopening the case from its checkpoint for each reviewer action reuses the resume machinery of M5. The
  approval is checked by the same guards as everywhere else.
- Scripted mode keeps the demo useful with no key and no budget, and it is honest about what it is.

**Given up.**
- No curated gallery of recorded live runs yet ("My runs" shows the session's runs). It needs live cassettes.
- No PDF download yet: the pack is JSON, plus the letter as Markdown (P12).
- A visitor cannot yet answer the desk's follow-up question from the page, in live mode.
- Bring-your-own-key, per-IP limits and the human check come with M7.

**Tested by.**
- `tests/test_web.py`:
  - a prepared run, then approve and send, with the audit trail;
  - special-category acknowledgement;
  - the other actions;
  - session isolation;
  - SSE;
  - visitor text needing a live model;
  - rate limits;
  - break-it scoring;
  - the append-only audit log, and sandbox expiry.
- `frontend/e2e/flows.spec.ts` (Playwright, Chromium, against the real backend in scripted mode):
  - the whole flow, from request to approval to the sent reply and the trace;
  - a break-it attack blocked and counted;
  - every page;
  - axe accessibility scans;
  - keyboard navigation.
- CI runs both, in its `web` job.
