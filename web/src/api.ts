const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:18100";

export type Incident = {
  fingerprint: string;
  alertname: string;
  service: string;
  status: "firing" | "resolved";
  starts_at: string | null;
  last_received_at: string;
  delivery_count: number;
};

export type TimelineEvent = {
  kind: "alert" | "deploy" | "backup";
  at: string;
  title: string;
  source_id: string;
};

export type AgentTrace = { node: string; duration_ms: number };
export type Claim = { text: string; source_ids: string[]; confidence?: string | null };
export type TriageState = {
  summary: string;
  hypotheses: Claim[];
  recommendations: Claim[];
  trace: AgentTrace[];
  followups?: Array<{ question: string; answer: string }>;
};

export type PendingAction = {
  id: string;
  kind: string;
  target: string;
  payload: Record<string, string>;
  status: string;
  created_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body: { detail?: string } = await response.json();
      if (body.detail) detail = body.detail;
    } catch {
      // Keep the HTTP status if a proxy returned non-JSON text.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  incidents: () => request<{ incidents: Incident[] }>("/api/incidents"),
  timeline: (fingerprint: string) =>
    request<{ events: TimelineEvent[] }>(`/api/incidents/${encodeURIComponent(fingerprint)}/timeline`),
  triage: (fingerprint: string) =>
    request<{ state: TriageState }>(`/api/triage/${encodeURIComponent(fingerprint)}`),
  pending: () => request<{ actions: PendingAction[] }>("/api/pending-actions"),
  followup: (fingerprint: string, question: string) =>
    request<{ answer: string }>(`/api/triage/${encodeURIComponent(fingerprint)}/followup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
  decide: (actionId: string, decision: "approve" | "reject", token: string) =>
    request<{ action: PendingAction }>(
      `/api/pending-actions/${encodeURIComponent(actionId)}/${decision}`,
      { method: "POST", headers: { "X-Approval-Token": token } },
    ),
};
