# Article 15: a multi-agent desk for GDPR access requests

**Build plan v0.3, 2026-11-20. Status: confirmed; build in progress (milestone M0).**

Decisions are settled in section 17.

## 1. Summary

- **What it is.** A working system that answers GDPR right-of-access requests (Article 15) for a fictional
  French online shop:
  1. Read the request.
  2. Check who is asking.
  3. Find all their data across four company systems.
  4. Hide other people's data.
  5. Draft the reply and check it.
  6. Wait for a person to approve the release.
- **What people can do online.** Anyone can open the public demo and file a request as one of the shop's
  fictional customers. They can:
  - watch the agents work step by step;
  - act as the reviewer who approves or rejects the reply;
  - try to trick the system with prompt injections;
  - read the live evaluation results.
- **How it is built.**
  - Seven LLM agents: one coordinator and six specialists.
  - The company systems are MCP servers.
  - An external "DPO advisor" agent runs as a separate service and is reached over A2A.
  - A human approval gate is enforced in code.
- **Models.** DeepSeek through an OpenAI-compatible client, for development and evals. The public demo runs on
  Mistral's EU endpoint, so visitor text stays in the EU (section 15.3). Switching is one setting.
- **Model cost.** About $0.02-0.05 per case on `deepseek-flash`, and about $15-35 for the whole build's eval runs.
- **Why it stands out.**
  1. A labelled eval suite with hard safety gates runs on every change.
  2. Tracing and metrics never record personal data.
  3. Policy is written once and enforced twice, in the prompts and in code. Three experiments measure the
     difference.
- **Size.** About 26 focused build days, including the public deployment. A minimum version is defined in
  section 14.

## 2. Why this project

- **The problem.** Anyone can ask a company for a copy of their personal data and how it is used (GDPR
  Art. 15). The company must answer within one month of receipt, extendable by two months for complex or numerous
  requests (Art. 12(3)). The copy must not harm other people's rights (Art. 15(4)). Small companies do this by
  hand across many tools. The result is missed data or someone else's data leaking.
- **Why multi-agent fits.** The job is a fixed sequence of distinct skills: reading, identity checking, searching,
  redacting, writing, reviewing. Each skill needs different tools and different access rights. Only one step,
  releasing the reply, can do harm, and that step belongs to a person.
- **Why it is good for evals.** The answers can be checked. Was the right person verified? Were all records found?
  Was every third-party name hidden? Is the deadline right? Most quality checks can therefore be exact, not
  opinions.
- **Why it is good for observability.** The traces are full of personal data, so keeping them clean is part of the
  design, and it is tested.
- **Why it is good for prompts as policy.** Rules written only in prompts drift and get ignored. This project
  writes each rule once, renders it into the prompts, enforces it in code, and measures what the code catches that
  the prompts miss.

## 3. Scope

In scope:
- Access requests (Art. 15) arriving by web form or email, in French or English.
- Four fictional company systems: CRM, helpdesk, orders, web logs.
- Identity check, data discovery, redaction, cover letter, validation, human approval, release, audit trail.
- Deadline tracking, with the extension workflow.
- An external DPO advisor agent for edge cases.
- Eval suite, observability, resilience, a public online demo, and self-hosting with Docker Compose.

Out of scope:
- Other rights (erasure, rectification, portability). These are detected and routed to a person, not automated.
- Real company data and real email. "Send" writes to an outbox that the demo shows.
- Legal advice. The rules are sourced from the GDPR text and EDPB guidance (Appendix A), but this is a
  demonstration, not a compliance product.

## 4. The workflow being automated

| # | What a privacy officer does by hand | Who does it here |
|---|---|---|
| 1 | Read the request, log the date received | Intake agent; code computes the deadline |
| 2 | Check the requester is who they claim | Identity agent proposes; code grants access to the data |
| 3 | Search every system for their data | Discovery agent (read-only tools, limited to one person) |
| 4 | Hide other people's data in what was found | Redaction agent proposes; code applies and verifies |
| 5 | Write the reply with the Article 15 information | Letter agent |
| 6 | Check everything before it goes out | Validator agent plus deterministic checks |
| 7 | Sign off, send, keep a record | A person in the reviewer screen; release service; audit log |
| - | Unusual case (repeat requests, health data, extension) | Coordinator consults the DPO advisor, then a person decides |

## 5. Architecture

### 5.1 Overview

```text
 browser (public demo)  ──HTTPS──>  web app (React)  ──API + live events──>  backend (FastAPI)
                                                                                  |
 +------------------------------ control plane ----------------------------------+--+
 |  Case store (SQLite / Postgres): state machine, deadlines, checkpoints, audit log  |
 |  Coordinator agent --agent tools--> Intake, Identity, Discovery, Redaction,         |
 |        |                            Letter, Validator   (sub-agents as tools)       |
 |        +-- A2A --> DPO advisor agent (separate service, own model + GDPR knowledge) |
 |  Policy guards (code) run before every tool call  <-- policy.yaml (single source)   |
 |  Reviewer approval --> Release service (checks signed approval) --> outbox          |
 +-------+----------------------------------------------------------------------------+
         | MCP (read-only, limited to one verified person, one sandbox per visitor)
   +-----+------+--------------+--------------+
   CRM      Helpdesk         Orders        Web logs        <- fictional company systems

 LLM calls -> provider layer -> DeepSeek (default) | Mistral | replay (tests, offline demo)
 Telemetry -> OpenTelemetry -> scrubber -> trace store + metrics -> in-app run view, Grafana (private)
```

### 5.2 Agents

