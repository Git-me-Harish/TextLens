import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";
import api from "../lib/api";
import { Badge, Spinner } from "../components/ui";
import { formatDistanceToNow } from "date-fns";
import {
  ArrowUpRight, FileText, Cpu, Activity, Zap,
  TrendingUp, HeartPulse, Scale, Truck, GraduationCap,
  Building2, LayoutDashboard, Layers, MessageSquare,
} from "lucide-react";

const STATUS_BADGE = { completed: "success", failed: "danger", processing: "processing", pending: "warning" };

const DOMAIN_META = {
  finance:    { label: "Finance",     icon: TrendingUp,    color: "#16a34a" },
  healthcare: { label: "Healthcare",  icon: HeartPulse,    color: "#2563eb" },
  legal:      { label: "Legal",       icon: Scale,         color: "#9333ea" },
  logistics:  { label: "Logistics",   icon: Truck,         color: "#d97706" },
  hr:         { label: "HR & Edu",    icon: GraduationCap, color: "#db2777" },
  government: { label: "Government",  icon: Building2,     color: "#0891b2" },
  general:    { label: "General",     icon: FileText,      color: "#64748b" },
};

// 7-day activity bar chart
function ActivityChart({ jobs }) {
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(); d.setDate(d.getDate() - (6 - i)); return d.toDateString();
  });
  const counts = days.map(day => jobs.filter(j => new Date(j.created_at).toDateString() === day).length);
  const max = Math.max(...counts, 1);
  return (
    <div>
      <div className="chart-bar-wrap">
        {counts.map((c, i) => (
          <div key={i} title={`${c} job${c !== 1 ? "s" : ""}`} className="chart-bar" style={{ height: `${Math.max(8, (c / max) * 100)}%` }} />
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
        {days.map((d, i) => (
          <span key={i} style={{ fontSize: "0.66rem", color: "var(--ink-muted)" }}>
            {new Date(d).toLocaleDateString("en", { weekday: "short" })}
          </span>
        ))}
      </div>
    </div>
  );
}

// Domain usage breakdown
function DomainBreakdown({ runs }) {
  if (!runs.length) return (
    <div style={{ textAlign: "center", color: "var(--ink-muted)", fontSize: "0.83rem", padding: "1.5rem 0" }}>
      No pipeline runs yet
    </div>
  );

  const counts = {};
  runs.forEach(r => { const d = r.domain || "general"; counts[d] = (counts[d] || 0) + 1; });
  const total = runs.length;
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {sorted.map(([domain, count]) => {
        const meta = DOMAIN_META[domain] || DOMAIN_META.general;
        const Icon = meta.icon;
        const pct = Math.round((count / total) * 100);
        return (
          <div key={domain}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <Icon size={13} style={{ color: meta.color, flexShrink: 0 }} />
              <span style={{ fontSize: "0.8rem", color: "var(--ink-secondary)", flex: 1 }}>{meta.label}</span>
              <span style={{ fontSize: "0.78rem", fontWeight: 600, color: "var(--ink)" }}>{count}</span>
              <span style={{ fontSize: "0.72rem", color: "var(--ink-muted)", minWidth: 32, textAlign: "right" }}>{pct}%</span>
            </div>
            <div style={{ height: 4, background: "var(--border)", borderRadius: 4 }}>
              <div style={{ height: "100%", width: `${pct}%`, background: meta.color, borderRadius: 4, transition: "width 0.4s ease" }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Quick action cards
function QuickAction({ to, icon: Icon, label, desc, accent }) {
  return (
    <Link to={to} style={{ textDecoration: "none" }}>
      <div className="card" style={{ padding: "1rem", display: "flex", alignItems: "center", gap: 12, cursor: "pointer", transition: "box-shadow 0.15s, transform 0.15s" }}
        onMouseEnter={e => { e.currentTarget.style.boxShadow = "0 4px 12px rgba(0,0,0,0.1)"; e.currentTarget.style.transform = "translateY(-1px)"; }}
        onMouseLeave={e => { e.currentTarget.style.boxShadow = ""; e.currentTarget.style.transform = ""; }}
      >
        <div style={{ width: 36, height: 36, borderRadius: 10, background: `${accent}18`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <Icon size={17} style={{ color: accent }} />
        </div>
        <div>
          <div style={{ fontWeight: 600, fontSize: "0.85rem", color: "var(--ink)" }}>{label}</div>
          <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>{desc}</div>
        </div>
      </div>
    </Link>
  );
}

// Main dashboard
export default function DashboardPage() {
  const { user } = useAuth();
  const [stats, setStats] = useState(null);
  const [recentJobs, setRecentJobs] = useState([]);
  const [recentAgents, setRecentAgents] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get("/users/me/stats"),
      api.get("/jobs?per_page=50"),
      api.get("/agents?per_page=20"),
    ]).then(([s, j, a]) => {
      setStats(s.data);
      setRecentJobs(j.data.jobs || []);
      setRecentAgents(a.data.runs || []);
    }).finally(() => setLoading(false));
  }, []);

  const successRate = stats?.total_jobs
    ? Math.round((stats.completed_jobs / stats.total_jobs) * 100)
    : 0;

  const recentFive = recentJobs.slice(0, 5);
  const recentAgentFive = recentAgents.slice(0, 5);

  return (
    <div>
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="page-title">{user?.full_name?.split(" ")[0]}'s workspace</h1>
          <p className="page-subtitle">Document intelligence command center</p>
        </div>
      </div>
      {loading ? (
        <div className="stats-row" style={{ marginBottom: "1.75rem" }}>
          {[1,2,3,4].map(i => <div key={i} className="stat-card skeleton" style={{ height: 88 }} />)}
        </div>
      ) : (
        <div className="stats-row" style={{ marginBottom: "1.75rem" }}>
          <div className="stat-card">
            <div className="stat-value">{stats?.total_jobs ?? 0}</div>
            <div className="stat-label">Total extractions</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{recentAgents.length > 0 ? recentAgents.length : 0}</div>
            <div className="stat-label">Agent runs</div>
          </div>
          <div className="stat-card">
            <div className="stat-value" style={{ color: successRate >= 80 ? "var(--success)" : successRate >= 50 ? "var(--warning)" : "var(--danger)" }}>
              {successRate}%
            </div>
            <div className="stat-label">Success rate</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{stats?.failed_jobs ?? 0}</div>
            <div className="stat-label">Failed jobs</div>
          </div>
        </div>
      )}
      <div style={{ marginBottom: "1.75rem" }}>
        <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-muted)", marginBottom: "0.75rem" }}>Quick actions</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: "0.75rem" }}>
          <QuickAction to="/pipelines" icon={Zap} label="Run Pipeline" desc="Upload + run domain agent" accent="var(--accent)" />
          <QuickAction to="/tools/pdf-extract" icon={FileText} label="Quick Extract" desc="Raw PDF/image text extraction" accent="#16a34a" />
          <QuickAction to="/tools/pdf-chat" icon={MessageSquare} label="PDF Chat" desc="Ask questions about a doc" accent="#9333ea" />
          <QuickAction to="/batch" icon={Layers} label="Batch Jobs" desc="Process multiple types of files" accent="#d97706" />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem", marginBottom: "1.5rem" }}>
        <div className="card" style={{ padding: "1.25rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: "1.25rem" }}>
            <Activity size={15} style={{ color: "var(--accent)" }} />
            <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>Extraction activity — 7 days</span>
          </div>
          {loading ? <div className="skeleton" style={{ height: 64 }} /> : <ActivityChart jobs={recentJobs} />}
        </div>

        <div className="card" style={{ padding: "1.25rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: "1.25rem" }}>
            <LayoutDashboard size={15} style={{ color: "var(--accent)" }} />
            <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>Pipeline runs by domain</span>
          </div>
          {loading ? <div className="skeleton" style={{ height: 80 }} /> : <DomainBreakdown runs={recentAgents} />}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem" }}>
        <div className="card" style={{ overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "1rem 1.25rem", borderBottom: "1px solid var(--border)" }}>
            <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>Recent extractions</span>
            <Link to="/history" style={{ fontSize: "0.78rem", color: "var(--accent)", display: "flex", alignItems: "center", gap: 4 }}>
              View all <ArrowUpRight size={12} />
            </Link>
          </div>
          {loading ? (
            <div style={{ padding: "1rem", display: "flex", flexDirection: "column", gap: 8 }}>
              {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: 36 }} />)}
            </div>
          ) : recentFive.length === 0 ? (
            <div style={{ padding: "2rem", textAlign: "center", color: "var(--ink-muted)", fontSize: "0.83rem" }}>No extractions yet</div>
          ) : (
            <table className="jobs-table">
              <thead><tr><th>File</th><th>Type</th><th>Status</th><th>When</th></tr></thead>
              <tbody>
                {recentFive.map(job => (
                  <tr key={job.id}>
                    <td style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{job.original_filename}</td>
                    <td><span style={{ fontFamily: "var(--font-mono)", fontSize: "0.72rem" }}>{job.job_type}</span></td>
                    <td><Badge variant={STATUS_BADGE[job.status] || "default"}>{job.status}</Badge></td>
                    <td style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>{formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card" style={{ overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "1rem 1.25rem", borderBottom: "1px solid var(--border)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Cpu size={15} style={{ color: "var(--accent)" }} />
              <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>Recent pipeline runs</span>
            </div>
            <Link to="/agent-history" style={{ fontSize: "0.78rem", color: "var(--accent)", display: "flex", alignItems: "center", gap: 4 }}>
              View all <ArrowUpRight size={12} />
            </Link>
          </div>
          {loading ? (
            <div style={{ padding: "1rem", display: "flex", flexDirection: "column", gap: 8 }}>
              {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: 36 }} />)}
            </div>
          ) : recentAgentFive.length === 0 ? (
            <div style={{ padding: "2rem", textAlign: "center" }}>
              <div style={{ color: "var(--ink-muted)", fontSize: "0.83rem", marginBottom: 8 }}>No pipeline runs yet</div>
              <Link to="/pipelines" style={{ fontSize: "0.8rem", color: "var(--accent)" }}>Run your first pipeline →</Link>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column" }}>
              {recentAgentFive.map((run, i) => {
                const meta = DOMAIN_META[run.domain] || DOMAIN_META.general;
                const Icon = meta.icon;
                return (
                  <div key={run.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "0.625rem 1.25rem", borderBottom: i < recentAgentFive.length - 1 ? "1px solid var(--border)" : "none" }}>
                    <div style={{ width: 28, height: 28, borderRadius: 8, background: `${meta.color}18`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                      <Icon size={13} style={{ color: meta.color }} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "0.82rem", fontWeight: 500, color: "var(--ink)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {run.pipeline_type?.replace(/_/g, " ")}
                      </div>
                      <div style={{ fontSize: "0.72rem", color: "var(--ink-muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {run.original_filename}
                      </div>
                    </div>
                    <Badge variant={run.status === "completed" ? "success" : run.status === "failed" ? "danger" : "processing"}>
                      {run.status}
                    </Badge>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
