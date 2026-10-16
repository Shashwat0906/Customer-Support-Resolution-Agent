import { useState } from "react";
import { api, formatINR, type Principal, setSession } from "../api/client";
import { Card, ErrorAlert, Loading } from "../components/ui";
import { useLoader } from "../hooks";

export const SCENARIOS: Record<string, string> = {
  "C-1001": "Returned headphones, refund not received (eligible)",
  "C-1002": "Earbuds arrived damaged · failed inspection",
  "C-1003": "High-value phone refund (senior review)",
  "C-1004": "Return still in transit · already refunded",
  "C-1005": "Inspection pending · delayed monitor",
  "C-1006": "Tablet refund · outside return window",
  "C-1007": "Damaged speaker · multi-item refund",
  "C-1008": "Rejected return · late damage report",
  "C-1009": "Several orders (asks which one)",
  "C-1010": "Refund already processing · v1 policy order",
  "C-1011": "Order predates every policy · needs review",
  "C-1012": "Carrier exception on tablet · no return started",
};

export function Login({ onLogin }: { onLogin: (p: Principal) => void }) {
  const users = useLoader(() => api.demoUsers(), []);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function login(subject: string) {
    setBusy(subject);
    setError(null);
    try {
      const r = await api.login(subject);
      setSession(r.access_token, r.principal);
      onLogin(r.principal);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page">
      <div className="stack">
        <h1>Customer Support Resolution Agent</h1>
        <p className="muted" style={{ maxWidth: 760, margin: 0 }}>
          A demo of an agentic support workflow for VoltCart, a fictional electronics retailer. Agents investigate and
          propose; the application checks eligibility, a human approves every refund, and only verified outcomes are
          reported. All data is synthetic and payments go to a mock provider.
        </p>
      </div>
      <ErrorAlert error={error ?? users.error} />
      <Card title="Sign in as a demo customer">
        {users.loading ? <Loading /> : (
          <div className="card-body">
            <div className="persona-grid">
              {users.data?.customers.map((c) => (
                <button key={c.id} className="persona" disabled={!!busy} onClick={() => login(c.subject)}>
                  <strong>{c.name}</strong>
                  <span className="muted small">{c.id} · {SCENARIOS[c.id] ?? "Synthetic customer"}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </Card>
      <Card title="Sign in as a support reviewer">
        {users.loading ? <Loading lines={1} /> : (
          <div className="card-body">
            <div className="persona-grid">
              {users.data?.reviewers.map((r) => (
                <button key={r.id} className="persona" disabled={!!busy} onClick={() => login(r.subject)}>
                  <strong>{r.name}</strong>
                  <span className="muted small">{r.role.replace("_", " ")} · approves up to {formatINR(r.approval_limit_minor)}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
