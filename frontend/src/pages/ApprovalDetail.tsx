import { useState } from "react";
import { api, formatDate, formatINR, type Principal } from "../api/client";
import { Citations, OrderFacts } from "../components/Evidence";
import { Badge, Card, ErrorAlert, Loading, StatusBadge, label } from "../components/ui";
import { useLoader } from "../hooks";

export function ApprovalDetail({ approvalId, principal }: { approvalId: string; principal: Principal }) {
  const approval = useLoader(() => api.approval(approvalId), [approvalId]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);

  if (approval.loading && !approval.data) return <div className="page"><Card><Loading lines={6} /></Card></div>;
  if (approval.error) return <div className="page"><ErrorAlert error={approval.error} /></div>;
  const a = approval.data!;
  const p = a.proposal.payload;
  const limit = principal.approval_limit_minor ?? 0;
  const expired = new Date(a.expires_at).getTime() <= Date.now();
  const blockers: string[] = [];
  if (a.decision !== "pending") blockers.push(`This approval is already ${a.decision}.`);
  if (expired && a.decision === "pending") blockers.push("This approval has expired.");
  if (p.amount_minor > limit) blockers.push(`The amount exceeds your approval limit of ${formatINR(limit)}.`);
  if (p.requires_senior_review && principal.role !== "senior_reviewer") blockers.push("A senior reviewer must approve this refund.");
  const canApprove = blockers.length === 0;

  async function submit(decision: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      await api.decide(approvalId, decision, note);
      setConfirming(null);
      await approval.refresh();
    } catch (e) {
      setError(e);
      await approval.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <a href="#/approvals" className="small">← Approval queue</a>
          <h1>Refund approval · {p.order_id}</h1>
          <span className="muted small">{a.approval_id} · proposal {a.proposal.proposal_id} (revision {a.proposal.revision})</span>
        </div>
        <StatusBadge value={a.decision} />
      </div>
      <div className="grid-main">
        <div className="stack">
          <Card title="Proposed action">
            <div className="card-body">
              <div className="row between">
                <div><div className="amount">{formatINR(p.amount_minor)}</div><div className="muted">Refund to original payment method · {p.currency}</div></div>
                <div className="stack" style={{ justifyItems: "end", gap: 6 }}>
                  {p.requires_senior_review && <Badge tone="violet">Senior reviewer required</Badge>}
                  <Badge tone={expired ? "warn" : "info"}>{expired ? "Expired" : "Expires"} {formatDate(a.expires_at)}</Badge>
                </div>
              </div>
              <dl className="kv">
                <dt>Customer</dt><dd>{p.customer_name} ({p.customer_id})</dd>
                <dt>Order</dt><dd>{p.order_id} · {(p.items ?? []).map((i: any) => i.name).join(", ")}</dd>
                <dt>Basis</dt><dd>{p.basis === "damage" ? "Damaged on arrival" : "Returned item"} · {label(p.decision_reason_code)}</dd>
                <dt>Reason</dt><dd>{p.reason}</dd>
                <dt>Policy</dt><dd>{p.policy_document_id} (v{p.policy_version})</dd>
                {p.damage_report && <><dt>Damage report</dt><dd>{p.damage_report.description} · photos: {String(p.damage_report.photos_available)}</dd></>}
                <dt>Payload hash</dt><dd className="mono small">{a.proposal.payload_hash.slice(0, 20)}…</dd>
              </dl>
            </div>
          </Card>
          <Card title="Complaint">
            <div className="card-body">
              <div>{a.ticket?.summary}</div>
              {a.investigation_summary && <div className="muted small">Investigation: {a.investigation_summary}</div>}
              {a.run_id && a.ticket && <a href={`#/tickets/${a.ticket.ticket_id}`} className="small">Open ticket and timeline →</a>}
            </div>
          </Card>
          <Card title="Decision">
            <div className="card-body">
              {a.decision === "pending" ? (
                <>
                  {blockers.map((b) => <div key={b} className="alert warn">{b}</div>)}
                  <label>Note (recorded in the audit trail)
                    <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional note" maxLength={2000} />
                  </label>
                  <ErrorAlert error={error} />
                  {confirming ? (
                    <div className="alert info row between">
                      <span>{confirming === "approve" ? `Approve a refund of ${formatINR(p.amount_minor)} for ${p.order_id}?` : "Reject this refund?"}</span>
                      <span className="row">
                        <button onClick={() => setConfirming(null)} disabled={busy}>Cancel</button>
                        <button className={confirming === "approve" ? "ok" : "danger"} disabled={busy} onClick={() => submit(confirming)}>
                          {busy && <span className="spinner" />} Confirm {confirming}
                        </button>
                      </span>
                    </div>
                  ) : (
                    <div className="row">
                      <button className="ok" disabled={!canApprove || busy} onClick={() => setConfirming("approve")}>Approve refund</button>
                      <button className="danger" disabled={a.decision !== "pending" || expired || busy} onClick={() => setConfirming("reject")}>Reject</button>
                    </div>
                  )}
                </>
              ) : (
                <dl className="kv">
                  <dt>Decision</dt><dd><StatusBadge value={a.decision} /></dd>
                  <dt>Reviewer</dt><dd>{a.reviewer?.name ?? "—"}</dd>
                  <dt>Decided</dt><dd>{formatDate(a.decided_at)}</dd>
                  {a.note && <><dt>Note</dt><dd>{a.note}</dd></>}
                </dl>
              )}
            </div>
          </Card>
        </div>
        <div className="stack">
          <Card title="Verified evidence"><div className="card-body"><OrderFacts facts={a.verified_facts} /></div></Card>
          <Card title="Policy citations"><div className="card-body"><Citations citations={a.policy_references} /></div></Card>
        </div>
      </div>
    </div>
  );
}
