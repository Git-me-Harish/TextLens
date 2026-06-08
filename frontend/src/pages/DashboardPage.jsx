import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";
import api from "../lib/api";
import { Badge, Spinner } from "../components/ui";
import { formatDistanceToNow } from "date-fns";
import { ArrowUpRight, FileText, Cpu, Activity } from "lucide-react";

const STATUS_BADGE = { completed: "success", failed: "danger", processing: "processing", pending: "warning" };

// Simple 7-day bar chart from job data
function ActivityChart({ jobs }) {
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    return d.toDateString();
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
      api.get("/agents?per_page=5"),
    ]).then(([s, j, a]) => {
      setStats(s.data);
      setRecentJobs(j.data.jobs);
      setRecentAgents(a.data.runs);
    }).finally(() => setLoading(false));
  }, []);

  const recentFive = recentJobs.slice(0, 5);

  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="page-title">
            {user?.full_name?.split(" ")[0]}'s workspace
          </h1>
          <p className="page-subtitle">Document intelligence command center</p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <Link to="/tools/pdf-extract" className="btn btn-outline btn-sm">
            <FileText size={14} /> Quick extract
          </Link>
          <Link to="/pipelines" className="btn btn-primary btn-sm">
            <Cpu size={14} /> Run pipeline
          </Link>
        </div>
      </div>

      {/* Stats */}
      {loading ? (
        <div className="stats-row" style={{ marginBottom: "2rem" }}>
          {[1,2,3,4].map(i => <div key={i} className="stat-card skeleton" style={{ height: 88 }} />)}
        </div>
      ) : (
        <div className="stats-row" style={{ marginBottom: "2rem" }}>
          <div className="stat-card">
            <div className="stat-value">{stats?.total_jobs ?? 0}</div>
            <div className="stat-label">Total jobs</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{stats?.completed_jobs ?? 0}</div>
            <div className="stat-label">Completed</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{recentAgents.length}</div>
            <div className="stat-label">Agent runs</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">
              {stats?.total_jobs ? Math.round((stats.completed_jobs / stats.total_jobs) * 100) : 0}%
            </div>
            <div className="stat-label">Success rate</div>
          </div>
        </div>
      )}

      {/* Two-column: activity + recent agents */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem", marginBottom: "1.5rem" }}>
        {/* Activity */}
        <div className="card" style={{ padding: "1.25rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: "1.25rem" }}>
            <Activity size={15} style={{ color: "var(--accent)" }} />
            <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>Activity — last 7 days</span>
          </div>
          {loading ? <div className="skeleton" style={{ height: 64 }} /> : <ActivityChart jobs={recentJobs} />}
        </div>

        {/* Recent agent runs */}
        <div className="card" style={{ padding: "1.25rem" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Cpu size={15} style={{ color: "var(--accent)" }} />
              <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>Recent pipeline runs</span>
            </div>
            <Link to="/history" style={{ fontSize: "0.78rem", color: "var(--accent)" }}>View all</Link>
          </div>
          {loading ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: 40 }} />)}
            </div>
          ) : recentAgents.length === 0 ? (
            <div className="empty-state" style={{ padding: "1.5rem 1rem" }}>
              <div className="empty-state-title">No pipeline runs yet</div>
              <div className="empty-state-desc">
                <Link to="/pipelines">Run a domain pipeline</Link> on extracted text
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {recentAgents.map(run => (
                <div key={run.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "0.5rem 0", borderBottom: "1px solid var(--border)" }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: "0.82rem", fontWeight: 500, color: "var(--ink)" }}>{run.pipeline_type.replace(/_/g, " ")}</div>
                    <div style={{ fontSize: "0.74rem", color: "var(--ink-muted)" }}>{run.original_filename}</div>
                  </div>
                  <Badge variant={run.status === "completed" ? "success" : run.status === "failed" ? "danger" : "processing"}>
                    {run.status}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Recent jobs table */}
      {recentFive.length > 0 && (
        <div className="card" style={{ overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "1rem 1.25rem", borderBottom: "1px solid var(--border)" }}>
            <span style={{ fontWeight: 600, fontSize: "0.875rem" }}>Recent extractions</span>
            <Link to="/history" style={{ fontSize: "0.78rem", color: "var(--accent)", display: "flex", alignItems: "center", gap: 4 }}>
              View all <ArrowUpRight size={12} />
            </Link>
          </div>
          <table className="jobs-table">
            <thead>
              <tr>
                <th>File</th>
                <th>Type</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {recentFive.map(job => (
                <tr key={job.id}>
                  <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{job.original_filename}</td>
                  <td><span style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>{job.job_type}</span></td>
                  <td><Badge variant={STATUS_BADGE[job.status] || "default"}>{job.status}</Badge></td>
                  <td style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>{formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
