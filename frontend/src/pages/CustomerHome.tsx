import { useState } from "react";
import { api, formatDate } from "../api/client";
import { Card, Empty, ErrorAlert, Loading, StatusBadge, label } from "../components/ui";
import { useLoader } from "../hooks";

const EXAMPLES: Record<string, string[]> = {
  "C-1001": ["I returned my headphones last week, but I still haven't received my refund.", "Where is my cable order ORD-5002?"],
  "C-1002": ["My earbuds arrived damaged.", "I returned my smartwatch ORD-5003, where's my refund?"],
  "C-1003": ["I returned my phone ORD-5005 and want my refund.", "When will my charger ORD-5006 ship?"],
  "C-1004": ["I sent back the speaker ORD-5007 days ago, where's my money?", "Where is my laptop ORD-5031?"],
  "C-1005": ["I returned my laptop ORD-5009, any update on the refund?", "My monitor ORD-5010 still hasn't arrived."],
  "C-1006": ["I returned my tablet ORD-5011, please refund me.", "I returned the mouse ORD-5012 weeks ago, refund please."],
  "C-1007": ["The speaker from ORD-5014 arrived with a cracked case. I have photos. I'd like a refund.", "I returned ORD-5013 (earbuds and cable). Refund?"],
  "C-1008": ["Why was my smartwatch return ORD-5015 refused? I want my refund.", "My headphones ORD-5016 arrived cracked, I have photos."],
  "C-1009": ["Where is my order?", "I returned my charger, where is the refund?"],
  "C-1010": ["Where is the refund for my headphones ORD-5020?", "I returned the cable from ORD-5021, where's my refund?"],
  "C-1011": ["I returned my keyboard ORD-5022 but never got the refund.", "My monitor return ORD-5023 — refund?"],
  "C-1012": ["Where is my tablet ORD-5025?", "I want a refund for my earbuds ORD-5024."],
};

export function CustomerHome({ customerId, navigate }: { customerId: string; navigate: (p: string) => void }) {
  const orders = useLoader(() => api.orders(), [customerId]);
  const tickets = useLoader(() => api.tickets(), [customerId], true, 4000);
  const [message, setMessage] = useState("");
  const [orderId, setOrderId] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const invalid = message.trim().length < 3;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (invalid) return;
    setBusy(true);
    setError(null);
    try {
      const t = await api.createTicket(message.trim(), orderId || undefined);
      await api.startRun(t.ticket_id);
      navigate(`/tickets/${t.ticket_id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="grid-main">
        <Card title="How can we help?">
          <form className="card-body" onSubmit={submit} noValidate>
            <label>
              Describe the problem
              <textarea value={message} onChange={(e) => setMessage(e.target.value)} onBlur={() => setTouched(true)}
                        placeholder="e.g. I returned my headphones last week but haven't received my refund."
                        maxLength={4000} aria-invalid={touched && invalid} />
              {touched && invalid && <span className="field-error">Please describe the problem (at least 3 characters).</span>}
            </label>
            <label>
              Order (optional)
              <select value={orderId} onChange={(e) => setOrderId(e.target.value)}>
                <option value="">Let the assistant work it out</option>
                {orders.data?.map((o) => (
                  <option key={o.order_id} value={o.order_id}>{o.order_id} — {o.items.join(", ")} ({o.status})</option>
                ))}
              </select>
            </label>
            {EXAMPLES[customerId] && (
              <div className="row">
                <span className="muted small">Try:</span>
                {EXAMPLES[customerId].map((ex) => (
                  <button type="button" className="chip" key={ex} onClick={() => setMessage(ex)}>{ex}</button>
                ))}
              </div>
            )}
            <ErrorAlert error={error} />
            <div className="row"><button className="primary" disabled={busy}>{busy ? <span className="spinner" /> : null} Submit complaint</button></div>
          </form>
        </Card>
        <Card title="Your orders">
          {orders.loading ? <Loading /> : orders.error ? <div className="card-body"><ErrorAlert error={orders.error} /></div> : (
            <div className="list">
              {orders.data?.map((o) => (
                <div className="list-item" key={o.order_id}>
                  <div className="row between"><strong>{o.order_id}</strong><StatusBadge value={o.status} /></div>
                  <span className="muted small">{o.items.join(", ")} · placed {formatDate(o.placed_at)}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <Card title="Your tickets">
        {tickets.loading ? <Loading /> : tickets.error ? <div className="card-body"><ErrorAlert error={tickets.error} /></div> :
          !tickets.data?.length ? <Empty>No tickets yet. Submit a complaint above to start.</Empty> : (
            <div className="list">
              {tickets.data.map((t) => (
                <a className="list-item" key={t.ticket_id} href={`#/tickets/${t.ticket_id}`}>
                  <div className="row between">
                    <strong>{t.summary}</strong>
                    <span className="row"><StatusBadge value={t.latest_run_status ?? t.status} /></span>
                  </div>
                  <span className="muted small">{label(t.category)} · {t.order_id ?? "order not identified yet"} · {formatDate(t.created_at)}</span>
                </a>
              ))}
            </div>
          )}
      </Card>
    </div>
  );
}
