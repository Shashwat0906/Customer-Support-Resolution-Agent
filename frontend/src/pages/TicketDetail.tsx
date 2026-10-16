import { useState } from "react";
import { api, formatDate, formatINR, type Principal, TERMINAL, WAITING } from "../api/client";
import { Citations, DecisionPanel, OrderFacts } from "../components/Evidence";
import { Timeline } from "../components/Timeline";
import { Badge, Card, ErrorAlert, Loading, StatusBadge, label } from "../components/ui";
import { useLoader } from "../hooks";

const OUTCOME_COPY: Record<string, { tone: "ok" | "warn" | "danger" | "info"; text: string }> = {
  refund_succeeded: { tone: "ok", text: "Refund confirmed by the payment provider." },
  refund_processing: { tone: "warn", text: "Refund submitted; provider confirmation still pending." },
  refund_rejected: { tone: "danger", text: "Refund rejected by a reviewer. No money moved." },
  approval_expired: { tone: "warn", text: "Approval expired before a decision; a new approval is required." },
  escalated: { tone: "danger", text: "Escalated to a specialist." },
  answered: { tone: "ok", text: "Answered with verified information." },
};

export function TicketDetail({ ticketId, principal }: { ticketId: string; principal: Principal }) {
  const ticket = useLoader(() => api.ticket(ticketId), [ticketId]);
  const runId = ticket.data?.latest_run_id;
  const [polling, setPolling] = useState(true);
  const run = useLoader(async () => {
    if (!runId) return null;
    const r = await api.run(runId);
    setPolling(!TERMINAL.has(r.status) && !WAITING.has(r.status));
    if (TERMINAL.has(r.status) || WAITING.has(r.status)) void ticket.refresh();
    return r;
  }, [runId], polling, 1200);
  const events = useLoader(() => (runId ? api.events(runId) : Promise.resolve([])), [runId, run.data?.status],
    polling, 1500);
  const [reply, setReply] = useState("");
  const [replyError, setReplyError] = useState<unknown>(null);
  const [sending, setSending] = useState(false);

  if (ticket.loading && !ticket.data) return <div className="page"><Card><Loading lines={5} /></Card></div>;
  if (ticket.error) return <div className="page"><ErrorAlert error={ticket.error} /></div>;
  const t = ticket.data!;
  const r = run.data;
  const s = r?.state ?? {};
  const reviewer = principal.kind === "staff";
  const outcome = r?.outcome ? OUTCOME_COPY[r.outcome] : null;

  async function sendReply(e: React.FormEvent) {
    e.preventDefault();
    if (!runId || !reply.trim()) return;
    setSending(true);
    setReplyError(null);
    try {
      await api.reply(runId, reply.trim());
      setReply("");
      setPolling(true);
      await Promise.all([ticket.refresh(), run.refresh()]);
    } catch (err) {
      setReplyError(err);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="page">
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <a href={reviewer ? "#/tickets" : "#/"} className="small">← Back</a>
          <h1>{t.summary}</h1>
          <span className="muted small">
            {t.ticket_id} · {label(t.category)} · {t.order_id ?? "order not identified"} · opened {formatDate(t.created_at)}
          </span>
        </div>
        <div className="row">
          <StatusBadge value={t.status} />
          {r && r.status !== t.status && <StatusBadge value={r.status} />}
          {r && polling && <span className="spinner" aria-label="Working" />}
        </div>
      </div>

      {r?.status === "awaiting_approval" && (
        <div className="alert info">A refund proposal is waiting for a support reviewer. Nothing is refunded until a reviewer approves it.
          {reviewer && r.pending_approval_id && <> <a href={`#/approvals/${r.pending_approval_id}`}>Review it →</a></>}</div>
      )}
      {outcome && <div className={`alert ${outcome.tone}`}><strong>{label(r!.outcome)}.</strong> {outcome.text}
        {s.escalation_reason && reviewer && <span className="mono"> ({s.escalation_reason})</span>}</div>}
      {r?.status === "failed" && <div className="alert danger">The workflow failed ({r.error_code}). The ticket has been escalated.</div>}
      <ErrorAlert error={run.error} />

      <div className="grid-main">
        <div className="stack">
          <Card title="Conversation">
            <div className="card-body">
              <div className="chat">
                {t.messages.map((m, i) => (
                  <div key={i} className={`bubble ${m.author_type === "customer" ? "customer" : "agent"}`}>
                    {m.body}
                    <span className="meta">{m.author_type === "customer" ? "You" : "VoltCart Support"} · {formatDate(m.created_at)}</span>
                  </div>
                ))}
                {r && !TERMINAL.has(r.status) && !WAITING.has(r.status) && (
                  <div className="bubble agent muted"><span className="spinner" /> Investigating…</div>
                )}
              </div>
              {r?.status === "awaiting_customer" && principal.kind === "customer" && (
                <form className="stack" onSubmit={sendReply}>
                  <label>Your reply
                    <textarea value={reply} onChange={(e) => setReply(e.target.value)} placeholder={s.clarification_question ?? ""} />
                  </label>
                  <ErrorAlert error={replyError} />
                  <div className="row"><button className="primary" disabled={sending || !reply.trim()}>Send</button></div>
                </form>
              )}
            </div>
          </Card>
          <Card title="Workflow timeline" actions={r && <span className="muted small">{r.agent_mode} agents</span>}>
            <div className="card-body">
              {events.loading && !events.data ? <Loading /> : <Timeline events={events.data ?? []} />}
            </div>
          </Card>
        </div>
        <div className="stack">
          <Card title="Verified evidence"><div className="card-body"><OrderFacts facts={s.verified_facts} /></div></Card>
          <Card title="Decision"><div className="card-body"><DecisionPanel decision={s.decision} state={s} /></div></Card>
          <Card title="Policy citations"><div className="card-body">
            {s.policy_finding?.interpretation && <div className="muted small">{s.policy_finding.interpretation}</div>}
            <Citations citations={s.policy_references} />
          </div></Card>
          {s.external_result_reference && (
            <Card title="External result"><div className="card-body">
              <dl className="kv"><dt>Provider reference</dt><dd className="mono">{s.external_result_reference}</dd>
                <dt>Status</dt><dd><StatusBadge value={s.execution_status} /></dd></dl>
            </div></Card>
          )}
          {reviewer && r?.metrics && (
            <Card title="Run metrics"><div className="card-body">
              <div className="stat-row">
                <div className="stat"><div className="v">{r.metrics.model_calls ?? 0}</div><div className="l">model calls</div></div>
                <div className="stat"><div className="v">{r.metrics.tool_calls ?? 0}</div><div className="l">tool calls</div></div>
                <div className="stat"><div className="v">{r.metrics.retries ?? 0}</div><div className="l">retries</div></div>
                <div className="stat"><div className="v">{Math.round(r.metrics.active_ms ?? 0)} ms</div><div className="l">active time</div></div>
                <div className="stat"><div className="v">{Math.round((r.metrics.waiting_approval_ms ?? 0) / 1000)} s</div><div className="l">waiting for reviewer</div></div>
                <div className="stat"><div className="v">${(r.metrics.estimated_cost_usd ?? 0).toFixed(4)}</div><div className="l">est. model cost</div></div>
              </div>
              {(s.unauthorized_tool_attempts ?? 0) > 0 && <Badge tone="danger">{s.unauthorized_tool_attempts} refused tool call(s)</Badge>}
              {(s.proposal_corrections ?? 0) > 0 && <Badge tone="warn">model proposal corrected</Badge>}
              {!!s.error_details?.length && (
                <div className="stack small">{s.error_details.map((e, i) => <div key={i} className="alert warn"><strong>{e.code}</strong> {e.message}</div>)}</div>
              )}
              {s.decision?.amount_minor ? <div className="muted small">Permitted refund: {formatINR(s.decision.amount_minor)}</div> : null}
            </div></Card>
          )}
        </div>
      </div>
    </div>
  );
}