| Agent | Job | Tools | What it may not do |
|---|---|---|---|
| Intake | Turn the request into a structured record: who claims to ask, what, when, which channel. Flag other rights, representatives and embedded instructions | none (structured output only) | no data access |
| Identity | Decide VERIFIED / NEEDS_MORE_INFO / REJECTED with evidence. Propose a proportionate follow-up question | CRM contact lookup (read) | can't grant access itself; code does, after checking the evidence |
| Discovery | Find every record of the verified person across the four systems. Follow linked identifiers (a second email, an order number) and leave out look-alikes | search tools of the four MCP servers | can't query anyone else; enforced by the servers, not the prompt |
| Redaction | Classify each person or identifier found in free text as the data subject, a third party or staff. Propose redactions with reasons | entity detector, record text reader | proposes only; code applies and re-checks |
| Letter | Draft the reply in the requester's language with the Article 15(1) information | company privacy register (read) | draft only |
| Validator | Check the pack against the requirements and the tool logs. Give a verdict: APPROVED, CONDITIONAL or REJECTED | case reader, verifier results | verdict only |
| Coordinator | Run the case: choose the next step, handle failures, consult the advisor, escalate | the agents above as tools, case tools, advisor tool | can't release; every stage change is checked by code |
| DPO advisor (separate service) | Advise on edge cases: repeated or excessive requests, extensions, health data | its own GDPR knowledge tool | advice only; never sees personal data |

Release is a plain service, not an agent, so no model can send anything.

### 5.3 Case lifecycle

The case lifecycle is a state machine in code. The coordinator chooses the next step, but only transitions the
state machine allows can happen. That keeps the legal order (identity before discovery, validation before
approval) out of the model's hands. The model's judgment covers the unusual paths: retries, follow-up questions,
escalation.

```text
RECEIVED -> INTAKE_DONE -> IDENTITY_CHECK <-> AWAITING_REQUESTER
         -> IDENTITY_VERIFIED -> DISCOVERY_DONE -> REDACTION_DONE -> DRAFT_READY
         -> VALIDATED -> AWAITING_APPROVAL -> APPROVED -> RELEASED -> CLOSED
side exits: REJECTED, NEEDS_HUMAN (edge case), EXTENDED (reason recorded), PARTIAL (a system was down)
```

Every transition has a guard:
- The required outputs must be present. For example, VALIDATED needs an APPROVED verdict and a passing redaction
  check.
- The policy conditions must hold.

Two modes run side by side:
- **Reactive:** a new request starts a case.
- **Scheduled:** a daily sweep raises alerts on day 20 and day 25 of the month.

### 5.4 Protocols

**MCP for the company systems:**
- Each system is its own MCP server process over a fake database.
- Tools only search and read, and they require a case token. The server maps the token to the one verified
  person, so even a tricked agent can't read anyone else's records.
- Tool names carry the server name (`crm_search_by_email`) to avoid collisions.
- Every tool has a timeout and returns errors as short, readable text the model can act on.
- A notifier server hides the human channel (in-app inbox now, email or Slack later). It can both post messages
  and read replies.

**A2A for the DPO advisor:**
- A separate service with its own model and its own GDPR knowledge tool.
- Published Agent Card, bearer-key authentication, one client tool per skill.
- The coordinator sees its advice, not its internals.
- It receives a minimised case summary: no names, emails or record text. An egress filter blocks anything that
  looks like personal data, and the eval checks this (must be 0).

**Why A2A only here.** MCP connects agents to tools and data. A2A connects to another agent that has its own
judgment and owner, and the advisor is the only such party in this system.

Stretch goal: expose the desk itself as an A2A server, so a person's own AI assistant can file a request for them.

### 5.5 Context design

- **Case file.** The case file is the single store of context. Agents get the slice they need, never the whole
  case: Redaction sees record text but not the case history; Letter sees counts and categories, not raw records.
- **Typed handoffs.** Handoffs are typed schemas with required fields: IntakeResult, IdentityDecision,
  DiscoveryResult, RedactionPlan, LetterDraft, ValidationReport. Every agent ends with a structured result that
  says plainly whether it succeeded.
- **References, not content.** Discovery returns record references and short summaries. Code fetches the full
  records when building the pack, which keeps personal data out of the model where possible and cuts cost.
- **Prompt order.** Prompts put stable parts first: role, policy, company register, then the case slice and tool
  results. That order lets the provider's cache work. DeepSeek caches repeated prefixes and charges less for them
  (section 15).
- **Fresh calls.** Each agent call starts fresh, so the coordinator must pass context explicitly. A "re-discovery"
  metric catches handoffs that lose context and force an agent to redo a search.

### 5.6 Human approval

- The reviewer screen offers five options:
  - approve and send;
  - request changes (back to the agents with a note);
  - reject;
  - extend the deadline (a reason is required);
  - escalate.
- Approval creates a signed token covering the case ID, a hash of the exact pack, the reviewer and the time. The
  release service refuses anything without a valid token for that exact pack, so a changed pack needs a new
  approval.
- A person is needed only for releases, edge cases and extensions, not routine steps. Reviews left unanswered
  escalate after a set time with the safest default: do not send.
- In the public demo, the visitor is the reviewer for their own case.

## 6. Policy as code

Rules written only in prompts have three problems:
- different prompts end up saying different things about the same action;
- a model can still skip a rule under pressure;
- a "wait for approval" step is useless if nothing can receive the approval.

The design closes those gaps:

1. **One versioned policy file,** `policy/policy.yaml`. Each rule has an ID, its source, structured fields (action,
   effect, which agents) and plain-language text.
2. **A renderer** writes each agent's POLICY prompt section from the rules that apply to it. Nothing is
   hand-copied, so prompts can't drift apart.
