import { useState, useEffect, useCallback, Fragment } from "react";
import { Trash2, Download, ChevronLeft, ChevronRight, RotateCcw, AlertCircle } from "lucide-react";
import toast from "react-hot-toast";
import { formatDistanceToNow } from "date-fns";
import api, { errMsg } from "../lib/api";
import { Badge, Spinner } from "../components/ui";

const STATUS_BADGE = { completed: "success", failed: "danger", processing: "processing", pending: "warning" };

function ExpandedRow({ job, onRetry, colSpan }) {
  if (job.status === "failed") {
    return (
      <tr>
        <td colSpan={colSpan} style={{ padding: "0.75rem 1rem 1rem", background: "var(--danger-light)" }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <AlertCircle size={15} style={{ color: "var(--danger)", flexShrink: 0, marginTop: 1 }} />
              <div>
                <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "var(--danger)", marginBottom: 2 }}>Processing failed</div>
                <div style={{ fontSize: "0.76rem", color: "var(--danger)", fontFamily: "var(--font-mono)", opacity: 0.85 }}>
                  {job.error_message || "Unknown error"}
                </div>
              </div>
            </div>
            <button onClick={() => onRetry(job.id)} className="btn btn-sm" style={{ flexShrink: 0, borderColor: "var(--danger)", color: "var(--danger)", background: "none", border: "1px solid" }}>
              <RotateCcw size={12} /> Retry
            </button>
          </div>
        </td>
      </tr>
    );
  }
  if (job.status === "completed" && job.result_text) {
    return (
      <tr>
        <td colSpan={colSpan} style={{ padding: "0.75rem 1rem 1rem", background: "var(--paper)" }}>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "0.76rem", color: "var(--ink-secondary)", lineHeight: 1.6, maxHeight: 120, overflow: "hidden" }}>
            {job.result_text.slice(0, 400)}{job.result_text.length > 400 ? "..." : ""}
          </div>
        </td>
      </tr>
    );
  }
  return null;
}

export default function HistoryPage() {
  const [jobs, setJobs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const PER_PAGE = 15;

  const load = useCallback(async (p = 1) => {
    setLoading(true);
    try {
      const { data } = await api.get(`/jobs?page=${p}&per_page=${PER_PAGE}`);
      setJobs(data.jobs);
      setTotal(data.total);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(page); }, [page]);

  const deleteJob = async (id) => {
    try {
      await api.delete(`/jobs/${id}`);
      toast.success("Deleted");
      load(page);
    } catch { toast.error("Delete failed"); }
  };

  const retryJob = async (id) => {
    try {
      await api.post(`/jobs/${id}/retry`);
      toast.success("Job re-queued");
      setTimeout(() => load(page), 1500);
    } catch (err) { toast.error(errMsg(err, "Retry failed")); }
  };

  const toggleExpand = (id) => setExpandedId(prev => prev === id ? null : id);
  const totalPages = Math.ceil(total / PER_PAGE);
  const COL = 7;

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Extraction History</h1>
        <p className="page-subtitle">{total} job{total !== 1 ? "s" : ""} total</p>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        {loading ? (
          <div style={{ padding: "3rem", textAlign: "center" }}><Spinner /></div>
        ) : jobs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-title">No extractions yet</div>
            <div className="empty-state-desc">Upload a file from any tool to get started</div>
          </div>
        ) : (
          <table className="jobs-table">
            <thead>
              <tr>
                <th>File</th><th>Type</th><th>Status</th>
                <th>Pages</th><th>Duration</th><th>Created</th>
                <th style={{ width: 96 }}></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map(job => (
                // key on Fragment fixes the React warning
                <Fragment key={job.id}>
                  <tr onClick={() => toggleExpand(job.id)} style={{ cursor: "pointer", background: expandedId === job.id ? "var(--paper)" : undefined }}>
                    <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={job.original_filename}>
                      {job.original_filename}
                    </td>
                    <td><span style={{ fontFamily: "var(--font-mono)", fontSize: "0.74rem" }}>{job.job_type}</span></td>
                    <td onClick={e => e.stopPropagation()}>
                      <Badge variant={STATUS_BADGE[job.status] || "default"}>{job.status}</Badge>
                    </td>
                    <td style={{ color: "var(--ink-muted)" }}>{job.page_count ?? "—"}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem", color: "var(--ink-muted)" }}>
                      {job.processing_time_ms ? `${job.processing_time_ms}ms` : "—"}
                    </td>
                    <td style={{ fontSize: "0.76rem", color: "var(--ink-muted)" }}>
                      {formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}
                    </td>
                    <td onClick={e => e.stopPropagation()}>
                      <div style={{ display: "flex", gap: 2 }}>
                        {job.status === "failed" && (
                          <button onClick={() => retryJob(job.id)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--warning)", padding: 4 }} title="Retry">
                            <RotateCcw size={14} />
                          </button>
                        )}
                        {job.result_file_path && (
                          <button onClick={() => window.open(`/api/jobs/${job.id}/download`, "_blank")} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--accent)", padding: 4 }}>
                            <Download size={14} />
                          </button>
                        )}
                        <button onClick={() => deleteJob(job.id)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)", padding: 4 }}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expandedId === job.id && <ExpandedRow job={job} onRetry={retryJob} colSpan={COL} />}
                </Fragment>
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
                {i > 0 && arr[i - 1] !== p - 1 && <span style={{ color: "var(--ink-muted)" }}>...</span>}
                <button className={`page-btn ${p === page ? "active" : ""}`} onClick={() => setPage(p)}>{p}</button>
              </span>
            ))}
          <button className="page-btn" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}><ChevronRight size={14} /></button>
        </div>
      )}
    </div>
  );
}