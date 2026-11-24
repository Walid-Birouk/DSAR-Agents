// The desk's API. Every call sends the anonymous session cookie; runs belong to the session that started them.

export type Preset = {
  case_id: string;
  kind: string;
  language: string;
  sender: string;
  subject: string;
  text: string;
  received_on: string;
};

export type Customer = { customer_id: string; name: string; email: string; language: string };

export type RunRow = {
  run_id: string;
  case_id: string;
  mode: string;
  arm: string;
  source: string;
  status: string;
  state: string | null;
  created_at: string;
  cost_usd: number;
  error?: string | null;
};

export type ToolEntry = {
  agent: string;
  tool: string;
  ok: boolean;
  error_kind: string | null;
  text: string;
  duration_s: number;
};

export type AuditRow = {
  seq: number;
  at: string;
  actor: string;
  action: string;
  detail: string;
  policy_version: string;
  pack_hash: string | null;
};

export type PackRecord = { system: string; table: string; record_id: string; fields: Record<string, unknown> };

export type RunDetail = {
  run: RunRow;
  request: { case_id: string; sender: string; subject_line: string; text: string; received_on: string } | null;
  case: {
    state: string;
    deadlines: { received: string; due: string; extended_due: string; extension_notice_by: string };
    history: { from_state: string; to_state: string; at: string; note: string }[];
    letter: { subject_line: string; body: string } | null;
    advice: { recommendation: string; reasons: string[]; sources: string[] }[];
    rejection_reason: string | null;
    refusal: { kind: string; language: string; subject_line: string; body: string } | null;
    flags: { special_category_found: boolean; repeat_request?: boolean; large_request?: boolean };
  } | null;
  outcome: { totals: Record<string, number>; stop_reason: string; elapsed_s: number } | null;
  pack: {
    records: PackRecord[];
    record_counts: Record<string, number>;
    redactions: { system: string; record_id: string; field: string; category: string }[];
    special_category: { system: string; record_id: string; field: string; category: string }[];
  } | null;
  tools: ToolEntry[];
  audit: AuditRow[];
  can_review: boolean;
  can_send_refusal: boolean;
  special_category: boolean;
};

export type Span = {
  name: string;
  span_id: string;
  parent_id: string | null;
  status: string;
  attributes: Record<string, unknown>;
  events: { name: string; attributes: Record<string, unknown> }[];
  duration_ms: number;
};

export type System = {
  runs_today: number;
  finished_today: number;
  success_rate: number | null;
  attempts: number;
  attempts_blocked: number;
  spent_today_usd: number;
  daily_cap_usd: number;
  mode: string;
  arm: string;
  policy_version: string;
};

export type EvalSummary = {
  name: string;
  suite: string;
  agent: string;
  arm: string;
  started_at: string;
  green: boolean;
  metrics: Record<string, number | null>;
  cases: number;
  by_kind: Record<string, { cases: number; passed: number }>;
  cost: number;
};

export type ForeignRecord = {
  system: string;
  table: string;
  record_id: string;
  owner: string | null;
  owner_is_customer: boolean;
  fields: Record<string, unknown>;
};

export type Mention = { system: string; record_id: string; field: string; text: string; note: string; context: string };

export type Story = {
  case_id: string;
  subject: string;
  request: { sender: string; subject_line: string; text: string; received_on: string };
  due: string;
  records_by_system: Record<string, number>;
  records_total: number;
  gloss: Record<string, string>;
  naive: {
    broken_versions: string[];
    records_total: number;
    records_own: number;
    foreign_records: ForeignRecord[];
    readable_mentions: Mention[];
  };
  recorded: { available: boolean; model: string | null; elapsed_s: number | null; cost_usd: number | null; model_calls: number | null };
  mode: "replay" | "scripted";
};

export type GuardInfo = { id: string; title: string; rules: string[]; code: string; what: string };
export type Catch = { run: string; case_id: string; kind: string; tool: string; attempt: string; refusal: string };
export type E2Arm = {
  run: string;
  cases: number;
  third_party_leaks: number;
  foreign_records: number;
  impostor_accepted: number;
  injection_succeeded: number;
  released_without_approval: number;
  violations_let_through: number;
  violations_refused: number;
  cases_with_violation_at_approval: number;
  passed: number;
  cost_usd: number;
  reruns?: string[];
};
export type Harness = {
  guards: GuardInfo[];
  catches: {
    runs: string[];
    cases: number;
    distinct_requests: number;
    tool_calls: number;
    model_refusals: number;
    cases_with_a_refusal: number;
    by_guard: Record<string, { count: number; cases: number; examples: Catch[] }>;
  };
  cost: {
    run: string;
    model: string;
    cases: number;
    cost_per_case_usd: number;
    model_calls_per_case: number;
    tool_calls_per_case: number;
    input_tokens_per_case: number;
    output_tokens_per_case: number;
    elapsed_p50_s: number;
  };
  scaling?: {
    run: string;
    cases: number;
    cost_per_record_usd: number;
    input_tokens_per_record: number;
    cache_hit_share: number;
    input_token_cap: number;
    records_before_cap: number;
    scenarios: { id: string; provider: string; model: string; peak_or_eu: boolean }[];
    rows: {
      records: number;
      measured: { cost_usd: number; input_tokens?: number; case_id?: string; average?: boolean } | null;
      input_tokens: number;
      over_cap: boolean;
      cost_usd: Record<string, number>;
    }[];
  } | null;
  e2?: {
    cases_planned: number;
    rows: { key: string; label: string }[];
    shadow: E2Arm;
    guarded: E2Arm;
    let_through: {
      case_id: string;
      kind: string;
      third_party_leaks: number;
      foreign_records: number;
      would_block: number;
      rules: string[];
      category: "leak_caught" | "leak_missed" | "procedure" | "harmless";
      now_flagged?: string[];
      final_state: string;
    }[];
  };
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin", ...init });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { error?: string; detail?: string }).error ?? `request failed (${r.status})`);
  return body as T;
}

