import { useState } from "react";
import { api, formatDate, formatINR } from "../api/client";
import { Card, Empty, ErrorAlert, Loading, StatusBadge } from "../components/ui";
import { useLoader } from "../hooks";

const PRESETS: { name: string; description: string; rule: Record<string, unknown> }[] = [
  { name: "Payment timeout after acceptance", description: "Provider commits the refund, then the call times out. The workflow must reconcile by idempotency key, not refund twice.",
    rule: { target: "payments.create_refund", mode: "commit_then_timeout", delay_ms: 5000, count: 1 } },
  { name: "Payment 503 before acceptance", description: "Provider rejects the first attempt without acting. Lookup finds nothing; the request is re-sent with the same key.",
    rule: { target: "payments.create_refund", mode: "error", status_code: 503, count: 1 } },
  { name: "Slow settlement", description: "Refund accepted as processing; settles after several status checks.",
    rule: { target: "payments.create_refund", mode: "processing", settle_after: 3, count: 1 } },
  { name: "Shipping API flaky (2 failures)", description: "Tracking lookups fail twice, then succeed (bounded retry with backoff).",
    rule: { target: "shipping.get_shipment", mode: "error", status_code: 503, count: 2 } },
  { name: "Shipping API down", description: "Tracking unavailable: the workflow escalates and says so honestly.",
    rule: { target: "shipping.get_shipment", mode: "error", status_code: 503, count: -1 } },
  { name: "Ticketing fails twice", description: "Only the ticket update is retried; the refund is not repeated.",
    rule: { target: "ticketing.add_note", mode: "error", status_code: 503, count: 2 } },
];

export function Ops() {
  const faults = useLoader(() => api.faults(), [], true, 3000);
  const refunds = useLoader(() => api.providerRefunds(), [], true, 3000);
  const [error, setError] = useState<unknown>(null);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await faults.refresh();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="page">
      <h1>Demo operations</h1>
      <p className="muted" style={{ margin: 0 }}>Inject failures into the mock payment, shipping and ticketing services, then run a customer scenario to watch recovery.</p>
      <ErrorAlert error={error} />
      <div className="grid-2">
        <Card title="Inject a fault">
          <div className="list">
            {PRESETS.map((p) => (
              <div key={p.name} className="list-item">
                <div className="row between"><strong>{p.name}</strong><button onClick={() => run(() => api.addFault(p.rule))}>Inject</button></div>
                <span className="muted small">{p.description}</span>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Active faults" actions={<button className="danger" onClick={() => run(() => api.clearFaults())}>Clear all</button>}>
          {faults.loading ? <Loading /> : faults.error ? <div className="card-body"><ErrorAlert error={faults.error} /></div> :
            !faults.data?.rules.length ? <Empty>No active faults.</Empty> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Target</th><th>Mode</th><th>Remaining</th></tr></thead>
                <tbody>{faults.data.rules.map((r, i) => (
                  <tr key={i}><td className="mono">{r.target}</td><td>{r.mode}</td><td>{r.count === -1 ? "until cleared" : r.count}</td></tr>))}</tbody>
              </table></div>
            )}
        </Card>
      </div>
      <Card title="Refunds recorded by the mock payment provider">
        {refunds.loading ? <Loading /> : !refunds.data?.refunds.length ? <Empty>No refunds.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Provider ref</th><th>Order</th><th>Amount</th><th>Status</th><th>Idempotency key</th><th>Created</th></tr></thead>
            <tbody>{refunds.data.refunds.map((r) => (
              <tr key={r.id}><td className="mono">{r.id}</td><td>{r.order_id}</td><td>{formatINR(r.amount_minor)}</td>
                <td><StatusBadge value={r.status} /></td><td className="mono small">{r.idempotency_key}</td><td>{formatDate(r.created_at)}</td></tr>))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
