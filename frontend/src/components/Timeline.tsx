import type { RunEvent } from "../api/client";
import { formatDate, formatINR } from "../api/client";

type Tone = "ok" | "warn" | "danger" | "info" | "violet" | "";

function describe(e: RunEvent): { title: string; detail?: string; tone: Tone } | null {
  const p = e.payload ?? {};
  switch (e.event_type) {
    case "run_queued": return { title: "Workflow started", tone: "info" };
    case "classified": return { title: `Classified as ${String(p.category ?? "…").replace(/_/g, " ")}`,
      detail: p.order_reference ? `Order mentioned: ${p.order_reference}` : undefined, tone: "info" };
    case "order_located": return { title: `Order ${p.order_id ?? ""} identified`,
      detail: p.source ? String(p.source).replace(/_/g, " ") : undefined, tone: "ok" };
    case "order_access_denied": return { title: "Order not found on this account", detail: p.reference, tone: "warn" };
    case "tool_called": return { title: `${e.actor.replace("agent:", "")} agent called ${p.tool ?? "a tool"}`,
      detail: p.ok === false ? `failed: ${p.error}` : undefined, tone: p.ok === false ? "warn" : "" };
    case "tool_denied": return { title: "Tool call refused (not permitted for this agent)", detail: p.tool, tone: "danger" };
    case "read_retry": return { title: `Retrying ${p.service} (${p.kind})`, detail: `attempt ${p.attempt}`, tone: "warn" };
    case "agent_output_invalid": return { title: "Model output failed validation", detail: "repair attempted", tone: "warn" };
    case "decision_made": if (!p.disposition) return { title: "Eligibility checked against policy rules", tone: "violet" };
      return { title: `Decision: ${String(p.disposition)} (${String(p.reason_code).replace(/_/g, " ")})`,
      detail: [p.policy, p.amount_minor ? formatINR(p.amount_minor) : null].filter(Boolean).join(" · "), tone: "violet" };
    case "proposal_corrected": return { title: "Model proposal corrected by policy rules",
      detail: `proposed ${p.draft_action} ${p.draft_amount ? formatINR(p.draft_amount) : ""} → ${p.permitted_action} ${p.permitted_amount ? formatINR(p.permitted_amount) : ""}`, tone: "warn" };
    case "citation_added": return { title: "Governing policy citation attached", detail: p.document_id, tone: "" };
    case "citation_rejected": return { title: "Citation from an inapplicable policy version removed", tone: "warn" };
    case "clarification_requested": return { title: "Asked the customer for information", detail: p.question, tone: "warn" };
    case "customer_replied": return { title: "Customer replied", detail: p.message, tone: "info" };
    case "proposal_created": if (p.amount_minor === undefined) return { title: "Refund proposal created", tone: "violet" };
      return { title: `Refund proposal created (${formatINR(p.amount_minor)})`,
      detail: `revision ${p.revision}${p.requires_senior_review ? " · senior review required" : ""}`, tone: "violet" };
    case "approval_requested": return { title: "Waiting for reviewer approval", tone: "violet" };
    case "approval_approved": return { title: "Reviewer approved the refund", detail: p.note ?? undefined, tone: "ok" };
    case "approval_rejected": return { title: "Reviewer rejected the refund", detail: p.note ?? undefined, tone: "danger" };
    case "approval_expired": return { title: "Approval request expired", tone: "warn" };
    case "approval_invalidated": return { title: "Approval invalidated: facts changed", detail: p.reason_code, tone: "warn" };
    case "refund_reserved": if (p.amount_minor === undefined) return { title: "Refund amount reserved", tone: "info" };
      return { title: `Refund amount reserved (${formatINR(p.amount_minor)})`,
      detail: `idempotency key ${p.idempotency_key}`, tone: "info" };
    case "refund_succeeded": return { title: "Payment provider confirmed the refund", detail: p.provider_reference, tone: "ok" };
    case "refund_failed": return { title: "Refund failed at the provider", detail: p.reason, tone: "danger" };
    case "refund_blocked": return { title: "Refund execution blocked", detail: p.reason, tone: "danger" };
    case "refund_execution_result": return { title: `Refund execution: ${p.status}`, tone: p.status === "succeeded" ? "ok" : "warn" };
    case "refund_verified": return { title: `Refund verified: ${p.status}`,
      detail: p.reconcile_attempts ? `${p.reconcile_attempts} reconciliation check(s)` : undefined, tone: p.status === "succeeded" ? "ok" : "warn" };
    case "provider_unavailable": return { title: `${p.service} service unavailable`, tone: "danger" };
    case "limit_reached": return { title: "Step/cost limit reached", tone: "danger" };
    case "escalated": return { title: "Escalated to a specialist", detail: p.reason, tone: "danger" };
    case "response_guard_corrected": return { title: "Response corrected to match verified state", tone: "warn" };
    case "response_sent": return { title: "Response sent to customer", tone: "ok" };
    case "ticket_updated": return { title: `Ticket updated (${p.status})`, detail: p.attempts > 1 ? `${p.attempts} attempts` : undefined, tone: "ok" };
    case "ticket_sync_failed": return { title: "Ticket system sync failed", detail: `${p.attempts} attempts; will retry`, tone: "warn" };
    case "run_recovered": return { title: "Workflow recovered after interruption", tone: "warn" };
    case "run_failed": return { title: "Workflow failed", tone: "danger" };
    case "ticket_created": return null;
    default: return { title: e.event_type.replace(/_/g, " "), tone: "" };
  }
}

export function Timeline({ events }: { events: RunEvent[] }) {
  const items = events.map((e) => ({ e, d: describe(e) })).filter((x) => x.d);
  if (!items.length) return <div className="muted small">No events yet.</div>;
  return (
    <ol className="timeline">
      {items.map(({ e, d }) => (
        <li key={e.id}>
          <span className={`dot ${d!.tone}`} />
          <div>
            <div className="title">{d!.title}</div>
            {d!.detail && <div className="detail">{d!.detail}</div>}
            <div className="detail">{formatDate(e.created_at)} · {e.actor}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}