const post = <T,>(path: string, body: unknown) =>
  call<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  system: () => call<System>("/api/system"),
  story: () => call<Story>("/api/story"),
  // The first page works without the backend: a static copy of the same summary ships with the web app.
  harness: () => call<Harness>("/api/harness").catch(() => call<Harness>("/harness.json")),
  // Whether the live desk (the backend) is connected to this site.
  connected: () =>
    call<{ ok: boolean }>("/api/health")
      .then((h) => h.ok === true)
      .catch(() => false),
  presets: (kind: "try" | "break") => call<Preset[]>(`/api/presets?kind=${kind}`),
  customers: () => call<Customer[]>("/api/customers"),
  runs: () => call<RunRow[]>("/api/runs"),
  run: (id: string) => call<RunDetail>(`/api/runs/${id}`),
  trace: (id: string) => call<Span[]>(`/api/runs/${id}/trace`),
  evals: () => call<EvalSummary[]>("/api/evals"),
  start: (body: { case_id?: string; customer_id?: string; subject?: string; text?: string; source?: string }) =>
    post<{ run_id: string }>("/api/runs", body),
  review: (id: string, body: { action: string; note?: string; special_category_acknowledged?: boolean }) =>
    post<{ state: string }>(`/api/runs/${id}/review`, body),
};

export const KIND_LABELS: Record<string, string> = {
  standard: "Standard request",
  identity_other_email: "Writes from another address",
  identity_impostor: "Impostor",
  identity_representative_no_mandate: "Representative without a mandate",
  identity_no_account: "No account",
  edge_repeat: "Repeated request",
  edge_other_rights: "Also asks for erasure",
  edge_extension: "Very large request",
  adversarial_injection_request: "Prompt injection in the request",
  adversarial_injection_record: "Prompt injection in a record",
  adversarial_malformed: "Heavy typos",
  adversarial_oversized: "Very long request",
};

export const STATE_LABELS: Record<string, string> = {
  RECEIVED: "Received",
  INTAKE_DONE: "Read",
  IDENTITY_CHECK: "Checking identity",
  AWAITING_REQUESTER: "Waiting for the requester",
  IDENTITY_VERIFIED: "Identity verified",
  DISCOVERY_DONE: "Records found",
  REDACTION_DONE: "Others' data hidden",
  DRAFT_READY: "Reply drafted",
  VALIDATED: "Validated",
  AWAITING_APPROVAL: "Waiting for your approval",
  APPROVED: "Approved",
  RELEASED: "Sent",
  CLOSED: "Closed",
  REJECTED: "Rejected",
  NEEDS_HUMAN: "With a person",
  EXTENDED: "Deadline extended",
  PARTIAL: "Partial (a system was down)",
};

export type SessionInfo = { live: boolean; own_key: string | null; human_check: boolean };

export const session = {
  info: () => call<SessionInfo>("/api/session"),
  bringKey: (provider: string, key: string) => post<{ own_key: string }>("/api/byok", { provider, key }),
  dropKey: () => call<{ own_key: null }>("/api/byok", { method: "DELETE" }),
};

// The human check: find a nonce whose SHA-256 with the server's challenge starts with `bits` zero bits.
async function leadingZeroBits(challenge: string, nonce: number, bits: number): Promise<boolean> {
  const data = new TextEncoder().encode(`${challenge}:${nonce}`);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
  let left = bits;
  for (const byte of digest) {
    if (left <= 0) return true;
    if (left >= 8) {
      if (byte !== 0) return false;
      left -= 8;
    } else {
      return byte >> (8 - left) === 0;
    }
  }
  return true;
}

export async function humanCheck(): Promise<{ challenge: string; nonce: string }> {
  const { challenge, bits } = await call<{ challenge: string; bits: number }>("/api/challenge");
  for (let n = 0; ; n++) {
    if (await leadingZeroBits(challenge, n, bits)) return { challenge, nonce: String(n) };
  }
}
