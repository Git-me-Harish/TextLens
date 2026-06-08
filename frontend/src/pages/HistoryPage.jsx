import { useState, useEffect } from "react";
import { Trash2, Download, ChevronLeft, ChevronRight } from "lucide-react";
import toast from "react-hot-toast";
import { formatDistanceToNow } from "date-fns";
import api from "../lib/api";
import { Badge, Button, Spinner } from "../components/ui";

const STATUS_BADGE = { completed: "success", failed: "danger", processing: "processing", pending: "warning" };

export default function HistoryPage() {
  const [jobs, setJobs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const PER_PAGE = 15;

  const load = async (p = page) => {
    setLoading(true);
    try {
      const { data } = await api.get(`/jobs?page=${p}&per_page=${PER_PAGE}`);
      setJobs(data.jobs);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(page); }, [page]);

  const deleteJob = async (id) => {
    try {
      await api.delete(`/jobs/${id}`);
      toast.success("Job deleted");
      load(page);
    } catch { toast.error("Delete failed"); }
  };

  const totalPages = Math.ceil(total / PER_PAGE);

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">History</h1>
        <p className="page-subtitle">{total} total jobs</p>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        {loading ? (
          <div style={{ padding: "3rem", textAlign: "center" }}><Spinner /></div>
        ) : jobs.length === 0 ? (
          <div style={{ padding: "3rem", textAlign: "center", color: "var(--ink-muted)" }}>
            No jobs yet. Upload a file to get started.
          </div>
        ) : (
          <table className="jobs-table">
            <thead>
              <tr>
                <th>File</th>
                <th>Type</th>
                <th>Status</th>
                <th>Pages</th>
                <th>Duration</th>
                <th>Created</th>
                <th style={{ width: 80 }}></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map(job => (
                <tr key={job.id}>
                  <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={job.original_filename}>
                    {job.original_filename}
                  </td>
                  <td><span style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>{job.job_type}</span></td>
                  <td><Badge variant={STATUS_BADGE[job.status] || "default"}>{job.status}</Badge></td>
                  <td style={{ color: "var(--ink-muted)" }}>{job.page_count ?? "—"}</td>
                  <td style={{ color: "var(--ink-muted)", fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>
                    {job.processing_time_ms ? `${job.processing_time_ms}ms` : "—"}
                  </td>
                  <td style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>
                    {formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: 4 }}>
                      {job.result_file_path && (
                        <button onClick={() => window.open(`/api/jobs/${job.id}/download`, "_blank")}
                          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--accent)", padding: 4 }}>
                          <Download size={15} />
                        </button>
                      )}
                      <button onClick={() => deleteJob(job.id)}
                        style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)", padding: 4 }}>
                        <Trash2 size={15} />
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
          <button className="page-btn" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>
            <ChevronLeft size={15} />
          </button>
          {Array.from({ length: totalPages }, (_, i) => i + 1)
            .filter(p => p === 1 || p === totalPages || Math.abs(p - page) <= 1)
            .map((p, i, arr) => (
              <span key={p} style={{ display: "contents" }}>
                {i > 0 && arr[i - 1] !== p - 1 && <span style={{ color: "var(--ink-muted)" }}>...</span>}
                <button className={`page-btn ${p === page ? "active" : ""}`} onClick={() => setPage(p)}>{p}</button>
              </span>
            ))
          }
          <button className="page-btn" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}>
            <ChevronRight size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
