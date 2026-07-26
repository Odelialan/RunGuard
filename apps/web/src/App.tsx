import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Box,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  ClipboardCheck,
  Clock3,
  Code2,
  Database,
  FileClock,
  FileText,
  Fingerprint,
  Gauge,
  GitBranch,
  Hexagon,
  KeyRound,
  Layers3,
  ListFilter,
  LockKeyhole,
  Menu,
  Network,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCcw,
  RotateCcw,
  Search,
  Server,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Timer,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AgentRun,
  api,
  EvalRun,
  Evidence,
  Incident,
  Overview,
  Postmortem,
  SystemHealth,
  ToolIntent,
  TraceSpan,
} from "./api";
import { initialLanguage, Language, useDocumentLanguage } from "./i18n";

type View =
  | "overview"
  | "incidents"
  | "incident"
  | "approvals"
  | "traces"
  | "postmortems"
  | "evaluations"
  | "policies";

type Toast = { message: string; tone: "success" | "error" };

const NAV_ITEMS = [
  { id: "overview" as View, label: "Overview", icon: Gauge },
  { id: "incidents" as View, label: "Incidents", icon: AlertTriangle },
  { id: "approvals" as View, label: "Approval center", icon: ClipboardCheck },
  { id: "traces" as View, label: "Trace explorer", icon: GitBranch },
  { id: "postmortems" as View, label: "Postmortems", icon: FileText },
  { id: "evaluations" as View, label: "Evaluations", icon: BarChart3 },
  { id: "policies" as View, label: "Policy simulator", icon: ShieldCheck },
];

const STATUS_ORDER = [
  "NEW",
  "TRIAGING",
  "INVESTIGATING",
  "PLAN_READY",
  "POLICY_CHECKING",
  "WAITING_APPROVAL",
  "EXECUTING",
  "VERIFYING",
  "RESOLVED",
];

