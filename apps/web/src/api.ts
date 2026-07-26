export type Incident = {
  id: string;
  title: string;
  severity: string;
  service: string;
  environment: string;
  status: string;
  description: string;
  created_at: string;
  updated_at: string;
  resolved_at?: string;
  current_run_id?: string;
  evidence_count?: number;
  pending_approvals?: number;
  token_usage?: number;
  tool_calls?: number;
  events?: IncidentEvent[];
  evidence?: Evidence[];
  hypotheses?: Hypothesis[];
  runs?: AgentRun[];
  tool_intents?: ToolIntent[];
};

export type IncidentEvent = {
  id: string;
  sequence: number;
  event_type: string;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type Evidence = {
  id: string;
  source_type: string;
  source_uri: string;
  title: string;
  content: string;
  observed_at: string;
  metadata: Record<string, unknown>;
};

export type Hypothesis = {
  id: string;
  cause: string;
  confidence: number;
  evidence_ids: string[];
};

export type AgentRun = {
  id: string;
  status: string;
  token_usage: number;
  tool_calls: number;
  prompt_version: string;
  started_at: string;
};

export type ToolIntent = {
  id: string;
  run_id: string;
  incident_id: string;
  incident_title?: string;
  severity?: string;
  agent_name: string;
  tool_name: string;
  environment: string;
  resource: Record<string, unknown>;
  arguments: Record<string, unknown>;
  rollback: Record<string, unknown>;
  risk_level: string;
  idempotency_key: string;
  status: string;
  decision?: string;
  policy_decision?: string;
  matched_rule?: string;
  reason?: string;
  input_snapshot?: Record<string, unknown>;
  created_at: string;
};

export type TraceSpan = {
  id: string;
  run_id: string;
  incident_id: string;
  incident_title: string;
  span_type: string;
  name: string;
  agent?: string;
  status: string;
  duration_ms: number;
  attributes: Record<string, unknown>;
  created_at: string;
};

export type Overview = {
  incidents: number;
  active: number;
  approvals: number;
  spans: number;
  tokens: number;
  automation_rate: number;
  mttr_minutes: number;
  policy_block_rate: number;
  by_status: Record<string, number>;
  by_severity: Record<string, number>;
  recent_incidents: Incident[];
};

export type SystemHealth = {
  status: string;
  version: string;
  execution_mode: string;
  connector_mode: string;
  agent_backend: string;
  policy_backend: string;
  database: string;
  database_backend: string;
  database_pool?: Record<string, number> | null;
  redis_stream: string;
  opentelemetry: string;
  authentication: string;
  workflow_checkpoints: string;
  frontend: string;
};

export type Identity = {
  subject: string;
  roles: string[];
  auth_mode: string;
};

export type EvalRun = {
  id: string;
  suite: string;
  model: string;
  prompt_version: string;
  status: string;
  metrics: Record<string, number | string | boolean>;
  cases: Array<Record<string, string | number | boolean>>;
  created_at: string;
};

export type Postmortem = {
  id: string;
  incident_id: string;
  run_id?: string;
  status: string;
  title: string;
  summary: string;
  impact: string;
  root_cause: string;
  contributing_factors: string[];
  timeline: Array<{ at: string; event: string; actor: string; detail: string }>;
  remediation: string[];
  action_items: Array<{
    title: string;
    owner: string;
    priority: string;
    due_date?: string;
    status: string;
  }>;
  lessons: string[];
  generated_by: string;
  updated_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const accessToken = sessionStorage.getItem("runguard-access-token");
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  hasAccessToken: () => Boolean(sessionStorage.getItem("runguard-access-token")),
  setAccessToken: (token: string) =>
    sessionStorage.setItem("runguard-access-token", token.trim()),
  clearAccessToken: () => sessionStorage.removeItem("runguard-access-token"),
  health: () => request<SystemHealth>("/api/health"),
  identity: () => request<Identity>("/api/auth/me"),
  overview: () => request<Overview>("/api/overview"),
  incidents: () => request<Incident[]>("/api/incidents"),
  incident: (id: string) => request<Incident>(`/api/incidents/${id}`),
  createIncident: (payload: Record<string, string>) =>
    request<Incident>("/api/incidents", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  startIncident: (id: string) =>
    request<Incident>(`/api/incidents/${id}/start`, { method: "POST" }),
  replayIncident: (id: string) =>
    request<Record<string, unknown>>(`/api/incidents/${id}/replay`, { method: "POST" }),
  approvals: () => request<ToolIntent[]>("/api/approvals"),
  approve: (id: string, comment: string) =>
    request<Incident>(`/api/tool-intents/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ reviewer: "SRE Operator", comment }),
    }),
  reject: (id: string, comment: string) =>
    request<ToolIntent>(`/api/tool-intents/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reviewer: "SRE Operator", comment }),
    }),
  traces: () => request<TraceSpan[]>("/api/traces"),
  simulatePolicy: (payload: Record<string, unknown>) =>
    request<Record<string, string>>("/api/policies/simulate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  evaluations: () => request<EvalRun[]>("/api/evals"),
  runEvaluation: () =>
    request<EvalRun>("/api/evals/run", {
      method: "POST",
      body: JSON.stringify({
        suite: "baseline-12",
        model: "deterministic-demo",
        prompt_version: "1.2.0",
      }),
    }),
  postmortem: (incidentId: string) =>
    request<Postmortem>(`/api/incidents/${incidentId}/postmortem`),
  generatePostmortem: (incidentId: string) =>
    request<Postmortem>(`/api/incidents/${incidentId}/postmortem`, { method: "POST" }),
};
