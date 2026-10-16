import type { ReactNode } from "react";
import { ApiError } from "../api/client";

type Tone = "ok" | "warn" | "danger" | "info" | "violet" | "";

const STATUS_TONE: Record<string, Tone> = {
  completed: "ok", resolved: "ok", approved: "ok", succeeded: "ok", refund_succeeded: "ok", answered: "ok",
  awaiting_approval: "violet", pending: "violet", awaiting_customer: "warn", in_progress: "info", queued: "info",
  running: "info", executing: "info", reconciling: "warn", refund_processing: "warn", processing: "warn",
  escalated: "danger", failed: "danger", rejected: "danger", refund_rejected: "danger", expired: "warn",
  approval_expired: "warn", open: "info", invalidated: "warn", unknown: "warn",
  eligible: "ok", blocked: "danger", needs_info: "warn", answer: "info",
};

const LABELS: Record<string, string> = {
  awaiting_approval: "Awaiting approval", awaiting_customer: "Needs your reply", refund_succeeded: "Refund confirmed",
  refund_processing: "Refund processing", refund_rejected: "Refund rejected", approval_expired: "Approval expired",
  in_progress: "In progress", order_status: "Order status", refund_request: "Refund", damaged_item: "Damaged item",
  unclassified: "New", eligible: "Eligible (needs approval)", blocked: "Not eligible", needs_info: "Needs information",
  answer: "Informational",
};

export function label(value: string | null | undefined): string {
  if (!value) return "—";
  return LABELS[value] ?? value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function StatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  return <span className={`badge ${STATUS_TONE[value] ?? ""}`}>{label(value)}</span>;
}

export function Badge({ tone = "", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-header">
          {typeof title === "string" ? <h2>{title}</h2> : title}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  return (
    <div className="alert danger" role="alert">
      <strong>{e.code ?? "Error"}</strong>: {e.message}
    </div>
  );
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return (
    <div className="card-body" aria-busy="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