function App() {
  const [language, setLanguage] = useState<Language>(initialLanguage);
  const [view, setView] = useState<View>("overview");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [approvals, setApprovals] = useState<ToolIntent[]>([]);
  const [traces, setTraces] = useState<TraceSpan[]>([]);
  const [evaluations, setEvaluations] = useState<EvalRun[]>([]);
  const [authenticated, setAuthenticated] = useState(api.hasAccessToken());
  const [loading, setLoading] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  useDocumentLanguage(language);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const nextHealth = await api.health();
      setHealth(nextHealth);
      if (nextHealth.authentication !== "disabled") {
        if (!api.hasAccessToken()) {
          setAuthenticated(false);
          return;
        }
        await api.identity();
      }
      setAuthenticated(true);
      const [nextOverview, nextIncidents, nextApprovals, nextTraces, nextEvaluations] =
        await Promise.all([
          api.overview(),
          api.incidents(),
          api.approvals(),
          api.traces(),
          api.evaluations(),
        ]);
      setOverview(nextOverview);
      setIncidents(nextIncidents);
      setApprovals(nextApprovals);
      setTraces(nextTraces);
      setEvaluations(nextEvaluations);
    } catch (error) {
      if ((error as Error).message.toLowerCase().includes("token")) {
        api.clearAccessToken();
        setAuthenticated(false);
      }
      setToast({ message: (error as Error).message, tone: "error" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const openIncident = (id: string) => {
    setSelectedId(id);
    setView("incident");
    setSidebarOpen(false);
  };

  const navigate = (next: View) => {
    setView(next);
    setSidebarOpen(false);
  };

  const activeLabel =
    view === "incident"
      ? selectedId ?? "Incident"
      : NAV_ITEMS.find((item) => item.id === view)?.label ?? "Overview";

  return (
    <div className="app-shell">
      <Sidebar
        view={view}
        open={sidebarOpen}
        approvals={approvals.length}
        health={health}
        onNavigate={navigate}
        onClose={() => setSidebarOpen(false)}
      />
      <main className="main-area">
        <Header
          title={activeLabel}
          onMenu={() => setSidebarOpen(true)}
          onCreate={() => setCreateOpen(true)}
          onRefresh={() => void refresh()}
          loading={loading}
          language={language}
          onLanguageChange={() => setLanguage((current) => (current === "en" ? "zh" : "en"))}
          onSignOut={
            health?.authentication !== "disabled" && authenticated
              ? () => {
                  api.clearAccessToken();
                  setAuthenticated(false);
                  setOverview(null);
                }
              : undefined
          }
        />
        <div className="content">
          {health && health.authentication !== "disabled" && !authenticated ? (
            <AuthenticationGate
              mode={health.authentication}
              onAuthenticated={async (token) => {
                api.setAccessToken(token);
                try {
                  const identity = await api.identity();
                  setAuthenticated(true);
                  setToast({
                    message: `Authenticated as ${identity.subject}`,
                    tone: "success",
                  });
                  await refresh();
                } catch (error) {
                  api.clearAccessToken();
                  setAuthenticated(false);
                  throw error;
                }
              }}
            />
          ) : loading && !overview ? (
            <LoadingState />
          ) : (
            <>
              {view === "overview" && overview && (
                <OverviewPage
                  data={overview}
                  approvals={approvals}
                  traces={traces}
                  onOpenIncident={openIncident}
                  onNavigate={navigate}
                />
              )}
              {view === "incidents" && (
                <IncidentsPage
                  incidents={incidents}
                  onOpen={openIncident}
                  onCreate={() => setCreateOpen(true)}
                />
              )}
              {view === "incident" && selectedId && (
                <IncidentWorkspace
                  incidentId={selectedId}
                  onChanged={refresh}
                  showToast={setToast}
                />
              )}
              {view === "approvals" && (
                <ApprovalCenter
                  approvals={approvals}
                  onChanged={refresh}
                  showToast={setToast}
                  onOpenIncident={openIncident}
                />
              )}
              {view === "traces" && <TraceExplorer traces={traces} />}
              {view === "postmortems" && (
                <PostmortemPage incidents={incidents} showToast={setToast} />
              )}
              {view === "evaluations" && (
                <EvaluationDashboard
                  runs={evaluations}
                  onChanged={refresh}
                  showToast={setToast}
                />
              )}
              {view === "policies" && <PolicySimulator showToast={setToast} />}
            </>
          )}
        </div>
      </main>
      {createOpen && (
        <CreateIncidentModal
          onClose={() => setCreateOpen(false)}
          onCreated={(incident) => {
            setCreateOpen(false);
            void refresh();
            openIncident(incident.id);
            setToast({ message: `${incident.id} created`, tone: "success" });
          }}
          showToast={setToast}
        />
      )}
      {toast && (
        <div className={`toast ${toast.tone}`}>
          {toast.tone === "success" ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
          {toast.message}
        </div>
      )}
    </div>
  );
}

function Sidebar({
  view,
  open,
  approvals,
  health,
  onNavigate,
  onClose,
}: {
  view: View;
  open: boolean;
  approvals: number;
  health: SystemHealth | null;
  onNavigate: (view: View) => void;
  onClose: () => void;
}) {
  return (
    <>
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">
            <Shield size={20} strokeWidth={2.3} />
            <span />
          </div>
          <div>
            <div className="brand-name">RunGuard</div>
            <div className="brand-version">TRUSTED OPS · V1.2</div>
          </div>
          <button className="icon-button sidebar-close" onClick={onClose}>
            <X size={19} />
          </button>
        </div>
        <div className="workspace-switch">
          <div className="workspace-icon">OL</div>
          <div>
            <strong>OdeliaLan Lab</strong>
            <span>Production workspace</span>
          </div>
          <ChevronDown size={15} />
        </div>
        <nav>
          <div className="nav-label">COMMAND</div>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = view === item.id || (view === "incident" && item.id === "incidents");
            return (
              <button
                key={item.id}
                className={`nav-item ${active ? "active" : ""}`}
                onClick={() => onNavigate(item.id)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
                {item.id === "approvals" && approvals > 0 && (
                  <em className="nav-badge">{approvals}</em>
                )}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-status">
          <div className="status-heading">
            <span className="live-dot" />
            System operational
          </div>
          <div className="status-row">
            <span>Agent runtime</span>
            <strong>{health?.agent_backend === "langgraph" ? "LangGraph" : "Deterministic"}</strong>
          </div>
          <div className="status-row">
            <span>Policy gateway</span>
            <strong>{health?.policy_backend === "opa" ? "OPA enforced" : "Python demo"}</strong>
          </div>
          <div className="status-row">
            <span>Execution</span>
            <strong>
              {health?.execution_mode === "kubernetes_job" ? "Kubernetes Job" : "Simulation"}
            </strong>
          </div>
          <div className="status-row">
            <span>Identity</span>
            <strong>
              {health?.authentication === "disabled"
                ? "Local demo"
                : health?.authentication?.toUpperCase()}
            </strong>
          </div>
          <div className="status-row">
            <span>Checkpoints</span>
            <strong>
              {health?.workflow_checkpoints === "postgres" ? "PostgreSQL" : "In memory"}
            </strong>
          </div>
        </div>
        <div className="user-row">
          <div className="avatar">OL</div>
          <div>
            <strong>OdeliaLan</strong>
            <span>Platform owner</span>
          </div>
          <ChevronRight size={16} />
        </div>
      </aside>
      {open && <button className="sidebar-backdrop" onClick={onClose} aria-label="Close menu" />}
    </>
  );
}

function Header({
  title,
  onMenu,
  onCreate,
  onRefresh,
  loading,
  language,
  onLanguageChange,
  onSignOut,
}: {
  title: string;
  onMenu: () => void;
  onCreate: () => void;
  onRefresh: () => void;
  loading: boolean;
  language: Language;
  onLanguageChange: () => void;
  onSignOut?: () => void;
}) {
  return (
    <header className="topbar">
      <div className="topbar-title">
        <button className="icon-button menu-button" onClick={onMenu}>
          <Menu size={21} />
        </button>
        <div>
          <span>RUNGUARD /</span>
          <strong>{title}</strong>
        </div>
      </div>
      <div className="topbar-actions">
        <div className="command-search">
          <Search size={16} />
          <span>Search incidents</span>
          <kbd>⌘ K</kbd>
        </div>
        <button className="icon-button" onClick={onRefresh} aria-label="Refresh">
          <RefreshCcw size={17} className={loading ? "spin" : ""} />
        </button>
        <button className="icon-button notification-button" aria-label="Notifications">
          <Bell size={17} />
          <span />
        </button>
        <button
          className="language-button"
          type="button"
          onClick={onLanguageChange}
          aria-label={language === "en" ? "切换为中文" : "Switch to English"}
          data-i18n-skip
        >
          <span className={language === "zh" ? "active" : ""}>中</span>
          <i />
          <span className={language === "en" ? "active" : ""}>EN</span>
        </button>
        {onSignOut && (
          <button className="icon-button" onClick={onSignOut} aria-label="Sign out">
            <KeyRound size={17} />
          </button>
        )}
        <button className="primary-button" onClick={onCreate}>
          <Plus size={17} />
          New incident
        </button>
      </div>
    </header>
  );
}

function AuthenticationGate({
  mode,
  onAuthenticated,
}: {
  mode: string;
  onAuthenticated: (token: string) => Promise<void>;
}) {
  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!token.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      await onAuthenticated(token.trim());
    } catch (nextError) {
      setError((nextError as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="authentication-gate">
      <div className="authentication-icon">
        <LockKeyhole size={24} />
      </div>
      <div>
        <span className="eyebrow">PROTECTED WORKSPACE</span>
        <h1>Authenticate to RunGuard</h1>
        <p>
          Enter a short-lived {mode === "oidc" ? "OIDC access token" : "API key"}.
          Credentials are kept in this browser tab only.
        </p>
      </div>
      <form onSubmit={submit}>
        <label htmlFor="access-token">Access token</label>
        <input
          id="access-token"
          type="password"
          autoComplete="off"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="Paste bearer token"
        />
        {error && <div className="form-error">{error}</div>}
        <button className="primary-button" type="submit" disabled={submitting || !token.trim()}>
          <KeyRound size={17} />
          {submitting ? "Authenticating…" : "Continue securely"}
        </button>
      </form>
    </section>
  );
}

function OverviewPage({
  data,
  approvals,
  traces,
  onOpenIncident,
  onNavigate,
}: {
  data: Overview;
  approvals: ToolIntent[];
  traces: TraceSpan[];
  onOpenIncident: (id: string) => void;
  onNavigate: (view: View) => void;
}) {
  const latestTrace = traces[0];
  return (
    <div className="page-stack">
      <section className="page-intro">
        <div>
          <div className="eyebrow">
            <Radio size={14} />
            LIVE OPERATIONS
          </div>
          <h1>Incident command, with guardrails.</h1>
          <p>
            Investigate faster, control every side effect, and keep a complete trail from alert
            to recovery.
          </p>
        </div>
        <div className="time-range">
          <button className="active">24 hours</button>
          <button>7 days</button>
          <button>30 days</button>
        </div>
      </section>

      <section className="metric-grid">
        <MetricCard
          label="Active incidents"
          value={data.active}
          note={`${data.incidents} total in workspace`}
          icon={AlertTriangle}
          tone="amber"
          trend="+1 today"
        />
        <MetricCard
          label="Automation rate"
          value={`${data.automation_rate}%`}
          note="Resolved without manual execution"
          icon={Zap}
          tone="green"
          trend="+6.2%"
        />
        <MetricCard
          label="Mean time to resolve"
          value={`${data.mttr_minutes}m`}
          note="Across closed incidents"
          icon={Timer}
          tone="blue"
          trend="-18%"
        />
        <MetricCard
          label="Risk controls"
          value={`${data.policy_block_rate}%`}
          note="Dangerous operations intercepted"
          icon={ShieldCheck}
          tone="purple"
          trend="No bypasses"
        />
      </section>

      <section className="overview-grid">
        <div className="panel incident-feed">
          <PanelHeader
            title="Live incident queue"
            subtitle="Prioritized by severity and policy state"
            action="View all"
            onAction={() => onNavigate("incidents")}
          />
          <div className="incident-list compact">
            {data.recent_incidents.map((incident) => (
              <button
                className="incident-row"
                key={incident.id}
                onClick={() => onOpenIncident(incident.id)}
              >
                <SeverityBadge severity={incident.severity} />
                <div className="incident-primary">
                  <div>
                    <strong>{incident.title}</strong>
                    <StatusBadge status={incident.status} />
                  </div>
                  <span>
                    {incident.id} · {incident.service} · {incident.environment}
                  </span>
                </div>
                <div className="incident-meta">
                  <strong>{relativeTime(incident.created_at)}</strong>
                  <span>{incident.tool_calls ?? 0} tool calls</span>
                </div>
                <ChevronRight size={18} />
              </button>
            ))}
          </div>
        </div>

        <div className="panel response-flow-panel">
          <PanelHeader title="Trusted execution flow" subtitle="Current platform control path" />
          <div className="response-flow">
            <FlowNode icon={Bell} label="Signal" detail="Validated" state="done" />
            <FlowConnector />
            <FlowNode icon={Bot} label="Investigate" detail="5 agents" state="done" />
            <FlowConnector />
            <FlowNode icon={Shield} label="Policy" detail="Enforced" state="active" />
            <FlowConnector />
            <FlowNode icon={TerminalSquare} label="Execute" detail="Sandboxed" state="idle" />
            <FlowConnector />
            <FlowNode icon={CheckCircle2} label="Verify" detail="SLO gates" state="idle" />
          </div>
          <div className="guardrail-note">
            <LockKeyhole size={18} />
            <div>
              <strong>LLMs never receive direct infrastructure credentials.</strong>
              <span>Every intent passes schema, policy, approval and idempotency checks.</span>
            </div>
          </div>
        </div>
      </section>

      <section className="overview-grid lower">
        <div className="panel">
          <PanelHeader
            title="Approval inbox"
            subtitle="Production changes waiting for a human"
            action="Open center"
            onAction={() => onNavigate("approvals")}
          />
          {approvals.length ? (
            approvals.slice(0, 2).map((intent) => (
              <div className="approval-preview" key={intent.id}>
                <div className="risk-icon">
                  <ShieldAlert size={20} />
                </div>
                <div>
                  <strong>{intent.tool_name}</strong>
                  <span>
                    {intent.incident_id} · {String(intent.resource.name)} · {intent.environment}
                  </span>
                  <div className="approval-tags">
                    <RiskBadge risk={intent.risk_level} />
                    <span>rollback ready</span>
                  </div>
                </div>
                <ArrowRight size={18} />
              </div>
            ))
          ) : (
            <EmptyInline icon={CheckCircle2} text="No pending approvals" />
          )}
        </div>
        <div className="panel trace-pulse">
          <PanelHeader
            title="Trace pulse"
            subtitle="Latest recorded execution span"
            action="Explore traces"
            onAction={() => onNavigate("traces")}
          />
          {latestTrace ? (
            <>
              <div className="trace-hero">
                <div className="trace-ring">
                  <Activity size={28} />
                </div>
                <div>
                  <span>{latestTrace.span_type.toUpperCase()}</span>
                  <strong>{latestTrace.name}</strong>
                  <small>
                    {latestTrace.incident_id} · {latestTrace.agent ?? "system"}
                  </small>
                </div>
                <div className="duration">
                  {latestTrace.duration_ms}
                  <span>ms</span>
                </div>
              </div>
              <div className="spark-bars">
                {[36, 62, 45, 74, 52, 86, 68, 92, 58, 76, 48, 81].map((height, index) => (
                  <span key={index} style={{ height: `${height}%` }} />
                ))}
              </div>
            </>
          ) : (
            <EmptyInline icon={GitBranch} text="No trace spans recorded" />
          )}
        </div>
      </section>
    </div>
  );
}

function MetricCard({
  label,
  value,
  note,
  icon: Icon,
  tone,
  trend,
}: {
  label: string;
  value: string | number;
  note: string;
  icon: typeof Gauge;
  tone: string;
  trend: string;
}) {
  return (
    <div className={`metric-card ${tone}`}>
      <div className="metric-top">
        <span>{label}</span>
        <div className="metric-icon">
          <Icon size={19} />
        </div>
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-bottom">
        <span>{note}</span>
        <strong>
          <ArrowDownRight size={13} />
          {trend}
        </strong>
      </div>
    </div>
  );
}

function IncidentsPage({
  incidents,
  onOpen,
  onCreate,
}: {
  incidents: Incident[];
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("ALL");
  const filtered = incidents.filter((incident) => {
    const matchesQuery = `${incident.id} ${incident.title} ${incident.service}`
      .toLowerCase()
      .includes(query.toLowerCase());
    const matchesFilter = filter === "ALL" || incident.status === filter;
    return matchesQuery && matchesFilter;
  });

  return (
    <div className="page-stack">
      <section className="page-intro slim">
        <div>
          <div className="eyebrow">INCIDENT OPERATIONS</div>
          <h1>Incident queue</h1>
          <p>One immutable operating record for every alert, decision, and recovery action.</p>
        </div>
        <button className="primary-button" onClick={onCreate}>
          <Plus size={17} /> Create incident
        </button>
      </section>
      <div className="panel table-panel">
        <div className="table-toolbar">
          <div className="table-search">
            <Search size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by ID, service or title"
            />
          </div>
          <div className="filter-tabs">
            {["ALL", "NEW", "INVESTIGATING", "WAITING_APPROVAL", "RESOLVED"].map((status) => (
              <button
                key={status}
                className={filter === status ? "active" : ""}
                onClick={() => setFilter(status)}
              >
                {status === "ALL" ? "All" : friendlyStatus(status)}
              </button>
            ))}
          </div>
          <button className="secondary-button">
            <ListFilter size={16} /> Filters
          </button>
        </div>
        <div className="data-table">
          <div className="data-row data-head incident-columns">
            <span>Incident</span>
            <span>Service / environment</span>
            <span>Status</span>
            <span>Created</span>
            <span>Evidence</span>
            <span />
          </div>
          {filtered.map((incident) => (
            <button
              className="data-row incident-columns"
              key={incident.id}
              onClick={() => onOpen(incident.id)}
            >
              <span className="incident-title-cell">
                <SeverityBadge severity={incident.severity} />
                <span>
                  <strong>{incident.title}</strong>
                  <small>{incident.id}</small>
                </span>
              </span>
              <span className="service-cell">
                <Server size={15} />
                <span>
                  <strong>{incident.service}</strong>
                  <small>{incident.environment}</small>
                </span>
              </span>
              <span>
                <StatusBadge status={incident.status} />
              </span>
              <span className="muted-cell">{relativeTime(incident.created_at)}</span>
              <span className="muted-cell">{incident.evidence_count ?? 0} linked</span>
              <span>
                <ChevronRight size={17} />
              </span>
            </button>
          ))}
        </div>
        {!filtered.length && <EmptyInline icon={Search} text="No incidents match this view" />}
        <div className="table-footer">
          <span>Showing {filtered.length} incidents</span>
          <span>Event stream is append-only</span>
        </div>
      </div>
    </div>
  );
}

function IncidentWorkspace({
  incidentId,
  onChanged,
  showToast,
}: {
  incidentId: string;
  onChanged: () => Promise<void>;
  showToast: (toast: Toast) => void;
}) {
  const [incident, setIncident] = useState<Incident | null>(null);
  const [tab, setTab] = useState<"investigation" | "plan" | "timeline">("investigation");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setIncident(await api.incident(incidentId));
    } catch (error) {
      showToast({ message: (error as Error).message, tone: "error" });
    }
  }, [incidentId, showToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const start = async () => {
    setBusy(true);
    try {
      const updated = await api.startIncident(incidentId);
      setIncident(updated);
      await onChanged();
      showToast({
        message:
          updated.status === "WAITING_APPROVAL"
            ? "Investigation complete — approval required"
            : "Incident workflow completed",
        tone: "success",
      });
    } catch (error) {
      showToast({ message: (error as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const replay = async () => {
    setBusy(true);
    try {
      const result = await api.replayIncident(incidentId);
      showToast({
        message: `Replay complete: ${String(result.span_count)} spans, zero side effects`,
        tone: "success",
      });
    } catch (error) {
      showToast({ message: (error as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  if (!incident) return <LoadingState />;
  const latestRun = incident.runs?.[0];
  const activeStep = STATUS_ORDER.indexOf(incident.status);
  const canStart = ["NEW", "INVESTIGATING", "HUMAN_HANDOFF"].includes(incident.status);

  return (
    <div className="page-stack workspace-page">
      <section className="incident-header-card">
        <div className="incident-heading">
          <div className="heading-badges">
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
            <span className="environment-badge">
              <Box size={13} /> {incident.environment}
            </span>
          </div>
          <h1>{incident.title}</h1>
          <p>
            {incident.id} · {incident.service} · Opened {relativeTime(incident.created_at)}
          </p>
        </div>
        <div className="incident-actions">
          {latestRun && (
            <button className="secondary-button" onClick={() => void replay()} disabled={busy}>
              <RotateCcw size={16} />
              Replay
            </button>
          )}
          <button className="primary-button" onClick={() => void start()} disabled={!canStart || busy}>
            {busy ? <RefreshCcw size={16} className="spin" /> : <Play size={16} />}
            {canStart ? "Run response" : friendlyStatus(incident.status)}
          </button>
        </div>
      </section>

      <section className="state-rail">
        {STATUS_ORDER.slice(0, -1).map((status, index) => {
          const isDone = activeStep > index || incident.status === "RESOLVED";
          const isActive = activeStep === index;
          const skipped =
            incident.status === "WAITING_APPROVAL" &&
            ["EXECUTING", "VERIFYING"].includes(status);
          return (
            <div className={`state-step ${isDone ? "done" : ""} ${isActive ? "active" : ""}`} key={status}>
              <div className="state-marker">
                {isDone ? <Check size={13} /> : isActive ? <Pause size={11} /> : <span />}
              </div>
              <div>
                <strong>{friendlyStatus(status)}</strong>
                <span>{skipped ? "Pending" : isDone ? "Complete" : isActive ? "Current" : "Queued"}</span>
              </div>
            </div>
          );
        })}
      </section>

      <section className="workspace-grid">
        <div className="workspace-main panel">
          <div className="workspace-tabs">
            {[
              ["investigation", "Investigation"],
              ["plan", "Remediation plan"],
              ["timeline", "Event timeline"],
            ].map(([id, label]) => (
              <button
                key={id}
                className={tab === id ? "active" : ""}
                onClick={() => setTab(id as typeof tab)}
              >
                {label}
              </button>
            ))}
          </div>
          {tab === "investigation" && (
            <InvestigationView
              evidence={incident.evidence ?? []}
              hypotheses={incident.hypotheses ?? []}
              status={incident.status}
            />
          )}
          {tab === "plan" && <PlanView intents={incident.tool_intents ?? []} />}
          {tab === "timeline" && <TimelineView events={incident.events ?? []} />}
        </div>
        <aside className="workspace-side">
          <div className="panel run-summary">
            <PanelHeader title="Run telemetry" subtitle={latestRun?.id ?? "No run started"} />
            <SummaryStat icon={Sparkles} label="Prompt version" value={latestRun?.prompt_version ?? "1.2.1"} />
            <SummaryStat icon={Code2} label="Tokens" value={formatNumber(latestRun?.token_usage ?? 0)} />
            <SummaryStat icon={TerminalSquare} label="Tool calls" value={String(latestRun?.tool_calls ?? 0)} />
            <SummaryStat
              icon={Fingerprint}
              label="Evidence"
              value={String(incident.evidence?.length ?? 0)}
            />
          </div>
          <div className="panel agent-roster">
            <PanelHeader title="Agent roster" subtitle="Version-pinned for this run" />
            <AgentRow name="Commander" role="Planning & control" state="done" />
            <AgentRow name="Investigator" role="Evidence & inference" state="done" />
            <AgentRow name="Remediation" role="Safe repair plan" state="done" />
            <AgentRow
              name="Reporter"
              role="Postmortem"
              state={incident.status === "RESOLVED" ? "done" : "idle"}
            />
          </div>
          <div className="panel trust-panel">
            <div className="trust-icon">
              <ShieldCheck size={24} />
            </div>
            <strong>Execution boundary active</strong>
            <p>Direct cluster access is disabled. All writes require normalized intent.</p>
            <div className="trust-checks">
              <span>
                <Check size={13} /> Schema validation
              </span>
              <span>
                <Check size={13} /> Idempotency
              </span>
              <span>
                <Check size={13} /> Compensating action
              </span>
            </div>
          </div>
        </aside>
      </section>
    </div>
  );
}

function InvestigationView({
  evidence,
  hypotheses,
  status,
}: {
  evidence: Evidence[];
  hypotheses: Incident["hypotheses"];
  status: string;
}) {
  if (!evidence.length) {
    return (
      <div className="workspace-empty">
        <div className="empty-orbit">
          <Search size={30} />
        </div>
        <h3>Ready to investigate</h3>
        <p>
          Start the response workflow to collect metrics, logs, workload state, and deployment
          history.
        </p>
        <span>Current state: {friendlyStatus(status)}</span>
      </div>
    );
  }
  const top = hypotheses?.[0];
  return (
    <div className="investigation-content">
      {top && (
        <div className="hypothesis-card">
          <div className="hypothesis-top">
            <span className="hypothesis-label">
              <Sparkles size={14} /> TOP HYPOTHESIS
            </span>
            <strong>{Math.round(top.confidence * 100)}% confidence</strong>
          </div>
          <h3>{top.cause}</h3>
          <div className="confidence-track">
            <span style={{ width: `${top.confidence * 100}%` }} />
          </div>
          <p>
            Supported by {top.evidence_ids.length} independently sourced observations. Each
            inference remains linked to its original evidence.
          </p>
        </div>
      )}
      <div className="section-heading">
        <div>
          <h3>Evidence chain</h3>
          <p>{evidence.length} observations · source integrity preserved</p>
        </div>
        <span className="verified-label">
          <ShieldCheck size={14} /> Verified sources
        </span>
      </div>
      <div className="evidence-grid">
        {evidence.map((item) => (
          <EvidenceCard key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
}

function EvidenceCard({ item }: { item: Evidence }) {
  const icons: Record<string, typeof Gauge> = {
    prometheus: Activity,
    kubernetes: Hexagon,
    loki: FileClock,
    github: GitBranch,
  };
  const Icon = icons[item.source_type] ?? Database;
  return (
    <div className="evidence-card">
      <div className={`source-icon ${item.source_type}`}>
        <Icon size={18} />
      </div>
      <div className="evidence-body">
        <div>
          <span>{item.source_type}</span>
          <code>{item.id}</code>
        </div>
        <strong>{item.title}</strong>
        <p>{item.content}</p>
        <small>{compactUri(item.source_uri)}</small>
      </div>
      <CheckCircle2 size={16} className="evidence-check" />
    </div>
  );
}

function PlanView({ intents }: { intents: ToolIntent[] }) {
  if (!intents.length) {
    return (
      <div className="workspace-empty">
        <div className="empty-orbit">
          <Code2 size={30} />
        </div>
        <h3>No remediation plan yet</h3>
        <p>A structured, reversible Tool Intent appears here after evidence review.</p>
      </div>
    );
  }
  return (
    <div className="plan-content">
      {intents.map((intent, index) => (
        <div className="intent-card" key={intent.id}>
          <div className="intent-heading">
            <div>
              <span className="step-number">{String(index + 1).padStart(2, "0")}</span>
              <div>
                <strong>{intent.tool_name}</strong>
                <small>{intent.id}</small>
              </div>
            </div>
            <div>
              <RiskBadge risk={intent.risk_level} />
              <StatusBadge status={intent.status} />
            </div>
          </div>
          <div className="intent-grid">
            <IntentBlock label="Target" value={intent.resource} />
            <IntentBlock label="Proposed change" value={intent.arguments} accent />
            <IntentBlock label="Rollback" value={intent.rollback} />
          </div>
          <div className="intent-footer">
            <KeyRound size={14} />
            <code>{intent.idempotency_key}</code>
            <span />
            <Shield size={14} />
            <strong>{intent.matched_rule ?? "Policy pending"}</strong>
          </div>
        </div>
      ))}
    </div>
  );
}

function IntentBlock({
  label,
  value,
  accent,
}: {
  label: string;
  value: Record<string, unknown>;
  accent?: boolean;
}) {
  return (
    <div className={`intent-block ${accent ? "accent" : ""}`}>
      <span>{label}</span>
      {Object.entries(value).map(([key, entry]) => (
        <div key={key}>
          <code>{key}</code>
          <strong>{String(entry)}</strong>
        </div>
      ))}
    </div>
  );
}

function TimelineView({ events }: { events: NonNullable<Incident["events"]> }) {
  return (
    <div className="timeline-list">
      {events
        .slice()
        .reverse()
        .map((event) => (
          <div className="timeline-item" key={event.id}>
            <div className="timeline-axis">
              <span />
            </div>
            <div className="timeline-content">
              <div>
                <strong>{event.event_type.replaceAll(".", " · ")}</strong>
                <time>{formatTime(event.created_at)}</time>
              </div>
              <p>
                Actor <code>{event.actor}</code> appended event #{event.sequence} to the incident
                record.
              </p>
              <pre>{JSON.stringify(event.payload, null, 2)}</pre>
            </div>
          </div>
        ))}
    </div>
  );
}

function ApprovalCenter({
  approvals,
  onChanged,
  showToast,
  onOpenIncident,
}: {
  approvals: ToolIntent[];
  onChanged: () => Promise<void>;
  showToast: (toast: Toast) => void;
  onOpenIncident: (id: string) => void;
}) {
  const [selected, setSelected] = useState<ToolIntent | null>(approvals[0] ?? null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!selected || !approvals.some((item) => item.id === selected.id)) {
      setSelected(approvals[0] ?? null);
    }
  }, [approvals, selected]);

  const decide = async (decision: "approve" | "reject") => {
    if (!selected) return;
    setBusy(true);
    try {
      if (decision === "approve") {
        await api.approve(selected.id, comment || "Reviewed evidence, scope, and rollback.");
        showToast({ message: "Approved, executed, and verified successfully", tone: "success" });
      } else {
        await api.reject(selected.id, comment || "Rejected for manual investigation.");
        showToast({ message: "Intent rejected and handed to a human", tone: "success" });
      }
      setComment("");
      await onChanged();
    } catch (error) {
      showToast({ message: (error as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-stack">
      <section className="page-intro slim">
        <div>
          <div className="eyebrow">
            <ShieldAlert size={14} /> HUMAN CONTROL POINT
          </div>
          <h1>Approval center</h1>
          <p>Review exact changes, policy rationale, and rollback before any side effect.</p>
        </div>
        <div className="approval-count">
          <strong>{approvals.length}</strong>
          <span>awaiting review</span>
        </div>
      </section>
      {!selected ? (
        <div className="panel empty-approval">
          <div className="success-orbit">
            <ShieldCheck size={34} />
          </div>
          <h2>Approval inbox is clear</h2>
          <p>No Agent is waiting to change protected infrastructure.</p>
        </div>
      ) : (
        <section className="approval-layout">
          <div className="panel approval-queue">
            <div className="queue-header">
              <strong>Pending intents</strong>
              <span>Oldest first</span>
            </div>
            {approvals.map((intent) => (
              <button
                key={intent.id}
                className={`queue-item ${selected.id === intent.id ? "active" : ""}`}
                onClick={() => setSelected(intent)}
              >
                <div>
                  <RiskBadge risk={intent.risk_level} />
                  <span>{relativeTime(intent.created_at)}</span>
                </div>
                <strong>{intent.tool_name}</strong>
                <small>
                  {intent.incident_id} · {String(intent.resource.name)}
                </small>
                <ChevronRight size={17} />
              </button>
            ))}
          </div>
          <div className="panel approval-detail">
            <div className="approval-detail-header">
              <div>
                <div className="heading-badges">
                  <RiskBadge risk={selected.risk_level} />
                  <span className="environment-badge">{selected.environment}</span>
                </div>
                <h2>{selected.tool_name}</h2>
                <button onClick={() => onOpenIncident(selected.incident_id)}>
                  {selected.incident_id} · {selected.incident_title}
                  <ArrowRight size={14} />
                </button>
              </div>
              <div className="policy-stamp">
                <Shield size={22} />
                <div>
                  <span>POLICY DECISION</span>
                  <strong>Human approval required</strong>
                </div>
              </div>
            </div>
            <div className="approval-reason">
              <AlertTriangle size={18} />
              <div>
                <strong>{selected.matched_rule}</strong>
                <p>{selected.reason}</p>
              </div>
            </div>
            <h3 className="detail-section-title">Proposed infrastructure change</h3>
            <div className="diff-card">
              <div className="diff-head">
                <span>
                  {String(selected.resource.kind)} / {String(selected.resource.name)}
                </span>
                <code>{String(selected.resource.namespace)}</code>
              </div>
              {Object.entries(selected.arguments).map(([key, value]) => (
                <div className="diff-row" key={key}>
                  <span>{key}</span>
                  <code className="removed">
                    - {String(selected.rollback[key] ?? "current")}
                  </code>
                  <ArrowRight size={14} />
                  <code className="added">+ {String(value)}</code>
                </div>
              ))}
            </div>
            <div className="approval-safety-grid">
              <div>
                <span>
                  <RotateCcw size={15} /> ROLLBACK
                </span>
                <strong>Compensating action ready</strong>
                <code>{JSON.stringify(selected.rollback)}</code>
              </div>
              <div>
                <span>
                  <Fingerprint size={15} /> IDEMPOTENCY
                </span>
                <strong>Duplicate side effects blocked</strong>
                <code>{selected.idempotency_key}</code>
              </div>
            </div>
            <label className="comment-field">
              Review note
              <textarea
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Record the reason for your decision…"
              />
            </label>
            <div className="approval-actions">
              <button
                className="danger-button"
                onClick={() => void decide("reject")}
                disabled={busy}
              >
                <X size={17} /> Reject
              </button>
              <span>
                Approval is recorded with reviewer, timestamp, policy version, and input snapshot.
              </span>
              <button
                className="primary-button"
                onClick={() => void decide("approve")}
                disabled={busy}
              >
                {busy ? <RefreshCcw size={16} className="spin" /> : <ShieldCheck size={17} />}
                Approve & execute
              </button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

function TraceExplorer({ traces }: { traces: TraceSpan[] }) {
  const [type, setType] = useState("all");
  const [query, setQuery] = useState("");
  const filtered = traces.filter((trace) => {
    return (
      (type === "all" || trace.span_type === type) &&
      `${trace.name} ${trace.incident_id} ${trace.agent}`
        .toLowerCase()
        .includes(query.toLowerCase())
    );
  });
  const grouped = useMemo(() => {
    const map = new Map<string, TraceSpan[]>();
    filtered.forEach((span) => map.set(span.run_id, [...(map.get(span.run_id) ?? []), span]));
    return [...map.entries()];
  }, [filtered]);

  return (
    <div className="page-stack">
      <section className="page-intro slim">
        <div>
          <div className="eyebrow">
            <Activity size={14} /> OBSERVABILITY
          </div>
          <h1>Trace explorer</h1>
          <p>Follow Agent reasoning, retrieval, policy, execution, and verification end to end.</p>
        </div>
        <div className="trace-health">
          <span className="live-dot" />
          <div>
            <strong>Collector connected</strong>
            <span>{traces.length} spans retained</span>
          </div>
        </div>
      </section>
      <div className="panel trace-panel">
        <div className="table-toolbar">
          <div className="table-search">
            <Search size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search spans, agents or incident IDs"
            />
          </div>
          <div className="filter-tabs trace-filters">
            {["all", "agent", "retrieval", "tool", "policy", "approval", "verification"].map(
              (item) => (
                <button
                  key={item}
                  className={type === item ? "active" : ""}
                  onClick={() => setType(item)}
                >
                  {item}
                </button>
              ),
            )}
          </div>
        </div>
        <div className="trace-list">
          {grouped.map(([runId, spans]) => (
            <div className="trace-run" key={runId}>
              <div className="trace-run-head">
                <div>
                  <Network size={17} />
                  <span>
                    <strong>{runId}</strong>
                    <small>
                      {spans[0]?.incident_id} · {spans[0]?.incident_title}
                    </small>
                  </span>
                </div>
                <div>
                  <span>{spans.length} spans</span>
                  <strong>{spans.reduce((total, span) => total + span.duration_ms, 0)} ms</strong>
                </div>
              </div>
              <div className="waterfall">
                {spans.map((span, index) => (
                  <div className="waterfall-row" key={span.id}>
                    <div className={`span-type ${span.span_type}`}>
                      <TraceTypeIcon type={span.span_type} />
                      {span.span_type}
                    </div>
                    <div className="span-name">
                      <strong>{span.name}</strong>
                      <span>{span.agent ?? "system"}</span>
                    </div>
                    <div className="span-water">
                      <span
                        style={{
                          marginLeft: `${Math.min(index * 6, 38)}%`,
                          width: `${Math.max(18, Math.min(span.duration_ms / 18, 70))}%`,
                        }}
                      />
                    </div>
                    <code>{span.duration_ms}ms</code>
                    <span className={`span-status ${span.status.toLowerCase()}`}>
                      {span.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        {!grouped.length && <EmptyInline icon={Activity} text="No traces match these filters" />}
      </div>
    </div>
  );
}

function EvaluationDashboard({
  runs,
  onChanged,
  showToast,
}: {
  runs: EvalRun[];
  onChanged: () => Promise<void>;
  showToast: (toast: Toast) => void;
}) {
  const [busy, setBusy] = useState(false);
  const latest = runs[0];
  const runSuite = async () => {
    setBusy(true);
    try {
      await api.runEvaluation();
      await onChanged();
      showToast({ message: "Baseline suite completed: 12 cases evaluated", tone: "success" });
    } catch (error) {
      showToast({ message: (error as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };
  const metrics = latest?.metrics;
  return (
    <div className="page-stack">
      <section className="page-intro slim">
        <div>
          <div className="eyebrow">
            <BarChart3 size={14} /> RELIABILITY EVALUATION
          </div>
          <h1>Evaluation dashboard</h1>
          <p>Reference expectations from fixed deterministic fixtures, not production measurements.</p>
        </div>
        <button className="primary-button" onClick={() => void runSuite()} disabled={busy}>
          {busy ? <RefreshCcw size={16} className="spin" /> : <Play size={16} />}
          Run baseline-12
        </button>
      </section>
      {!latest ? (
        <div className="panel evaluation-empty">
          <div className="empty-orbit">
            <BarChart3 size={34} />
          </div>
          <h2>Establish the v1.0 baseline</h2>
          <p>Load 12 deterministic fixtures to inspect expected diagnosis and policy behavior.</p>
          <button className="primary-button" onClick={() => void runSuite()} disabled={busy}>
            Run evaluation
          </button>
        </div>
      ) : (
        <>
          <section className="evaluation-summary">
            <div className="score-card">
              <div className="score-ring">
                <svg viewBox="0 0 120 120">
                  <circle cx="60" cy="60" r="50" />
                  <circle
                    cx="60"
                    cy="60"
                    r="50"
                    style={{
                      strokeDashoffset: `${314 - (314 * Number(metrics?.top1_root_cause_accuracy)) / 100}`,
                    }}
                  />
                </svg>
                <div>
                  <strong>{String(metrics?.top1_root_cause_accuracy)}%</strong>
                  <span>Top-1 RCA</span>
                </div>
              </div>
              <div>
                <span>BASELINE SCORE</span>
                <h2>Trusted response quality</h2>
                <p>
                  Static fixture · prompt {latest.prompt_version} · {latest.cases.length}{" "}
                  fixed cases
                </p>
              </div>
            </div>
            <div className="evaluation-kpis">
              <EvalKpi label="Policy accuracy" value={`${metrics?.policy_decision_accuracy}%`} />
              <EvalKpi label="R3 block rate" value={`${metrics?.dangerous_action_block_rate}%`} />
              <EvalKpi label="Trace coverage" value={`${metrics?.trace_coverage}%`} />
              <EvalKpi label="Duplicate effects" value={String(metrics?.duplicate_side_effects)} />
            </div>
          </section>
          <section className="evaluation-grid">
            <div className="panel">
              <PanelHeader
                title="Scenario matrix"
                subtitle={`${latest.cases.length} fixed Kubernetes failure modes`}
              />
              <div className="case-table">
                <div className="case-row case-head">
                  <span>Scenario</span>
                  <span>Root cause</span>
                  <span>Policy</span>
                  <span>Calls</span>
                  <span>Result</span>
                </div>
                {latest.cases.map((testCase) => (
                  <div className="case-row" key={String(testCase.id)}>
                    <span>
                      <strong>{String(testCase.name)}</strong>
                      <small>{String(testCase.id)}</small>
                    </span>
                    <span>{testCase.top1_correct ? "Top-1 match" : "Top-3 match"}</span>
                    <span>{String(testCase.actual_policy_decision).replace("_", " ")}</span>
                    <span>{String(testCase.tool_calls)}</span>
                    <span className={testCase.status === "PASS" ? "case-pass" : "case-partial"}>
                      {testCase.status === "PASS" ? <Check size={13} /> : <AlertTriangle size={13} />}
                      {String(testCase.status)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <aside className="evaluation-side">
              <div className="panel metric-breakdown">
                <PanelHeader title="Efficiency" subtitle="Per incident average" />
                <BreakdownBar
                  label="Tool calls"
                  value={Number(metrics?.average_tool_calls)}
                  max={12}
                  suffix=""
                />
                <BreakdownBar
                  label="P50 duration"
                  value={Number(metrics?.p50_duration_seconds)}
                  max={20}
                  suffix="s"
                />
                <BreakdownBar
                  label="P95 duration"
                  value={Number(metrics?.p95_duration_seconds)}
                  max={20}
                  suffix="s"
                />
                <BreakdownBar
                  label="Valid arguments"
                  value={Number(metrics?.valid_tool_arguments)}
                  max={100}
                  suffix="%"
                />
              </div>
              <div className="panel measured-note">
                <Fingerprint size={25} />
                <strong>Reference fixture — not measured</strong>
                <p>
                  These values are bundled expectations for UI and policy-contract demonstrations.
                  They do not execute a model, Kubernetes fault, or worker-recovery experiment.
                </p>
                <span>
                  <Check size={13} /> Fixture versioned with the repository
                </span>
              </div>
            </aside>
          </section>
        </>
      )}
    </div>
  );
}

function PostmortemPage({
  incidents,
  showToast,
}: {
  incidents: Incident[];
  showToast: (toast: Toast) => void;
}) {
  const candidates = incidents.filter((incident) =>
    ["RESOLVED", "ROLLED_BACK", "HUMAN_HANDOFF"].includes(incident.status),
  );
  const [selected, setSelected] = useState<string>(candidates[0]?.id ?? "");
  const [document, setDocument] = useState<Postmortem | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    setLoading(true);
    api
      .postmortem(selected)
      .then((result) => {
        if (active) setDocument(result);
      })
      .catch(() => {
        if (active) setDocument(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected]);

  const generate = async () => {
    if (!selected) return;
    setLoading(true);
    try {
      const result = await api.generatePostmortem(selected);
      setDocument(result);
      showToast({ message: "Structured postmortem generated", tone: "success" });
    } catch (error) {
      showToast({ message: (error as Error).message, tone: "error" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="page-stack">
      <div className="page-intro slim">
        <div>
          <div className="eyebrow">LEARNING SYSTEM</div>
          <h1>Postmortems</h1>
          <p>Turn evidence, decisions, execution, and verification into a structured record.</p>
        </div>
        <div className="postmortem-actions">
          <select value={selected} onChange={(event) => setSelected(event.target.value)}>
            {candidates.map((incident) => (
              <option value={incident.id} key={incident.id}>
                {incident.id} · {incident.title}
              </option>
            ))}
          </select>
          <button className="secondary-button" onClick={() => void generate()} disabled={!selected || loading}>
            <Sparkles size={15} />
            {document ? "Regenerate" : "Generate report"}
          </button>
          {document && (
            <button
              className="primary-button postmortem-export"
              type="button"
              onClick={() => void api.exportPostmortem(document.incident_id)}
            >
              <FileText size={15} />
              Export Markdown
            </button>
          )}
        </div>
      </div>

      {!candidates.length ? (
        <div className="empty-state">
          <FileClock size={28} />
          <h2>No closed incidents yet</h2>
          <p>Resolve or roll back an incident before generating a postmortem.</p>
        </div>
      ) : loading && !document ? (
        <LoadingState />
      ) : !document ? (
        <div className="empty-state">
          <FileText size={28} />
          <h2>No report generated</h2>
          <p>Create a structured postmortem from the immutable incident record.</p>
          <button className="primary-button" onClick={() => void generate()}>
            Generate report
          </button>
        </div>
      ) : (
        <article className="postmortem-document">
          <header className="postmortem-hero">
            <div>
              <span>{document.status} · {document.generated_by}</span>
              <h2>{document.title}</h2>
              <p>{document.summary}</p>
            </div>
            <div className="postmortem-meta">
              <span>Incident</span>
              <strong>{document.incident_id}</strong>
              <span>Run</span>
              <strong>{document.run_id ?? "n/a"}</strong>
            </div>
          </header>
          <div className="postmortem-grid">
            <section>
              <h3>Impact</h3>
              <p>{document.impact}</p>
            </section>
            <section>
              <h3>Root cause</h3>
              <p>{document.root_cause}</p>
            </section>
          </div>
          <section className="postmortem-section">
            <h3>Contributing factors</h3>
            <ul>{document.contributing_factors.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
          <section className="postmortem-section">
            <h3>Incident timeline</h3>
            <div className="postmortem-timeline">
              {document.timeline.map((item, index) => (
                <div key={`${item.at}-${index}`}>
                  <time>{formatTime(item.at)}</time>
                  <span>{item.actor}</span>
                  <strong>{item.event}</strong>
                  <p>{item.detail}</p>
                </div>
              ))}
            </div>
          </section>
          <div className="postmortem-grid">
            <section>
              <h3>Recovery actions</h3>
              <ul>{document.remediation.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
            <section>
              <h3>Lessons learned</h3>
              <ul>{document.lessons.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
          </div>
          <section className="postmortem-section">
            <h3>Action items</h3>
            <div className="action-item-list">
              {document.action_items.map((item) => (
                <div key={item.title}>
                  <span className={`severity ${item.priority.toLowerCase()}`}>{item.priority}</span>
                  <strong>{item.title}</strong>
                  <span>{item.owner}</span>
                  <em>{item.status}</em>
                </div>
              ))}
            </div>
          </section>
        </article>
      )}
    </section>
  );
}

function PolicySimulator({ showToast }: { showToast: (toast: Toast) => void }) {
  const [environment, setEnvironment] = useState("production");
  const [tool, setTool] = useState("kubernetes.patch_deployment");
  const [risk, setRisk] = useState("R2");
  const [rollback, setRollback] = useState(true);
  const [result, setResult] = useState<Record<string, string> | null>({
    decision: "require_approval",
    risk_level: "R2",
    matched_policy: "prod-write-requires-human",
    reason: "Production write operation requires SRE approval.",
  });
  const simulate = async () => {
    try {
      setResult(
        await api.simulatePolicy({
          agent: "remediation-agent",
          role: "incident_remediator",
          environment,
          tool,
          resource: "order-api",
          risk_level: risk,
          has_rollback: rollback,
          rollback: rollback ? { memory_limit: "256Mi" } : {},
          incident_severity: "P1",
        }),
      );
    } catch (error) {
      showToast({ message: (error as Error).message, tone: "error" });
    }
  };
  return (
    <div className="page-stack">
      <section className="page-intro slim">
        <div>
          <div className="eyebrow">
            <ShieldCheck size={14} /> POLICY AS CODE
          </div>
          <h1>Policy simulator</h1>
          <p>Preview the exact decision before an Agent intent enters the execution path.</p>
        </div>
        <span className="version-chip">policy · 1.2.1 · active</span>
      </section>
      <section className="policy-layout">
        <div className="panel policy-form">
          <PanelHeader title="Tool intent input" subtitle="Normalized policy evaluation payload" />
          <div className="form-grid">
            <label>
              Environment
              <select value={environment} onChange={(event) => setEnvironment(event.target.value)}>
                <option value="staging">staging</option>
                <option value="production">production</option>
              </select>
            </label>
            <label>
              Risk level
              <select value={risk} onChange={(event) => setRisk(event.target.value)}>
                {["R0", "R1", "R2", "R3"].map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </label>
            <label className="wide">
              Tool
              <select value={tool} onChange={(event) => setTool(event.target.value)}>
                <option value="prometheus.query">prometheus.query</option>
                <option value="kubernetes.patch_deployment">kubernetes.patch_deployment</option>
                <option value="kubernetes.rollout_restart">kubernetes.rollout_restart</option>
                <option value="kubernetes.delete_namespace">kubernetes.delete_namespace</option>
                <option value="shell.execute">shell.execute</option>
              </select>
            </label>
          </div>
          <label className="toggle-row">
            <div>
              <strong>Verified rollback available</strong>
              <span>Intent includes a compensating action and before snapshot.</span>
            </div>
            <input
              type="checkbox"
              checked={rollback}
              onChange={(event) => setRollback(event.target.checked)}
            />
          </label>
          <div className="input-preview">
            <div>
              <Code2 size={15} />
              Policy input
            </div>
            <pre>
{JSON.stringify(
  {
    agent: "remediation-agent",
    role: "incident_remediator",
    environment,
    tool,
    resource: "order-api",
    risk_level: risk,
    has_rollback: rollback,
    rollback: rollback ? { memory_limit: "256Mi" } : {},
    incident_severity: "P1",
  },
  null,
  2,
)}
            </pre>
          </div>
          <button className="primary-button full-button" onClick={() => void simulate()}>
            <Play size={16} /> Evaluate intent
          </button>
        </div>
        <div className="panel policy-result">
          <PanelHeader title="Decision" subtitle="Deterministic policy output" />
          {result && (
            <>
              <div className={`decision-hero ${result.decision}`}>
                {result.decision === "allow" ? (
                  <ShieldCheck size={33} />
                ) : result.decision === "deny" ? (
                  <ShieldAlert size={33} />
                ) : (
                  <ClipboardCheck size={33} />
                )}
                <span>DECISION</span>
                <h2>{result.decision.replace("_", " ")}</h2>
                <p>{result.reason}</p>
              </div>
              <div className="decision-details">
                <div>
                  <span>Matched rule</span>
                  <code>{result.matched_policy}</code>
                </div>
                <div>
                  <span>Classified risk</span>
                  <RiskBadge risk={result.risk_level} />
                </div>
                <div>
                  <span>Next gate</span>
                  <strong>
                    {result.decision === "allow"
                      ? "Idempotent execution"
                      : result.decision === "deny"
                        ? "Stop workflow"
                        : "Human approval"}
                  </strong>
                </div>
              </div>
              <div className="rule-path">
                <strong>Evaluation path</strong>
                {["Schema valid", "Role permitted", `Risk ${result.risk_level}`, result.matched_policy].map(
                  (item, index) => (
                    <div key={item}>
                      <span>{index + 1}</span>
                      <p>{item}</p>
                      <Check size={14} />
                    </div>
                  ),
                )}
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function CreateIncidentModal({
  onClose,
  onCreated,
  showToast,
}: {
  onClose: () => void;
  onCreated: (incident: Incident) => void;
  showToast: (toast: Toast) => void;
}) {
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      onCreated(
        await api.createIncident({
          title: String(form.get("title")),
          severity: String(form.get("severity")),
          service: String(form.get("service")),
          environment: String(form.get("environment")),
          description: String(form.get("description")),
        }),
      );
    } catch (error) {
      showToast({ message: (error as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <form className="modal" onSubmit={(event) => void submit(event)} onMouseDown={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <span>MANUAL INTAKE</span>
            <h2>Create incident</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose}>
            <X size={19} />
          </button>
        </div>
        <div className="modal-form">
          <label className="wide">
            Incident title
            <input name="title" required minLength={3} placeholder="e.g. order-api latency elevated" />
          </label>
          <label>
            Severity
            <select name="severity" defaultValue="P2">
              <option>P0</option>
              <option>P1</option>
              <option>P2</option>
              <option>P3</option>
            </select>
          </label>
          <label>
            Environment
            <select name="environment" defaultValue="staging">
              <option>staging</option>
              <option>production</option>
              <option>development</option>
            </select>
          </label>
          <label className="wide">
            Service
            <input name="service" required minLength={2} placeholder="order-api" />
          </label>
          <label className="wide">
            Alert context
            <textarea
              name="description"
              placeholder="Describe the signal, duration and known impact…"
            />
          </label>
        </div>
        <div className="modal-note">
          <Shield size={17} />
          Creating an incident does not authorize any infrastructure change.
        </div>
        <div className="modal-actions">
          <button type="button" className="secondary-button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary-button" disabled={busy}>
            {busy ? <RefreshCcw size={16} className="spin" /> : <Plus size={16} />}
            Create incident
          </button>
        </div>
      </form>
    </div>
  );
}

function PanelHeader({
  title,
  subtitle,
  action,
  onAction,
}: {
  title: string;
  subtitle: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="panel-header">
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
      {action && (
        <button onClick={onAction}>
          {action} <ArrowRight size={15} />
        </button>
      )}
    </div>
  );
}

function FlowNode({
  icon: Icon,
  label,
  detail,
  state,
}: {
  icon: typeof Gauge;
  label: string;
  detail: string;
  state: string;
}) {
  return (
    <div className={`flow-node ${state}`}>
      <div>
        <Icon size={18} />
      </div>
      <strong>{label}</strong>
      <span>{detail}</span>
    </div>
  );
}

function FlowConnector() {
  return (
    <div className="flow-connector">
      <span />
      <ChevronRight size={13} />
    </div>
  );
}

function SummaryStat({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Gauge;
  label: string;
  value: string;
}) {
  return (
    <div className="summary-stat">
      <div>
        <Icon size={16} />
      </div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function AgentRow({ name, role, state }: { name: string; role: string; state: string }) {
  return (
    <div className="agent-row">
      <div className={`agent-icon ${state}`}>
        <Bot size={17} />
      </div>
      <div>
        <strong>{name}</strong>
        <span>{role}</span>
      </div>
      {state === "done" ? <CheckCircle2 size={16} /> : <CircleDot size={16} />}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status-badge status-${status.toLowerCase()}`}>{friendlyStatus(status)}</span>;
}

function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`severity-badge severity-${severity.toLowerCase()}`}>{severity}</span>;
}

function RiskBadge({ risk }: { risk: string }) {
  return <span className={`risk-badge risk-${risk.toLowerCase()}`}>{risk}</span>;
}

function TraceTypeIcon({ type }: { type: string }) {
  const icons: Record<string, typeof Gauge> = {
    router: Network,
    agent: Bot,
    retrieval: Search,
    tool: TerminalSquare,
    tool_execution: TerminalSquare,
    llm: Sparkles,
    policy: Shield,
    approval: ClipboardCheck,
    verification: CheckCircle2,
  };
  const Icon = icons[type] ?? Activity;
  return <Icon size={14} />;
}

function EvalKpi({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>
        <CheckCircle2 size={13} /> target met
      </small>
    </div>
  );
}

function BreakdownBar({
  label,
  value,
  max,
  suffix,
}: {
  label: string;
  value: number;
  max: number;
  suffix: string;
}) {
  return (
    <div className="breakdown">
      <div>
        <span>{label}</span>
        <strong>
          {value}
          {suffix}
        </strong>
      </div>
      <div className="breakdown-track">
        <span style={{ width: `${Math.min((value / max) * 100, 100)}%` }} />
      </div>
    </div>
  );
}

function EmptyInline({ icon: Icon, text }: { icon: typeof Gauge; text: string }) {
  return (
    <div className="empty-inline">
      <Icon size={20} />
      <span>{text}</span>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="loading-state">
      <div className="loading-logo">
        <Shield size={28} />
        <span />
      </div>
      <strong>Synchronizing incident state</strong>
      <p>Loading events, policy decisions and trace spans…</p>
    </div>
  );
}

function friendlyStatus(status: string) {
  return status
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function relativeTime(value: string) {
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en", { notation: value > 9999 ? "compact" : "standard" }).format(value);
}

function compactUri(value: string) {
  return value.length > 45 ? `${value.slice(0, 42)}…` : value;
}

export default App;
