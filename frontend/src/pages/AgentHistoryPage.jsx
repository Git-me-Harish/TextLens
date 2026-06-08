import { useState, useEffect, useCallback } from "react";
import { Trash2, Download, Eye, ChevronLeft, ChevronRight, X, AlertTriangle } from "lucide-react";
import toast from "react-hot-toast";
import { formatDistanceToNow } from "date-fns";
import api from "../lib/api";
import { Badge, Spinner } from "../components/ui";

const STATUS_BADGE = { completed: "success", failed: "danger", running: "processing", pending: "warning" };

const DOMAINS = ["finance", "healthcare", "legal", "logistics", "hr", "general"];

// Inline drawer to view full agent result
function ResultDrawer({ run, onClose }) {
  const [activeTab, setActiveTab] = useState("structured");
  const [copied, setCopied] = useState(false);

  if (!run) return null;

  const copyJSON = () => {
    navigator.clipboard.writeText(JSON.stringify(run.structured_result, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const exportCSV = () => window.open(`/api/export/agent/${run.id}/csv`, "_blank");
  const exportExcel = () => window.open(`/api/export/agent/${run.id}/excel`, "_blank");

  // Recursive structured renderer
  function Field({ label, value }) {
    if (value === null || value === undefined) return null;
    if (typeof value === "object" && !Array.isArray(value) && value.severity) {
      return (
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--ink-muted)", marginBottom: 4 }}>{label}</div>
          <div className={`risk-flag ${value.severity}`}>
            <AlertTriangle size={13} style={{ flexShrink: 0 }} />
            <span>{value.issue || JSON.stringify(value)}</span>
          </div>
        </div>
      );
    }
    if (Array.isArray(value)) {
      if (value.length === 0) return null;
      if (typeof value[0] === "string") {
        return (
          <div className="result-field">
            <span className="result-field-key">{label.replace(/_/g, " ")}</span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
              {value.map((v, i) => (
                <span key={i} style={{ background: "var(--paper-2)", border: "1px solid var(--border)", borderRadius: 6, padding: "2px 8px", fontSize: "0.76rem" }}>{v}</span>
              ))}
            </div>
          </div>
        );
      }
      return (
        <div className="result-field">
          <span className="result-field-key">{label.replace(/_/g, " ")} ({value.length})</span>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
            {value.map((item, i) => (
              <div key={i} style={{ background: "var(--paper)", border: "1px solid var(--border)", borderRadius: 8, padding: "0.75rem", fontSize: "0.82rem" }}>
                {typeof item === "object" ? Object.entries(item).map(([k, v]) => (
                  <div key={k} style={{ display: "flex", gap: 8, marginBottom: 3 }}>
                    <span style={{ color: "var(--ink-muted)", minWidth: 120, fontSize: "0.75rem", fontWeight: 600 }}>{k.replace(/_/g, " ")}</span>
                    <span style={{ color: "var(--ink-secondary)" }}>{String(v ?? "—")}</span>
                  </div>
                )) : String(item)}
              </div>
            ))}
          </div>
        </div>
      );
    }
    if (typeof value === "object") {
      return (
        <div className="result-field">
          <span className="result-field-key">{label.replace(/_/g, " ")}</span>
          <div style={{ background: "var(--paper)", border: "1px solid var(--border)", borderRadius: 8, padding: "0.75rem", marginTop: 4 }}>
            {Object.entries(value).map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 8, marginBottom: 3, fontSize: "0.82rem" }}>
                <span style={{ color: "var(--ink-muted)", minWidth: 120, fontSize: "0.75rem", fontWeight: 600 }}>{k.replace(/_/g, " ")}</span>
                <span style={{ color: "var(--ink-secondary)" }}>{String(v ?? "—")}</span>
              </div>
            ))}
          </div>
        </div>
      );
    }
    return (
      <div className="result-field">
        <span className="result-field-key">{label.replace(/_/g, " ")}</span>
        <span className="result-field-value">{String(value)}</span>
      </div>
    );
  }

  return (
    <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: "min(520px, 100vw)", background: "#fff", boxShadow: "var(--shadow-lg)", zIndex: 300, display: "flex", flexDirection: "column", borderLeft: "1px solid var(--border)" }}>
      {/* Drawer header */}
      <div style={{ padding: "1.25rem 1.5rem", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{run.pipeline_type.replace(/_/g, " ")}</div>
          <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)", marginTop: 2 }}>{run.original_filename}</div>
        </div>
        <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", padding: 4 }}><X size={18} /></button>
      </div>

      {/* Summary */}
      {run.summary && (
        <div style={{ padding: "1rem 1.5rem", background: "var(--paper)", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-muted)", marginBottom: 6 }}>AI Summary</div>
          <p style={{ fontSize: "0.855rem", color: "var(--ink-secondary)", lineHeight: 1.6, marginBottom: 10 }}>{run.summary}</p>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ flex: 1, height: 5, background: "var(--paper-3)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${run.confidence_score}%`, background: run.confidence_score >= 80 ? "var(--success)" : run.confidence_score >= 50 ? "var(--warning)" : "var(--danger)", borderRadius: 3 }} />
            </div>
            <span style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--ink-muted)" }}>{run.confidence_score}% confidence</span>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div style={{ padding: "0 1.5rem", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <div className="tabs" style={{ marginBottom: 0 }}>
          {["structured", "raw"].map(t => (
            <button key={t} className={`tab ${activeTab === t ? "active" : ""}`} onClick={() => setActiveTab(t)}>
              {t === "structured" ? "Extracted Fields" : "Raw JSON"}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "1.25rem 1.5rem" }}>
        {activeTab === "structured" && run.structured_result && (
          <div className="result-field-grid">
            {Object.entries(run.structured_result).map(([k, v]) => <Field key={k} label={k} value={v} />)}
          </div>
        )}
        {activeTab === "raw" && (
          <div className="result-raw" style={{ maxHeight: "none" }}>{JSON.stringify(run.structured_result, null, 2)}</div>
        )}
      </div>

      {/* Export bar */}
      <div style={{ padding: "1rem 1.5rem", borderTop: "1px solid var(--border)", display: "flex", gap: 8, flexShrink: 0 }}>
        <button onClick={copyJSON} className="btn btn-ghost btn-sm" style={{ flex: 1 }}>{copied ? "Copied!" : "Copy JSON"}</button>
        <button onClick={exportCSV} className="btn btn-outline btn-sm" style={{ flex: 1 }}>
          <Download size={13} /> CSV
        </button>
        <button onClick={exportExcel} className="btn btn-primary btn-sm" style={{ flex: 1 }}>
          <Download size={13} /> Excel
        </button>
      </div>
    </div>
  );
}

export default function AgentHistoryPage() {
  const [runs, setRuns] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [domainFilter, setDomainFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedRun, setSelectedRun] = useState(null);
  const PER_PAGE = 15;

  const load = useCallback(async (p = page) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: p, per_page: PER_PAGE });
      if (domainFilter) params.append("domain", domainFilter);
      if (statusFilter) params.append("status", statusFilter);
      const { data } = await api.get(`/agents?${params}`);
      setRuns(data.runs);
      setTotal(data.total);
    } finally { setLoading(false); }
  }, [page, domainFilter, statusFilter]);

  useEffect(() => { load(page); }, [page, domainFilter, statusFilter]);

  const deleteRun = async (id) => {
    try {
      await api.delete(`/agents/${id}`);
      toast.success("Deleted");
      load(page);
      if (selectedRun?.id === id) setSelectedRun(null);
    } catch { toast.error("Delete failed"); }
  };

  const totalPages = Math.ceil(total / PER_PAGE);

  return (
    <>
      <div style={{ marginRight: selectedRun ? "520px" : 0, transition: "margin 0.2s ease" }}>
        <div className="page-header">
          <h1 className="page-title">Pipeline History</h1>
          <p className="page-subtitle">{total} agent run{total !== 1 ? "s" : ""}</p>
        </div>

        {/* Filters */}
        <div style={{ display: "flex", gap: 10, marginBottom: "1.25rem", flexWrap: "wrap" }}>
          <select
            value={domainFilter}
            onChange={e => { setDomainFilter(e.target.value); setPage(1); }}
            className="form-input"
            style={{ width: "auto", minWidth: 150 }}
          >
            <option value="">All domains</option>
            {DOMAINS.map(d => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
          </select>
          <select
            value={statusFilter}
            onChange={e => { setStatusFilter(e.target.value); setPage(1); }}
            className="form-input"
            style={{ width: "auto", minWidth: 140 }}
          >
            <option value="">All statuses</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="running">Running</option>
          </select>
          {(domainFilter || statusFilter) && (
            <button className="btn btn-ghost btn-sm" onClick={() => { setDomainFilter(""); setStatusFilter(""); setPage(1); }}>
              Clear filters
            </button>
          )}
        </div>

        <div className="card" style={{ overflow: "hidden" }}>
          {loading ? (
            <div style={{ padding: "3rem", textAlign: "center" }}><Spinner /></div>
          ) : runs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-title">No pipeline runs found</div>
              <div className="empty-state-desc">Run a domain pipeline from the Intelligence section</div>
            </div>
          ) : (
            <table className="jobs-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Domain</th>
                  <th>Pipeline</th>
                  <th>Status</th>
                  <th>Confidence</th>
                  <th>Duration</th>
                  <th>Created</th>
                  <th style={{ width: 100 }}></th>
                </tr>
              </thead>
              <tbody>
                {runs.map(run => (
                  <tr key={run.id} style={{ cursor: "pointer" }} onClick={() => setSelectedRun(run)}>
                    <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{run.original_filename}</td>
                    <td><span style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>{run.domain}</span></td>
                    <td style={{ fontSize: "0.8rem" }}>{run.pipeline_type.replace(/_/g, " ")}</td>
                    <td onClick={e => e.stopPropagation()}>
                      <Badge variant={STATUS_BADGE[run.status] || "default"}>{run.status}</Badge>
                    </td>
                    <td>
                      {run.confidence_score != null ? (
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <div style={{ width: 40, height: 4, background: "var(--paper-3)", borderRadius: 2, overflow: "hidden" }}>
                            <div style={{ height: "100%", width: `${run.confidence_score}%`, background: run.confidence_score >= 80 ? "var(--success)" : "var(--warning)" }} />
                          </div>
                          <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>{run.confidence_score}%</span>
                        </div>
                      ) : "—"}
                    </td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem", color: "var(--ink-muted)" }}>
                      {run.processing_time_ms ? `${run.processing_time_ms}ms` : "—"}
                    </td>
                    <td style={{ fontSize: "0.76rem", color: "var(--ink-muted)" }}>
                      {formatDistanceToNow(new Date(run.created_at), { addSuffix: true })}
                    </td>
                    <td onClick={e => e.stopPropagation()}>
                      <div style={{ display: "flex", gap: 4 }}>
                        <button onClick={() => setSelectedRun(run)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--accent)", padding: 4 }}>
                          <Eye size={14} />
                        </button>
                        {run.structured_result && (
                          <button onClick={() => window.open(`/api/export/agent/${run.id}/excel`, "_blank")} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--success)", padding: 4 }}>
                            <Download size={14} />
                          </button>
                        )}
                        <button onClick={() => deleteRun(run.id)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)", padding: 4 }}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {totalPages > 1 && (
          <div className="pagination">
            <button className="page-btn" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}><ChevronLeft size={14} /></button>
            {Array.from({ length: totalPages }, (_, i) => i + 1)
              .filter(p => p === 1 || p === totalPages || Math.abs(p - page) <= 1)
              .map((p, i, arr) => (
                <span key={p} style={{ display: "contents" }}>
                  {i > 0 && arr[i - 1] !== p - 1 && <span style={{ color: "var(--ink-muted)", fontSize: "0.85rem" }}>...</span>}
                  <button className={`page-btn ${p === page ? "active" : ""}`} onClick={() => setPage(p)}>{p}</button>
                </span>
              ))}
            <button className="page-btn" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}><ChevronRight size={14} /></button>
          </div>
        )}
      </div>

      {/* Overlay */}
      {selectedRun && <div onClick={() => setSelectedRun(null)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.2)", zIndex: 250 }} />}
      <ResultDrawer run={selectedRun} onClose={() => setSelectedRun(null)} />
    </>
  );
}