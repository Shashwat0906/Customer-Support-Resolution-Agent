// Typed client for the FastAPI backend. The base URL is fixed at build time (VITE_API_BASE_URL).

export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8000";

export interface Principal {
  kind: "customer" | "staff";
  id: string;
  name: string;
  role: string;
  approval_limit_minor?: number;
}

export interface DemoUsers {
  customers: { id: string; name: string; subject: string }[];
  reviewers: { id: string; name: string; role: string; subject: string; approval_limit_minor: number }[];
}

export interface TicketMessage {
  author_type: "customer" | "agent" | "reviewer" | "system";
  body: string;
  created_at: string;
}

export interface Ticket {
  ticket_id: string;
  customer_id: string;
  order_id: string | null;
  category: string;
  status: string;
  summary: string;
  created_at: string;
  updated_at: string;
  latest_run_id: string | null;
  latest_run_status: string | null;
  messages: TicketMessage[];
}

export interface Citation {
  document_id: string;
  version: string;
  section_ref: string;
  quote: string;
  added_by?: string;
}

export interface Decision {
  kind: string;
  disposition: string;
  reason_code: string;
  explanation: string;
  amount_minor: number;
  refundable_balance_minor: number;
  policy_document_id: string | null;
  policy_version: string | null;
  requires_senior_review: boolean;
  missing_information: string[];
}

export interface RunState {
  order_id?: string | null;
  complaint_category?: string | null;
  verified_facts?: Record<string, any>;
  policy_references?: Citation[];
  missing_information?: string[];
  evidence?: { facts: { ref: string; statement: string }[]; gaps: string[]; summary: string } | null;
  policy_finding?: { interpretation: string; missing_policy: boolean; conflict: boolean } | null;
  resolution_draft?: { action: string; amount_minor: number; reason: string } | null;
  decision?: Decision | null;
  clarification_question?: string | null;
  outcome?: string | null;
  escalation_reason?: string | null;
  execution_status?: string | null;
  external_result_reference?: string | null;
  error_details?: { code: string; message: string }[];
  retry_counts?: Record<string, number>;
  ticket_sync?: { synced: boolean; attempts: number; error: string | null } | null;
  proposal_corrections?: number;
  unauthorized_tool_attempts?: number;
}

export interface Run {
  run_id: string;
  ticket_id: string;
  status: string;
  outcome: string | null;
  agent_mode: string;
  started_at: string | null;
  completed_at: string | null;
  final_response: string | null;
  error_code: string | null;
  state: RunState;
  metrics: Record<string, any> | null;
  pending_approval_id: string | null;
}

export interface RunEvent {
  id: number;
  created_at: string;
  actor: string;
  event_type: string;
  payload: Record<string, any> | null;
}

export interface ApprovalView {
  approval_id: string;
  decision: string;
  expires_at: string;
  decided_at: string | null;
  reviewer: { id: string; name: string } | null;
  note: string | null;
  proposal: { proposal_id: string; status: string; revision: number; payload: Record<string, any>; payload_hash: string };
  run_id: string | null;
  ticket: { ticket_id: string; summary: string; category: string; status: string } | null;
  verified_facts: Record<string, any>;
  policy_references: Citation[];
  investigation_summary: string | null;
  can_approve?: boolean;
}

export interface OrderSummary {
  order_id: string;
  status: string;
  placed_at: string;
  delivered_at: string | null;
  paid_amount_minor: number;
  items: string[];
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

const TOKEN_KEY = "support-demo-token";
const PRINCIPAL_KEY = "support-demo-principal";

function safeGet(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string | null): void {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the session simply does not persist */
  }
}

let token: string | null = safeGet(TOKEN_KEY);

export function storedPrincipal(): Principal | null {
  const raw = safeGet(PRINCIPAL_KEY);
  return raw ? (JSON.parse(raw) as Principal) : null;
}

export function setSession(t: string | null, p: Principal | null): void {
  token = t;
  safeSet(TOKEN_KEY, t);
  safeSet(PRINCIPAL_KEY, p ? JSON.stringify(p) : null);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", `Cannot reach the API at ${API_BASE}. Is the backend running?`);
  }
  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const err = data?.error ?? {};
    throw new ApiError(resp.status, err.code ?? `HTTP_${resp.status}`, err.message ?? resp.statusText, err.details);
  }
  return data as T;
}

export const api = {
  health: () => request<{ status: string; checks: Record<string, string> }>("GET", "/health"),
  demoUsers: () => request<DemoUsers>("GET", "/auth/demo-users"),
  login: (subject: string) =>
    request<{ access_token: string; principal: Principal }>("POST", "/auth/demo-login", { subject }),
  orders: () => request<OrderSummary[]>("GET", "/orders"),
  tickets: () => request<Ticket[]>("GET", "/tickets"),
  ticket: (id: string) => request<Ticket>("GET", `/tickets/${id}`),
  createTicket: (message: string, order_id?: string) =>
    request<Ticket>("POST", "/tickets", { message, ...(order_id ? { order_id } : {}) }),
  startRun: (ticketId: string, agent_mode?: string) =>
    request<{ run_id: string }>("POST", `/tickets/${ticketId}/runs`, agent_mode ? { agent_mode } : undefined),
  run: (id: string) => request<Run>("GET", `/runs/${id}`),
  events: (id: string) => request<RunEvent[]>("GET", `/runs/${id}/events`),
  reply: (id: string, message: string) => request<Run>("POST", `/runs/${id}/messages`, { message }),
  approvals: (status: "pending" | "decided" | "all" = "pending") =>
    request<ApprovalView[]>("GET", `/approvals?status=${status}`),
  approval: (id: string) => request<ApprovalView>("GET", `/approvals/${id}`),
  decide: (id: string, decision: "approve" | "reject", note: string) =>
    request<{ decision: string }>("POST", `/approvals/${id}/decision`, { decision, note }),
  faults: () => request<{ rules: any[]; hits: Record<string, number> }>("GET", "/demo/faults"),
  addFault: (rule: Record<string, unknown>) => request<unknown>("POST", "/demo/faults", rule),
  clearFaults: () => request<unknown>("DELETE", "/demo/faults"),
  providerRefunds: () => request<{ refunds: any[] }>("GET", "/demo/provider-refunds"),
};

export function formatINR(minor: number | null | undefined): string {
  if (minor === null || minor === undefined) return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(minor / 100);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export const WAITING = new Set(["awaiting_customer", "awaiting_approval"]);
export const TERMINAL = new Set(["completed", "escalated", "rejected", "expired", "failed"]);
