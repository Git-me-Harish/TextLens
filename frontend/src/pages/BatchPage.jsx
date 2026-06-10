import { useState, useEffect, useRef } from "react";
import { useDropzone } from "react-dropzone";
import {
  Upload, Layers, CheckCircle2, XCircle, Clock,
  Download, RefreshCw, Trash2, ChevronDown, ChevronRight,
  FileText, AlertTriangle,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { Button, Spinner, Badge } from "../components/ui";
import { formatDistanceToNow } from "date-fns";

/* ─────────────────────────────── helpers ─────────────────────────── */

const STATUS_COLOR = {
  pending:    "var(--ink-muted)",
  processing: "var(--accent)",
  completed:  "var(--success)",
  failed:     "var(--danger)",
  partial:    "var(--warning)",
};

const STATUS_ICON = {
  pending:    <Clock size={14} />,
  processing: <RefreshCw size={14} className="spinning" />,
  completed:  <CheckCircle2 size={14} />,
  failed:     <XCircle size={14} />,
  partial:    <AlertTriangle size={14} />,
};

function ProgressBar({ value, total, color }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>
          {value} / {total} files
        </span>
        <span style={{ fontSize: "0.75rem", fontWeight: 600, color }}>{pct}%</span>
      </div>
      <div style={{ height: 6, background: "var(--paper-2)", borderRadius: 99, overflow: "hidden" }}>
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background: color,
            borderRadius: 99,
            transition: "width 0.4s ease",
          }}
        />
      </div>
    </div>
  );
}