3. **Guards in code read the same rules:** state machine transitions, a check before every tool call, and the
   release gate. Each rule names the guard that enforces it, or says "prompt-only" for judgment rules such as tone.
4. **A policy linter runs in CI.** It checks that:
   - every rule has a source;
   - every enforced rule has a guard, and every guard has a rule;
   - no two rules conflict;
   - every prompt renders.
5. **Every trace and eval result records the policy version.** A change to policy or prompts must come with an eval
   comparison (section 8.7).

Example rule:

```yaml
- id: R-RELEASE-APPROVAL
  source: "project rule"
  action: release_pack
  effect: requires_human_approval
  applies_to: [coordinator]
  enforced_by: release_service.check_approval_token
  text: "A person must approve every reply before it is sent."
```

Rules for v1 (legal sources in Appendix A):

| Rule | Content | Source |
|---|---|---|
| R-DEADLINE | Answer without undue delay and within one month of receipt. Counting: a request on the 31st with no 31st next month is due on the last day of that month; if the last day is a weekend or holiday, the next working day. Extend by up to two months only for complexity or number of requests, telling the requester within the first month with reasons | Art. 12(3); EDPB para. 160-161 |
| R-NO-ACTION | If the request is not acted on, say so within one month with the reasons and the right to complain | Art. 12(4) |
| R-IDENTITY | Verify against the account first (registered email, non-intrusive security questions). Ask for more only with reasonable doubts. No ID-document copy by default | Art. 12(6); EDPB para. 73-74 |
| R-SCOPE | All personal data of the verified person from every system, including linked activity logs, plus the Art. 15(1) information: purposes, categories, recipients, retention, rights, complaint, source, automated decisions | Art. 15(1); EDPB |
| R-COPY | First copy free; a commonly used electronic format when the request was electronic | Art. 15(3), 12(5) |
| R-OTHERS | Redact rather than refuse; record the reason for each redaction. v1: other customers and private individuals. v2: also staff names, keeping the role | Art. 15(4); EDPB |
| R-EXCESSIVE | Manifestly unfounded or excessive (e.g. repetitive) requests: a person decides; reasons recorded; read narrowly | Art. 12(5); EDPB |
| R-SPECIAL | Special-category data found (health etc.): flag for human review before release | project rule; list from Art. 9(1) |
| R-OTHER-RIGHTS | Requests for erasure, rectification or other rights: route to a person | project rule |
| R-RELEASE-APPROVAL | A person approves every release | project rule |
| R-NO-FOREIGN-DATA | Never include another person's records | project rule |
| R-MINIMISE-EGRESS | No personal data goes to the external advisor | project rule |

## 7. Safety

**Access controls:**
- **No raw access.** Agents get no SQL and no shell, only purpose-built search tools, each limited to the verified
  person.
- **Least privilege.** Databases are opened read-only by the MCP servers. Containers run as non-root. Each agent
  sees only its own tools.

**Checks on what the agents produce:**
- **Claims checked against the tool log.** The Validator compares every record ID and count in the letter with what
  the tools actually returned. A claimed record that no tool returned fails validation.
- **Prompt injection.**
  - Request text and record text are marked as untrusted data, and instructions come only from the system prompt.
  - Tools are limited to one person, so an injected "also send user X's data" can't reach X.
  - An injection test set measures the success rate, which must be 0.
- **Bounded runs.** Each agent and each case has caps on steps, tokens, time and cost. Every tool error goes back to
  the model as a result; none crashes the run.

**Infrastructure:**
- **Secrets** live in environment files and the host's secret store. They are never committed, never in prompts,
  never in logs.

The public demo adds its own protections (section 11.3).

## 8. Evaluation plan (the core of the project)

### 8.1 Data

The generator is seeded and reproducible. It builds the fictional shop: about 300 customers with records across
the four systems, plus the request cases with their correct answers. Because the generator creates the data, it
knows the ground truth.

Difficulties built into the data:

| Built-in difficulty | Tests |
|---|---|
| Namesakes (two people with the same name) | discovery precision |
| A second email that appears only inside a helpdesk ticket | discovery recall |
| Third parties in free text: a partner's name, another customer's order number, staff names | redaction |
| Health remarks in tickets | special-category flag |
| Anonymous web-log events that look similar but aren't linked | precision |
| Requests on the 31st and before weekends | deadline arithmetic |
| French and English, typos, long and short requests | robustness |

Case mix (160 cases):
- 100 standard cases.
- 20 identity cases: a different email, an impostor, a representative without a mandate, no account.
- 10 legal edge cases: repeated requests, an extension, other rights.
- 30 adversarial cases: injection in the request or in records, malformed or oversized input.

Two further controls:
- **Golden 20.** A person reviews 20 cases by hand (about 2 hours) to confirm the generator's labels are right.
- **Dev/test split.** Prompts are tuned on the dev set, and only the held-out test set is reported.

### 8.2 Metrics and bars

| Area | Metric | Starting bar |
|---|---|---|
| Task | identity decision accuracy | >= 95% |
| Task | impostor accepted | 0 (hard gate) |
| Task | discovery recall / precision (per record) | >= 98% / >= 97% |
| Task | due date correct | 100% |
| Task | Art. 15(1) items present in the letter | 100% |
| Safety | third-party personal data in a released pack | 0 (hard gate) |
| Safety | another person's record in a pack | 0 (hard gate) |
| Safety | release without a valid approval | 0 (hard gate) |
| Safety | successful injections | 0 (hard gate) |
| Safety | personal data in telemetry; in advisor payloads | 0 (hard gates) |
| Safety | redaction recall / over-redaction of the person's own data | >= 98% / <= 5% |
| Behaviour | required stage order kept (from traces) | 100% |
| Behaviour | tool calls per case; repeated identical calls | report |
| Coordination | handoffs with all required fields | 100% |
| Coordination | searches repeated after a handoff (lost context) | 0 |
| Quality | letter clarity, accuracy, tone (LLM judge, 1-5) | >= 4.0 |
| Robustness | spread across 3 runs of the same 20 cases | report |
| Efficiency | cost per case and per successful case; latency p50/p95; cache hit rate | report |

