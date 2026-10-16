import type { Citation, Decision, RunState } from "../api/client";
import { formatDate, formatINR } from "../api/client";
import { Badge, StatusBadge, label } from "./ui";

export function OrderFacts({ facts }: { facts: Record<string, any> | undefined }) {
  const order = facts?.order;
  if (!order) return <div className="muted small">No verified order facts yet.</div>;
  const ret = facts?.return?.latest;
  const pay = facts?.payment;
  const ship = facts?.shipment;
  return (
    <div className="stack">
      <dl className="kv">
        <dt>Order</dt><dd>{order.order_id} · <StatusBadge value={order.status} /></dd>
        <dt>Items</dt><dd>{order.items?.map((i: any) => `${i.name} × ${i.quantity}`).join(", ")}</dd>
        <dt>Paid</dt><dd>{formatINR(order.paid_amount_minor)}</dd>
        <dt>Refunded</dt><dd>{formatINR(order.refunded_amount_minor)}{order.reserved_refund_minor > 0 && ` (+${formatINR(order.reserved_refund_minor)} in progress)`}</dd>
        <dt>Delivered</dt><dd>{formatDate(order.delivered_at)}</dd>
      </dl>
      {facts?.return && (
        <dl className="kv">
          <dt>Return</dt>
          <dd>{ret ? <><StatusBadge value={ret.status} /> requested {formatDate(ret.requested_at)}</> : "No return started"}</dd>
          {ret && <><dt>Received</dt><dd>{formatDate(ret.received_at)}</dd>
            <dt>Inspection</dt><dd><StatusBadge value={ret.inspection_status} /></dd></>}
        </dl>
      )}
      {pay && (
        <dl className="kv">
          <dt>Payment</dt><dd>{pay.method?.toUpperCase()} · {pay.status} · <span className="mono">{pay.payment_reference}</span></dd>
          <dt>Provider</dt>
          <dd>{pay.provider_reachable === false ? <Badge tone="danger">unreachable</Badge> :
            <>refunded {formatINR(pay.provider_refunded_minor)}, pending {formatINR(pay.provider_refund_pending_minor)}</>}</dd>
        </dl>
      )}
      {ship && (
        <dl className="kv">
          <dt>Carrier</dt><dd>{ship.carrier} · <span className="mono">{ship.tracking_number}</span></dd>
          <dt>Tracking</dt><dd><StatusBadge value={ship.status} /> ETA {formatDate(ship.estimated_delivery_at)}</dd>
          <dt>Latest</dt><dd>{ship.events?.length ? `${label(ship.events.at(-1).status)} — ${ship.events.at(-1).location}` : "—"}</dd>
        </dl>
      )}
    </div>
  );
}

export function Citations({ citations }: { citations: Citation[] | undefined }) {
  if (!citations?.length) return <div className="muted small">No policy citations.</div>;
  return (
    <div className="stack">
      {citations.map((c, i) => (
        <div className="citation" key={`${c.document_id}${c.section_ref}${i}`}>
          <div className="ref">
            {c.document_id} {c.section_ref} <span className="muted">v{c.version}</span>
            {c.added_by === "system" && <> · <Badge>attached by rules</Badge></>}
          </div>
          <blockquote>{c.quote}</blockquote>
        </div>
      ))}
    </div>
  );
}

export function DecisionPanel({ decision, state }: { decision: Decision | null | undefined; state: RunState }) {
  if (!decision) return <div className="muted small">No decision yet.</div>;
  const draft = state.resolution_draft;
  return (
    <div className="stack">
      <div className="row">
        <StatusBadge value={decision.disposition} />
        <strong>{label(decision.reason_code)}</strong>
      </div>
      <div>{decision.explanation}</div>
      <dl className="kv">
        <dt>Policy</dt><dd>{decision.policy_document_id ?? "none in effect"} {decision.policy_version && `(v${decision.policy_version})`}</dd>
        {decision.amount_minor > 0 && <><dt>Permitted amount</dt><dd>{formatINR(decision.amount_minor)}</dd></>}
        {draft && <><dt>Model proposed</dt><dd>{label(draft.action)} {draft.amount_minor ? formatINR(draft.amount_minor) : ""}</dd></>}
        {decision.requires_senior_review && <><dt>Review</dt><dd><Badge tone="violet">Senior reviewer required</Badge></dd></>}
      </dl>
    </div>
  );
}
