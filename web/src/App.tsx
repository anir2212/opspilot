import { useCallback, useEffect, useState } from "react";
import { api, type Incident, type PendingAction, type TimelineEvent, type TriageState } from "./api";

const time = (value: string | null) =>
  value ? new Date(value).toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" }) : "—";

export default function App() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [triage, setTriage] = useState<TriageState | null>(null);
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [question, setQuestion] = useState("");
  const [approvalToken, setApprovalToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [incidentsResult, pendingResult] = await Promise.all([api.incidents(), api.pending()]);
      setIncidents(incidentsResult.incidents);
      setPending(pendingResult.actions);
      setSelected((current) => current ?? incidentsResult.incidents[0]?.fingerprint ?? null);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load incidents");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    void Promise.allSettled([api.timeline(selected), api.triage(selected)]).then(([events, state]) => {
      if (cancelled) return;
      setTimeline(events.status === "fulfilled" ? events.value.events : []);
      setTriage(state.status === "fulfilled" ? state.value.state : null);
    });
    return () => { cancelled = true; };
  }, [selected, incidents]);

  const active = incidents.find((incident) => incident.fingerprint === selected);

  async function askQuestion(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !question.trim()) return;
    setBusy(true);
    try {
      await api.followup(selected, question.trim());
      setTriage((await api.triage(selected)).state);
      setQuestion("");
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Follow-up failed");
    } finally {
      setBusy(false);
    }
  }

  async function decide(actionId: string, decision: "approve" | "reject") {
    if (!approvalToken.trim()) {
      setError("Enter the local approval token before deciding an action.");
      return;
    }
    setBusy(true);
    try {
      await api.decide(actionId, decision, approvalToken);
      setApprovalToken("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Decision failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">◉</span><div><strong>OpsPilot</strong><small>Incident console</small></div></div>
        <div className="sidebar-heading"><span>INCIDENTS</span><button onClick={() => void refresh()}>Refresh</button></div>
        {incidents.length === 0 && <p className="empty-sidebar">No alerts received yet. Inject a local fault to start the demo.</p>}
        <div className="incident-list">
          {incidents.map((incident) => (
            <button key={incident.fingerprint} className={`incident-item ${selected === incident.fingerprint ? "selected" : ""}`} onClick={() => setSelected(incident.fingerprint)}>
              <span className={`status-dot ${incident.status}`} />
              <span className="incident-copy"><strong>{incident.alertname}</strong><small>{incident.service} · {time(incident.last_received_at)}</small></span>
              <span className="delivery-count">{incident.delivery_count}</span>
            </button>
          ))}
        </div>
        <div className="sidebar-foot">Local demo · synthetic faults</div>
      </aside>

      <main className="main">
        <header className="topbar"><div><span className="eyebrow">OPERATIONS / INCIDENTS</span><h1>{active?.alertname ?? "Waiting for an alert"}</h1></div><span className={`status-pill ${active?.status ?? "idle"}`}>{active?.status ?? "idle"}</span></header>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!active ? <div className="welcome"><div className="welcome-icon">◎</div><h2>Ready for the next signal</h2><p>Start the local stack and inject a fault. New alerts appear here automatically.</p></div> : (
          <>
            <section className="stats"><div><span>Service</span><strong>{active.service}</strong></div><div><span>First seen</span><strong>{time(active.starts_at ?? active.last_received_at)}</strong></div><div><span>Webhook deliveries</span><strong>{active.delivery_count}</strong></div><div><span>Thread</span><strong className="mono">{active.fingerprint.slice(0, 12)}</strong></div></section>
            <div className="columns">
              <div className="primary">
                <section className="card"><div className="section-title"><span>01</span><h2>Agent summary</h2></div>{triage ? <div className="summary">{triage.summary}</div> : <p className="muted">Triage has not completed for this alert.</p>}</section>
                <section className="card"><div className="section-title"><span>02</span><h2>Timeline</h2></div>{timeline.length ? <ol className="timeline">{timeline.map((event, index) => <li key={`${event.source_id}-${index}`}><div className="timeline-line"><strong>{event.title}</strong><time>{time(event.at)}</time></div><small>{event.kind.toUpperCase()} · {event.source_id}</small></li>)}</ol> : <p className="muted">No correlated timeline events recorded.</p>}</section>
                <section className="card"><div className="section-title"><span>03</span><h2>Follow-up</h2></div><form className="question-form" onSubmit={(event) => void askQuestion(event)}><input aria-label="Question about incident" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask why this runbook was suggested…" /><button disabled={busy || !triage}>Ask</button></form>{triage?.followups?.map((entry, index) => <div className="followup" key={index}><strong>{entry.question}</strong><p>{entry.answer}</p></div>)}</section>
              </div>
              <div className="secondary">
                <section className="card"><div className="section-title"><span>04</span><h2>Agent trace</h2></div>{triage?.trace?.length ? triage.trace.map((step, index) => <div className="trace-row" key={`${step.node}-${index}`}><span>{step.node.replaceAll("_", " ")}</span><strong>{step.duration_ms.toFixed(1)} ms</strong></div>) : <p className="muted">No trace available.</p>}</section>
                <section className="card"><div className="section-title"><span>05</span><h2>Pending approvals</h2></div>{pending.length === 0 ? <p className="muted">No actions waiting for a decision.</p> : <><label className="token-label" htmlFor="approval-token">Local approval token</label><input id="approval-token" type="password" value={approvalToken} onChange={(event) => setApprovalToken(event.target.value)} placeholder="Required for decisions" />{pending.map((action) => <div className="approval" key={action.id}><strong>{action.kind.replaceAll("_", " ")}</strong><small>{action.target}</small><p>{action.payload.text ?? action.payload.short_description}</p><div className="approval-buttons"><button disabled={busy} onClick={() => void decide(action.id, "reject")}>Reject</button><button disabled={busy} className="approve" onClick={() => void decide(action.id, "approve")}>Approve</button></div></div>)}</>}</section>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