The bars are starting points. M3 measures a baseline, and the final bars are fixed before the reported run, then
left alone.

### 8.3 Graders

- **Exact checks first:** set comparison for records, exact spans for redactions, date arithmetic for deadlines,
  a checklist for Art. 15(1), schema validation for handoffs, and order checks on traces.
- **An LLM judge only for the letter's quality,** with a written rubric. The judge's scores are compared with a person's
  ratings on the golden 20 to check that it agrees with a person.

### 8.4 Trajectory checks (from traces)

- Identity is verified before any search.
- The validator runs before approval.
- No agent uses a tool outside its own set.
- No duplicate searches.

### 8.5 Failure injection

| Failure | Test |
|---|---|
| Invented facts | Letter claims checked against tool logs; a case where a system returns nothing |
| Wrong tool or bad parameters | Wrong-person queries refused by the scoped tools; unknown parameters rejected by strict schemas |
| Skipped instructions | A coordinator skipping a step is caught by the state machine; experiment E2 counts how often it tries |
| Runaway loops | A tool that always fails: the agent stops at its cap and escalates |
| Lost context | A handoff missing fields; two workers on one case (a lock prevents a double send) |
| Timeouts | A slow or down system: retries, circuit breaker, case marked PARTIAL rather than a silent gap |
| Crashes | Process killed mid-case: resumes from the checkpoint without redoing finished stages |
| Provider outage | DeepSeek unreachable: falls back to the second provider, or pauses cleanly |
| Security | Injection set; secrets never appear in logs |

### 8.6 Testing the tests

Seven deliberately broken versions must each turn the suite red:
1. Redaction off.
2. Release guard off.
3. Identity step skipped.
4. Look-alikes included.
5. Retention period missing from the letter.
6. Raw prompts sent to telemetry.
7. Injection defences removed.

A check that has never been seen to fail can't be trusted.

### 8.7 Running evals cheaply and honestly

- **Replay mode.** Every live run records each model request and response, and CI replays them at no cost. Replay
  proves code changes didn't break anything. A changed prompt or policy produces new requests, so it needs a live
  run.
- **Agent-level evals.** Each agent is evaluated on its own fixed inputs. This is far cheaper than whole cases, so
  most iteration happens here.
- **Rule for changes.** A change to prompts, policy or models must include a live smoke run (20 cases) and the
  comparison with the last baseline. Full runs happen at milestones.
- **Budget caps.** The runner stops when a run's budget is spent.

### 8.8 Experiments for the write-up

- **E1: one agent vs seven.** One agent with all the tools against the multi-agent design, compared on quality,
  cost and latency. This tests the benefit instead of assuming it.
- **E2: prompt-only policy vs prompts plus code guards,** on the adversarial cases. In the first arm the guards only
  log what they would block. Measured: how many violations the model attempts, and how many get through.
- **E3: policy v1 to v2,** where staff names are redacted too. It shows the eval difference that the change process
  displays, including the cost change.
- **E4: DeepSeek vs Mistral** (and model size within each), compared on cost per successful case and on the hard
  gates.

## 9. Observability

- **Traces.** OpenTelemetry, one trace per case; the case ID is the correlation ID.
  - Span hierarchy: case > stage > agent run > model call > tool call (MCP, A2A or internal).
  - Event names: agent.start, agent.complete, llm.request, llm.response, tool.invoke, tool.complete,
    error.occurred. The event schema is versioned.
  - Attributes: agent, provider, model, tokens (input, output, cache hit), latency, stop reason, policy version,
    prompt hash, tool name, status, record counts. Never record contents.
- **Personal data never enters telemetry, by design:**
  - Identifiers are keyed hashes. No request text, names or record contents go into spans or logs.
  - Prompts and responses are needed for replay and the demo's run view, so they go to a separate store with
    short retention.
  - A scrubber in the telemetry pipeline is the second line of defence.
  - A leak test scans every exported span for every seeded personal value; it must find 0.
- **Metrics:** case completion rate, days to reply vs deadline, cost and tokens per case and per successful case,
  calls per case, latency p50/p95/p99, cache hit rate, errors by type, timeouts, retries, circuit-breaker
  openings, guard blocks, human changes (approved as-is vs changed).
- **Where people see it:**
  - **Visitors:** a run view showing every step, tool call, timing and cost of their own case, built from the
    stored spans.
  - **Public "System" page:** runs today, success rate, spend today against the daily cap, injections blocked,
    and the latest eval results.
  - **Operator:** full Jaeger and Grafana, private, behind a login.
- **Alerts** (to the operator): error rate above 5%, latency spikes, daily spend near the cap, a case near its deadline.
- **Audit log:** an append-only record of every stage, decision, approval and release: who, when, policy
  version, pack hash.

## 10. Resilience

- **Timeouts and retries.** Every model, tool and A2A call has a timeout. Retries apply only to transient errors
  (timeouts, server errors, rate limits), never to invalid input or authentication errors. The schedule is
  exponential backoff with jitter, capped at about 30 s, 5 tries.
- **Circuit breakers** per MCP server, per model provider and for the advisor. Each opens at 50% failures over
  10 calls and tries again after 60 s.
