import { api, formatDate, formatINR, type Principal } from "../api/client";
import { Badge, Card, Empty, ErrorAlert, Loading, StatusBadge, label } from "../components/ui";
import { useLoader } from "../hooks";

function expiresIn(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  return h > 0 ? `${h}h ${m}m left` : `${m}m left`;
}

export function ApprovalQueue({ principal }: { principal: Principal }) {
  const pending = useLoader(() => api.approvals("pending"), [], true, 3000);
  const decided = useLoader(() => api.approvals("decided"), [], true, 6000);
  return (
    <div className="page">
      <div className="row between">
        <h1>Approval queue</h1>
        <span className="muted small">Signed in as {principal.name} ({label(principal.role)}, limit {formatINR(principal.approval_limit_minor)})</span>
      </div>
      <Card title="Pending approvals">
        {pending.loading ? <Loading /> : pending.error ? <div className="card-body"><ErrorAlert error={pending.error} /></div> :
          !pending.data?.length ? <Empty>No refunds are waiting for approval.</Empty> : (
            <div className="list">
              {pending.data.map((a) => {
                const p = a.proposal.payload;
                return (
                  <a key={a.approval_id} className="list-item" href={`#/approvals/${a.approval_id}`}>
                    <div className="row between">
                      <strong>{formatINR(p.amount_minor)} refund · {p.order_id}</strong>
                      <span className="row">
                        {p.requires_senior_review && <Badge tone="violet">senior review</Badge>}
                        {!a.can_approve && <Badge tone="warn">above your limit</Badge>}
                        <Badge tone="info">{expiresIn(a.expires_at)}</Badge>
                      </span>
                    </div>
                    <span className="muted small">{p.customer_name} ({p.customer_id}) · {a.ticket?.summary}</span>
                  </a>
                );
              })}
            </div>
          )}
      </Card>
      <Card title="Recently decided">
        {decided.loading ? <Loading /> : !decided.data?.length ? <Empty>No decisions yet.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Order</th><th>Amount</th><th>Decision</th><th>Reviewer</th><th>Decided</th></tr></thead>
            <tbody>{decided.data.slice(0, 15).map((a) => (
              <tr key={a.approval_id}>
                <td><a href={`#/approvals/${a.approval_id}`}>{a.proposal.payload.order_id}</a></td>
                <td>{formatINR(a.proposal.payload.amount_minor)}</td>
                <td><StatusBadge value={a.decision} /></td>
                <td>{a.reviewer?.name ?? "—"}</td>
                <td>{formatDate(a.decided_at)}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}

export function AllTickets() {
  const tickets = useLoader(() => api.tickets(), [], true, 4000);
  return (
    <div className="page">
      <h1>All tickets</h1>
      <Card>
        {tickets.loading ? <Loading /> : tickets.error ? <div className="card-body"><ErrorAlert error={tickets.error} /></div> :
          !tickets.data?.length ? <Empty>No tickets yet. Sign in as a customer to submit one.</Empty> : (
            <div className="list">
              {tickets.data.map((t) => (
                <a className="list-item" key={t.ticket_id} href={`#/tickets/${t.ticket_id}`}>
                  <div className="row between"><strong>{t.summary}</strong>
                    <span className="row"><StatusBadge value={t.status} />{t.latest_run_status && <StatusBadge value={t.latest_run_status} />}</span></div>
                  <span className="muted small">{t.customer_id} · {label(t.category)} · {t.order_id ?? "no order"} · {formatDate(t.created_at)}</span>
                </a>
              ))}
            </div>
          )}
      </Card>
    </div>
  );
}
