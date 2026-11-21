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
    flags: { special_category_found: boolean };
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