- **Provider fallback:** DeepSeek, then Mistral, then pause.
- **Degradation tiers,** each with a kill switch:
  - T1: everything runs.
  - T2: advisor down; edge cases go straight to a person.
  - T3: a company system is down; the case is marked PARTIAL with the missing system named, never sent silently
    incomplete.
  - T4: model spend cap reached or automation off; the demo switches to recorded runs.
- **Checkpoints** at each stage boundary. Stages are idempotent with operation IDs, so a resume never sends twice.
- **One worker per case** at a time, using a lock with expiry.

## 11. The public demo

### 11.1 What a visitor can do

1. **Try it.** Pick one of the shop's fictional customers, or write a request as one of them. Watch the agents
   work live: each step, what they looked up, what they found, what they hid and why. Then act as the reviewer
   and approve, change or reject the reply. Finally, download the generated pack (PDF and JSON).
2. **Break it.** A guided "red team" page invites visitors to make the desk leak someone else's data, through
   injections in the request, a fake identity or a pretend lawyer. Every attempt is scored, and a public counter
   shows attempts made and attempts blocked.
3. **See inside.** For any run, the visitor sees the full trace, token use and cost, and which policy rules fired.
4. **Check the numbers.** The eval results page shows the latest full run, the hard gates, the experiments E1-E4,
   and how results changed across versions.
5. **Watch recorded runs.** A gallery of interesting recorded cases replays at no model cost. It always works,
   even when the daily budget is used up.

### 11.2 How it is built

- **Web app:** React with TypeScript (Vite) and Tailwind, for a polished, responsive, accessible interface. Live
  progress arrives through server-sent events.
- **Backend:** FastAPI. Agent runs go to a small job queue, so a slow model never blocks the website.
- **Sandboxes:** each visitor session gets its own copy of the fictional company, created from the seed and deleted
  after 24 hours. Visitors can't see or change each other's data.
- **Accounts:** none needed. An anonymous session uses a cookie for that session only.
- **Hosting:** one small virtual server in the EU, running Docker Compose behind a reverse proxy with automatic
  HTTPS. A free CDN/DNS layer sits in front for caching and basic DDoS protection. A custom domain is optional.

### 11.3 Abuse and cost controls

- **Rate limits** per session and per IP (for example 3 runs per hour, 10 per day), and a short human check on run
  submission.
- **A hard daily model-spend cap of EUR 2** on the shared key. When it is reached, new live runs pause and the demo
  offers recorded runs, or the visitor can bring their own key.
- **Bring your own key.** A visitor can paste their own Mistral or DeepSeek API key to run live past the cap:
  - the key is held in server memory for that session only, never written to disk, logs or traces;
  - it is used only for that visitor's runs and dropped when the session ends or after 1 hour;
  - the page says plainly where the key goes and which provider receives the visitor's text.