function BatchCard({ batch, onDelete, onDownload }) {
  const [expanded, setExpanded] = useState(false);
  const color = STATUS_COLOR[batch.status] || "var(--ink-muted)";
  const isDone = ["completed", "failed", "partial"].includes(batch.status);

  return (
    <div className="card" style={{ overflow: "hidden", marginBottom: "1rem" }}>
      {/* Header row */}
      <div
        style={{
          padding: "1rem 1.25rem",
          display: "flex",
          alignItems: "center",
          gap: 12,
          cursor: "pointer",
          borderBottom: expanded ? "1px solid var(--border)" : "none",
        }}
        onClick={() => setExpanded((e) => !e)}
      >
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: 10,
            background: "var(--paper-2)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color,
            flexShrink: 0,
          }}
        >
          <Layers size={16} />
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontWeight: 600,
              fontSize: "0.875rem",
              color: "var(--ink)",
              marginBottom: 2,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {batch.name}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>
            {batch.pipeline_type.replace(/_/g, " ")} ·{" "}
            {formatDistanceToNow(new Date(batch.created_at), { addSuffix: true })}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ color, display: "flex", alignItems: "center", gap: 4, fontSize: "0.8rem" }}>
            {STATUS_ICON[batch.status]}
            <span style={{ textTransform: "capitalize" }}>{batch.status}</span>
          </div>

          {isDone && (
            <>
              {batch.status !== "failed" && (
                <button
                  onClick={(e) => { e.stopPropagation(); onDownload(batch.id); }}
                  className="btn btn-outline btn-sm"
                  title="Download results"
                >
                  <Download size={13} />
                </button>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(batch.id); }}
                className="btn btn-ghost btn-sm"
                title="Delete batch"
                style={{ color: "var(--danger)" }}
              >
                <Trash2 size={13} />
              </button>
            </>
          )}

          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </div>

      {/* Progress */}
      {(batch.status === "processing" || batch.status === "pending") && (
        <div style={{ padding: "0.875rem 1.25rem", borderBottom: "1px solid var(--border)" }}>
          <ProgressBar
            value={batch.completed_files}
            total={batch.total_files}
            color="var(--accent)"
          />
        </div>
      )}

      {/* Expanded items */}
      {expanded && batch.items && batch.items.length > 0 && (
        <div style={{ maxHeight: 320, overflowY: "auto" }}>
          {batch.items.map((item) => (
            <div
              key={item.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "0.625rem 1.25rem",
                borderBottom: "1px solid var(--border)",
              }}
            >
              <FileText size={13} style={{ color: "var(--ink-muted)", flexShrink: 0 }} />
              <span
                style={{
                  flex: 1,
                  fontSize: "0.82rem",
                  color: "var(--ink-secondary)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {item.original_filename}
              </span>
              <div
                style={{
                  color: STATUS_COLOR[item.status],
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: "0.75rem",
                  flexShrink: 0,
                }}
              >
                {STATUS_ICON[item.status]}
                <span style={{ textTransform: "capitalize" }}>{item.status}</span>
              </div>
              {item.error_message && (
                <span
                  title={item.error_message}
                  style={{ color: "var(--danger)", fontSize: "0.72rem", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis" }}
                >
                  {item.error_message}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Aggregate stats when done */}
      {isDone && (
        <div
          style={{
            padding: "0.75rem 1.25rem",
            display: "flex",
            gap: 24,
            background: "var(--paper)",
            fontSize: "0.8rem",
          }}
        >
          <span style={{ color: "var(--success)" }}>✓ {batch.completed_files} completed</span>
          {batch.failed_files > 0 && (
            <span style={{ color: "var(--danger)" }}>✗ {batch.failed_files} failed</span>
          )}
          <span style={{ color: "var(--ink-muted)" }}>{batch.total_files} total</span>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────── page ────────────────────────────── */

export default function BatchPage() {
  const [catalog, setCatalog] = useState(null);
  const [batches, setBatches] = useState([]);
  const [loading, setLoading] = useState(true);

  // New batch form state
  const [files, setFiles] = useState([]);
  const [domain, setDomain] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [batchName, setBatchName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Polling refs
  const pollingRef = useRef(null);

  const loadBatches = async () => {
    try {
      const { data } = await api.get("/batch?per_page=30");
      setBatches(data.batches);
    } catch {
      /* silent */
    }
  };

  useEffect(() => {
    Promise.all([
      api.get("/agents/catalog"),
      api.get("/batch?per_page=30"),
    ]).then(([c, b]) => {
      setCatalog(c.data);
      setBatches(b.data.batches);
    }).finally(() => setLoading(false));
  }, []);

  // Poll for active batches
  useEffect(() => {
    const hasActive = batches.some(
      (b) => b.status === "processing" || b.status === "pending"
    );

    if (hasActive) {
      pollingRef.current = setInterval(loadBatches, 3000);
    } else {
      clearInterval(pollingRef.current);
    }

    return () => clearInterval(pollingRef.current);
  }, [batches]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: (accepted) => setFiles((prev) => [...prev, ...accepted]),
    accept: {
      "application/pdf": [],
      "image/jpeg": [],
      "image/png": [],
      "image/webp": [],
      "image/tiff": [],
      "application/zip": [],
    },
    multiple: true,
  });

  const removeFile = (idx) => setFiles((f) => f.filter((_, i) => i !== idx));

  const submitBatch = async () => {
    if (!files.length) return toast.error("Add at least one file");
    if (!domain || !pipeline) return toast.error("Select domain and pipeline");

    setSubmitting(true);
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("name", batchName || `Batch — ${pipeline.replace(/_/g, " ")}`);
    form.append("domain", domain);
    form.append("pipeline_type", pipeline);
    form.append("user_instructions", instructions);

    try {
      const { data } = await api.post("/batch", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success(`Batch started — ${data.total_files} files queued`);
      setBatches((prev) => [data, ...prev]);
      setFiles([]);
      setDomain("");
      setPipeline("");
      setBatchName("");
      setInstructions("");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to start batch");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id) => {
    if (!confirm("Delete this batch job?")) return;
    try {
      await api.delete(`/batch/${id}`);
      setBatches((prev) => prev.filter((b) => b.id !== id));
      toast.success("Batch deleted");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Delete failed");
    }
  };

  const handleDownload = (id) => {
    window.open(`/api/batch/${id}/results`, "_blank");
  };

  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", paddingTop: "4rem" }}>
        <Spinner size={32} />
      </div>
    );
  }

  const pipelines = domain && catalog?.[domain]?.pipelines
    ? Object.entries(catalog[domain].pipelines)
    : [];

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <h1 className="page-title">Batch Processing</h1>
        <p className="page-subtitle">
          Process up to 50 files in parallel — ZIP upload supported
        </p>
      </div>

      {/* New batch form */}
      <div className="card" style={{ padding: "1.5rem", marginBottom: "1.75rem" }}>
        <h2 style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "1.25rem" }}>
          New batch job
        </h2>

        {/* File drop */}
        <div
          {...getRootProps()}
          className={`dropzone ${isDragActive ? "active" : ""}`}
          style={{ marginBottom: "1.25rem" }}
        >
          <input {...getInputProps()} />
          {isDragActive ? (
            <div className="dropzone-title">Drop files here...</div>
          ) : (
            <>
              <div className="dropzone-icon">
                <Upload size={24} />
              </div>
              <div className="dropzone-title">Drop files or a ZIP archive</div>
              <div className="dropzone-sub">
                PDF, JPG, PNG, WEBP, TIFF — or a ZIP of any combination — max 50 files
              </div>
            </>
          )}
        </div>

        {/* File list */}
        {files.length > 0 && (
          <div
            style={{
              marginBottom: "1.25rem",
              maxHeight: 180,
              overflowY: "auto",
              border: "1px solid var(--border)",
              borderRadius: 8,
            }}
          >
            {files.map((f, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "0.5rem 0.875rem",
                  borderBottom: i < files.length - 1 ? "1px solid var(--border)" : "none",
                }}
              >
                <FileText size={13} style={{ color: "var(--ink-muted)", flexShrink: 0 }} />
                <span
                  style={{
                    flex: 1,
                    fontSize: "0.82rem",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {f.name}
                </span>
                <span style={{ fontSize: "0.72rem", color: "var(--ink-muted)" }}>
                  {(f.size / 1024).toFixed(0)} KB
                </span>
                <button
                  onClick={() => removeFile(i)}
                  style={{ background: "none", border: "none", cursor: "pointer", padding: 2, color: "var(--ink-muted)" }}
                >
                  <XCircle size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Config row */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "1rem", marginBottom: "1rem" }}>
          <div>
            <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>
              Batch name
            </label>
            <input
              className="form-input"
              placeholder="e.g. Q4 Invoices"
              value={batchName}
              onChange={(e) => setBatchName(e.target.value)}
            />
          </div>

          <div>
            <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>
              Domain *
            </label>
            <select
              className="form-input"
              value={domain}
              onChange={(e) => { setDomain(e.target.value); setPipeline(""); }}
            >
              <option value="">Select domain...</option>
              {catalog && Object.entries(catalog).map(([k, v]) => (
                <option key={k} value={k}>{v.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>
              Pipeline *
            </label>
            <select
              className="form-input"
              value={pipeline}
              onChange={(e) => setPipeline(e.target.value)}
              disabled={!domain}
            >
              <option value="">Select pipeline...</option>
              {pipelines.map(([k, p]) => (
                <option key={k} value={k}>{p.label}</option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ marginBottom: "1rem" }}>
          <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>
            Instructions <span style={{ fontWeight: 400 }}>(optional)</span>
          </label>
          <textarea
            className="form-input"
            placeholder="Any specific extraction focus, context, or formatting instructions..."
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={2}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: "0.8rem", color: "var(--ink-muted)" }}>
            {files.length > 0 ? `${files.length} file${files.length > 1 ? "s" : ""} ready` : "No files selected"}
          </span>
          <Button
            onClick={submitBatch}
            loading={submitting}
            disabled={submitting || !files.length || !domain || !pipeline}
          >
            <Layers size={14} />
            Start batch
          </Button>
        </div>
      </div>

      {/* Existing batches */}
      <div>
        <h2 style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "1rem" }}>
          Batch history
        </h2>

        {batches.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-title">No batch jobs yet</div>
            <div className="empty-state-desc">
              Upload multiple files above to process them all through the same pipeline.
            </div>
          </div>
        ) : (
          batches.map((batch) => (
            <BatchCard
              key={batch.id}
              batch={batch}
              onDelete={handleDelete}
              onDownload={handleDownload}
            />
          ))
        )}
      </div>
    </div>
  );
}