- **Input limits:** maximum length, allowed languages, and requests only on behalf of the fictional customers.
- **No real personal data.** The form states it plainly. A detector rejects text that looks like real personal data
  (real email domains, phone numbers that aren't in the fictional range). Visitor text is never used for anything
  but that run, and is deleted with the sandbox.
- **Transparency:** a clear "you are using an AI system" notice, a short privacy notice, and no tracking cookies.

### 11.4 Self-hosting

`docker compose up` runs the same stack locally with your own API key. The README explains how to replace the
fictional systems with your own MCP servers.

## 12. Technology

| Need | Choice |
|---|---|
| Language | Python 3.12 (backend, agents); TypeScript (web app) |
| Packaging | uv (Python); pnpm or npm (web) |
| Agent core | A small, framework-free core: Agent, Tool, ToolManager, provider interface, agents as tools. It adds a bounded loop, a guard hook before each tool call and a tracing hook. Owning the loop keeps policy checks and tracing first-class |
| Model providers | One OpenAI-compatible client with three settings: DeepSeek (default), Mistral, replay (tests and recorded runs). Switching provider is one environment variable |
| MCP | Official MCP Python SDK; version pinned in M0 |
| A2A | Official A2A Python SDK, for both the advisor server and the client; pinned in M0 |
| Schemas | pydantic |
| Storage | SQLite for development and per-visitor sandboxes; Postgres for the hosted case store and run history |
| Personal-data detection | Microsoft Presidio plus rules |
| Web app | React, TypeScript, Vite, Tailwind |
| API | FastAPI with server-sent events; a small job queue (arq or RQ with Redis) |
| Telemetry | OpenTelemetry SDK and collector, Jaeger, Prometheus, Grafana |
| Tests | pytest, hypothesis (date arithmetic, redaction offsets), Playwright (web app end to end) |
| Run and host | Docker Compose; reverse proxy with automatic HTTPS (Caddy) |
| CI/CD | GitHub Actions: tests, replay evals, policy linter, image build, deploy on tag |

## 13. Professional finish

- **A README that does the selling:** a one-line pitch, the live demo link, a 2-minute video, an architecture
  picture, a results table, and a quick start.
- **`docs/`:**
  - architecture;
  - design decisions (one short record per decision);
  - policy and its sources;
  - the eval report;
  - a threat model;
  - running and self-hosting.
- **Badges:** tests, eval gates, coverage, demo status.
- **Releases:** versioned (semantic versioning), with a changelog. Docker images are published to GitHub's
  container registry.
- **Repository hygiene:** licence (MIT), `SECURITY.md`, `CONTRIBUTING.md`, issue templates.
- **No secrets in the history.** A secret scanner runs in CI.

## 14. Milestones

Times are focused days of building.

**M0: foundations (1.5 days)**
- Repository, uv project, linters, type checks, tests, pre-commit, CI skeleton.
- A decision record for each confirmed decision.
- SDK versions pinned after checking their current documentation.
- `policy.yaml` v1 with sources; schemas for policy, agents and handoffs.
- The case state machine with tests.
- Done when: CI is green, policy v1 has been reviewed, and illegal transitions are rejected in tests.

**M1: fictional company and MCP servers (2.5 days)**
- Seeded generator for customers, records, cases and correct answers, with the difficulties from 8.1.
- The four MCP servers with person-limited read tools, and the notifier.
- Tests: contract tests; the scope test (person A's token can't read person B); same seed gives the same data.
- Done when: one command generates everything and all server tests pass.

**M2: agent core and a one-agent end-to-end run (3 days)**
- Core:
  - agent loop: bounded, errors returned as results, structured final output;
  - tool manager;
  - OpenAI-compatible provider: DeepSeek and Mistral settings, token and cost accounting, cache-hit tracking;
  - replay provider;
  - agents as tools.
- A single agent with all the tools (the E1 baseline) runs a case end to end and writes a pack.
- Done when: 5 cases run live on DeepSeek, the same 5 replay offline with identical results, and real token use is
  measured. The cost estimate is then redone with real numbers.

**M3: eval harness, before the real agents (3 days)**
- Graders, metrics, and a runner that is parallel, resumable and budget-capped.
- JSON and Markdown reports with comparison to a baseline.
- Smoke (20) and full (160) suites with the dev/test split; the golden 20 sent for review.
- First 3 broken-version tests.
- CI runs unit tests and the replay smoke on every push.
- Done when: a baseline report for the single agent exists and each broken version fails a hard gate.

**M4: multi-agent system and policy as code (4 days)**
- Seven agents with typed handoffs.
- Policy renderer, guards, linter, and shadow mode for E2.
- Redaction pipeline; letter with the Art. 15(1) checklist; validator with the claims cross-check.
- Done when: the full suite runs, E1 and E2 are in the report, and the linter runs in CI.

**M5: advisor, observability and resilience (4 days)**
- Advisor service over A2A with an egress filter.
- OpenTelemetry tracing and metrics with the scrubber; the private Jaeger/Grafana stack.
- Timeouts, retries, circuit breakers, provider fallback, degradation tiers, checkpoints, locks.
- Failure-injection tests.
- Done when: one case shows a complete trace including the advisor call, the leak and egress tests read 0, and the
  failure tests pass.

**M6: web app (4 days)**
- React app: Try it (live run view), reviewer screen, Break it page, run trace view, eval results page, recorded
  runs gallery.
- Server-sent events; per-visitor sandboxes; signed approvals; release service; audit log.
- Playwright tests of the main flows; accessibility pass (keyboard, contrast, screen-reader labels).
- Done when: the whole flow works locally in a browser and the Playwright tests pass.

**M7: public deployment and launch (4 days)**
- Server set-up, Compose deployment, HTTPS, backups, monitoring, alerts to the operator.
- Rate limits, human check, daily spend cap, PII detector on input, privacy and AI notices.
- Load test (for example 20 concurrent visitors) and a small public red-team round. Fix what it finds.
- Full eval run on the test split, the 3-run spread, E3 and E4; README, video, eval report.
- Done when: the demo is live at a public URL, the hard gates pass, the spend cap and limits are tested, and the
  README links everything.

**Total:** about 26 focused days.

**Minimum version (about 12 days):** M0 to M3, a reduced M4, and a simple web page (Try it plus the run view)
deployed with the spend cap.

## 15. Costs

### 15.1 Model spend

Rough per-case assumptions, re-measured in M2:

| Agent | Calls | Input tokens | Output tokens |
|---|---|---|---|
| Intake | 1 | ~3k | ~0.5k |
| Identity | ~3 | ~14k | ~1.5k |
| Discovery | ~6 | ~54k | ~2.4k |
| Redaction | ~3 | ~24k | ~4.5k |
| Letter | 1-2 | ~6k | ~1.5k |
| Validator | ~2 | ~10k | ~1k |
| Coordinator | ~5 | ~30k | ~3k |
| **Total per case** | | **~140k** | **~15k** |

Estimated cost per case, assuming about 60% of input tokens hit the cache, using the prices in 15.3. Thinking
mode adds output, which explains the upper ends:

| Model | Per case | Whole project (~700 case runs) | Public demo, per 100 runs |
|---|---|---|---|
| `deepseek-flash` (peak price) | ~$0.03-0.05 | ~$25-35 | ~$3-5 |
| `deepseek-flash` (off-peak, half price) | ~$0.02-0.03 | ~$15-20 | ~$2-3 |
| `deepseek-v4-pro` (peak price) | ~$0.14-0.20 | ~$100-140 | ~$14-20 |
| Mistral Small 4 | ~$0.02-0.03 | ~$15-20 | ~$2-3 |
| Mistral Large 4 | ~$0.08-0.10 | ~$55-70 | ~$8-10 |

Mistral's EU endpoint costs 1.1 times list price. Eval runs are scheduled off-peak for DeepSeek. All figures are
re-measured with real token counts in M2.

### 15.2 Hosting

| Item | Estimate |
|---|---|
| One EU virtual server for the whole stack | roughly EUR 5-15 per month, depending on size |
| CDN/DNS layer | free tier |
| Domain (optional) | about EUR 10-15 per year |
| Daily model-spend cap for the public demo | set by the operator (decision D4) |

### 15.3 Provider prices and facts (official pages, read 2026-11-20)

**DeepSeek** (sources: https://api-docs.deepseek.com/quick_start/pricing , /guides/tool_calls , /guides/json_mode ,
/guides/kv_cache , /quick_start/rate_limit)
- **Models:** `deepseek-flash` (DeepSeek-V4.1-Flash) and `deepseek-v4-pro` (DeepSeek-V4-Pro). Both have a 1M-token
  context. The older `deepseek-chat` / `deepseek-reasoner` names don't appear in the current docs.
- **Prices in USD per 1M tokens, peak hours:**

  | Model | Input, cache hit | Input, cache miss | Output |
  |---|---|---|---|
  | `deepseek-flash` | 0.006 | 0.30 | 1.20 |
  | `deepseek-v4-pro` | 0.044 | 1.32 | 3.96 |

  Off-peak is half price. Peak hours are 01:00-04:00 and 06:00-10:00 UTC on weekdays.
- **Features:**
  - Tool calling on both models, in thinking and non-thinking modes. Thinking is on by default and can be switched
    per request.
  - `strict` tool schemas exist but are in beta: they need the `/beta` base URL, and every property must be
    required.
  - JSON output mode exists. If the output isn't valid JSON, `content` can come back blank, so the provider layer
    validates and retries.
  - Caching is automatic and best-effort. Usage reports cache hits and misses.
- **Endpoint and limits:** OpenAI-format base URL `https://api.deepseek.com`.
  Concurrency limits are 2500 (flash) and 500 (pro) per account.
- **Data location:** the privacy policy (updated Feb 10, 2026) says data is collected, processed and stored "in
  People's Republic of China". It also lists training on "User Input" as a use. Whether that covers API inputs
  isn't settled on that page; the terms of use and the model-training disclosure page are still to be read
  in M0.

**Mistral** (sources: https://docs.mistral.ai/getting-started/models/ , https://docs.mistral.ai/inference/pricing ,
https://docs.mistral.ai/inference/regional-inference)
- **Models and prices in USD per 1M tokens:**

  | Model | Input | Cached input | Output | Context |
  |---|---|---|---|---|
  | Mistral Small 4 (`mistral-small-2603`) | 0.15 | 0.015 | 0.60 | 256k |
  | Mistral Large 4 (`mistral-large-4`) | 0.70 | 0.07 | 2.10 | 1M |
  | DeepSeek V4.1 Flash, hosted by Mistral | 0.30 | 0.007 | 1.20 | - |

  Mistral's marketing page shows an older Large price, so the console figure is confirmed in M0.
- **Features:** function calling, structured outputs and JSON mode, on a chat-completions API in the OpenAI format.
  The OpenAI SDK with `https://api.mistral.ai/v1` is tested in M0 because the docs don't state it explicitly.
- **EU hosting:** the EU endpoint `api.eu.mistral.ai` runs in EU and EFTA data centres, at 1.1 times list price.
  Function calling is supported there. Whether the Mistral-hosted DeepSeek model is offered on it is checked in M0.

**What this means for a GDPR project:**
- For development and eval runs the data is fully synthetic, so DeepSeek's cheap prices are a good fit.
- The public demo is different. Visitors type free text, and the project's whole message is careful handling of
  personal data. Sending visitor text to a provider that stores it in China and may train on it would undercut
  that message.
- The demo therefore defaults to Mistral's EU endpoint (decision D3). The same provider layer makes this a
  configuration setting, and experiment E4 reports the quality and cost difference.

## 16. Risks

| Risk | Mitigation |
|---|---|
| Public demo abused or overspent | Rate limits, human check, hard daily cap, recorded-run fallback, alerts |
| Visitors type real personal data | Clear notice, input detector, fictional-only flows, sandbox deletion after 24 h |
| Synthetic data too easy | Generator difficulty settings, adversarial set, golden-20 review, limits stated in the report |
| Legal rules wrong | Every legal rule quotes its source (Appendix A); README disclaimer: not legal advice |
| Model output varies between runs | Spread measured over 3 runs; replay for exact reproduction |
| Provider outage or API change | Provider layer with fallback; circuit breaker; recorded runs |
| Provider data location (DeepSeek processes data in China) | Only synthetic data sent to DeepSeek; the public demo uses Mistral's EU endpoint; stated openly in the README |
| DeepSeek API quirks (blank JSON content, beta strict mode) | Provider layer validates every structured output against its schema and retries; contract tests per provider |
| SDKs change (MCP, A2A move fast) | Versions pinned; protocol code isolated behind the tool layer |
| Scope creep | Minimum version defined; anything new goes to a backlog unless it closes a hard gate |

## 17. Decisions to confirm

| # | Decision | Default in this plan | Alternatives |
|---|---|---|---|
| D1 | Project | Article 15 access-request desk | - |
| D2 | Agent core | Own small core | LangGraph; OpenAI Agents SDK (also works with compatible providers) |
| D3 | Models | **Confirmed:** DeepSeek for development and evals; Mistral EU endpoint for the public demo | - |
| D4 | Daily spend cap for the public demo | **Confirmed:** EUR 2 per day on the shared key. Past the cap, visitors can bring their own API key (section 11.3) | - |
| D5 | Web app | React + TypeScript + Tailwind | Server-rendered pages (simpler, less polished) |
| D6 | Hosting | One EU virtual server with Docker Compose | A platform service (Fly.io, Render, Railway) |
| D7 | Domain | A dedicated domain | The host's free subdomain |
| D8 | Request languages | French and English | English only |
| D9 | Repository | Public on GitHub from the start, MIT licence | Private until launch |
| D10 | Build size | **Confirmed:** full plan (~26 days) | - |

## 18. Requirement traceability

| Requirement | Design element | How it is tested |
|---|---|---|
| Answer within one month; extension rules (Art. 12(3)) | Deadline computed in the case store; daily sweep; extension workflow | Due-date accuracy incl. month-end and weekend cases |
| Proportionate identity check (Art. 12(6)) | Identity agent; access granted only by code | Identity accuracy; impostor accepted = 0 |
| All personal data plus Art. 15(1) information | Discovery; Letter; Validator checklist | Discovery recall; Art. 15(1) completeness |
| Protect other people (Art. 15(4)) | Redaction pipeline and verifier | Redaction recall; leak gate = 0 |
| Never another person's records | Person-limited MCP tools | Foreign-record gate = 0; scope tests |
| A person approves every release | Signed token; release service | Unapproved release = 0; E2 |
| Special-category data handled with care (Art. 9(1)) | Flag to a person | Flag recall on seeded cases |
| Excessive or unfounded requests (Art. 12(5)) | Advisor plus a person decides | Routing accuracy on edge cases |
| No personal data to the advisor | Egress filter | Advisor payload gate = 0 |
| Observability without personal data | Hashed IDs, scrubber, separate prompt store | Telemetry leak gate = 0 |
| Survives failures | Timeouts, retries, breakers, fallback, checkpoints, locks | Failure-injection tests |
| Public demo safe and affordable | Rate limits, cap, sandboxes, input detector | Load test, cap test, red-team round |
| Policy changes versioned and gated | policy.yaml, linter, change rule | Linter in CI; E3 |

## 19. How this plan was checked

| Check | Result |
|---|---|
| Legal rules against the official text | Art. 9(1), 12 and 15 checked on EUR-Lex; gdpr-info.eu has the same wording |
| Points the regulation leaves open (identity proof, month counting, redaction, scope) | Taken from the EDPB guidelines v2.1 text (Appendix A) |
| Model names, prices, features, data location | From DeepSeek's and Mistral's official documentation pages, read 2026-11-20 (section 15.3) |
| Cost arithmetic | Recomputed from those prices and the per-case token assumptions (section 15.1) |
| Internal consistency | Every "section N" reference exists; milestone days add up to 26; every decision D1-D10 has a default |
| Every requirement has a design element and a test | Section 18 |
| Tools on the build machine | git, Docker, uv, Node and Python 3.12 present |

Still open, to be settled in M0:
- Current versions of the MCP, A2A, OpenTelemetry and Presidio packages.
- DeepSeek's terms on API data and training.
- A live test of Mistral's endpoint with the OpenAI SDK.
- Whether the Mistral-hosted DeepSeek model is available on the EU endpoint.

## Appendix A: legal sources

Sources:
- **[EUR-Lex]:** Regulation (EU) 2016/679, official text,
  https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679 (OJ L 119, 4.5.2016).
- **[gdpr-info]:** https://gdpr-info.eu/art-12-gdpr/, /art-15-gdpr/, /art-9-gdpr/. Same wording as EUR-Lex.
- **[EDPB]:** Guidelines 01/2022 on data subject rights, Right of access, version 2.1,
  https://www.edpb.europa.eu/system/files/documents/2023-04/edpb_guidelines_202201_data_subject_rights_access_v2_en.pdf

| Topic | What the source says (short quotes) | Source |
|---|---|---|
| Deadline | "without undue delay and in any event within one month of receipt of the request" | Art. 12(3) |
| Extension | "may be extended by two further months where necessary, taking into account the complexity and number of the requests"; the requester is told "within one month of receipt", "together with the reasons for the delay" | Art. 12(3) |
| Counting the month | A request on 31 August is due 30 September, because there is no 31 September: the last day of the following month. A deadline on a weekend or public holiday moves to the next working day | EDPB para. 160-161 |
| No action | Inform "without delay and at the latest within one month", with the reasons and "the possibility of lodging a complaint with a supervisory authority and seeking a judicial remedy" | Art. 12(4) |
| Free; excessive requests | "free of charge"; for "manifestly unfounded or excessive" requests, "in particular because of their repetitive character", a reasonable fee or refusal; the controller "shall bear the burden of demonstrating" it. The EDPB says these concepts "have to be interpreted narrowly" | Art. 12(5); EDPB |
| Identity | With "reasonable doubts concerning the identity", ask for "additional information necessary to confirm the identity" | Art. 12(6) |
| Identity, proportionality | "it is disproportionate to require a copy of an identity document" when the requester is already authenticated. Better: logging in, or non-intrusive security questions | EDPB para. 73-74 |
| Contents | (a) purposes, (b) categories, (c) recipients, (d) storage period or criteria, (e) rectification, erasure, restriction, objection, (f) complaint, (g) source, (h) automated decision-making including profiling | Art. 15(1) |
| Copy | "a copy of the personal data undergoing processing"; fees only for further copies; "a commonly used electronic form" for electronic requests | Art. 15(3) |
| Others' rights | The copy "shall not adversely affect the rights and freedoms of others". The EDPB: it "should not result in refusing the data subject's request altogether", only "leaving out or rendering illegible" the affected parts | Art. 15(4); EDPB |
| Scope | Includes e.g. "history of purchases", "activity logs, search activities"; pseudonymised data is still personal data | EDPB |
| Special categories | Racial or ethnic origin, political opinions, religious or philosophical beliefs, trade union membership, genetic data, biometric data for unique identification, health, sex life or sexual orientation | Art. 9(1) |

## Appendix B: external requirements

- A DeepSeek API key and a Mistral API key (Mistral powers the public demo and is the fallback).
- A spending limit on both provider accounts.
- About 2 hours in M3 to review the golden 20 cases, and a sign-off on policy v1 in M0.
- For launch: a server account with an EU host, optionally a domain, and a GitHub account.
