import { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import {
  FileText, Hash, Layers, Scissors, Minimize2,
  Image as ImageIcon, Download, RotateCcw, Check,
  Upload, X, ChevronRight,
} from "lucide-react";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Spinner } from "../components/ui";
import { waitForJobSSE } from "../hooks/useSSE";

/*  Action definitions  */

const ACTIONS = [
  {
    id:          "pdf_to_word",
    label:       "PDF → Word",
    badge:       ".docx",
    desc:        "Convert PDF to an editable Microsoft Word document",
    icon:        FileText,
    color:       "#2563eb",
    bg:          "#eff6ff",
    accept:      { "application/pdf": [] },
    acceptLabel: "PDF only",
    multi:       false,
    endpoint:    "jobs",
  },
  {
    id:          "pdf_to_markdown",
    label:       "PDF → Markdown",
    badge:       ".md",
    desc:        "Convert PDF structure to clean, portable Markdown format",
    icon:        Hash,
    color:       "#7c3aed",
    bg:          "#f5f3ff",
    accept:      { "application/pdf": [] },
    acceptLabel: "PDF only",
    multi:       false,
    endpoint:    "jobs",
  },
  {
    id:          "pdf_merge",
    label:       "Merge PDFs",
    badge:       "Combine",
    desc:        "Join 2–10 PDF files into a single document in upload order",
    icon:        Layers,
    color:       "#059669",
    bg:          "#ecfdf5",
    accept:      { "application/pdf": [] },
    acceptLabel: "2–10 PDF files",
    multi:       true,
    minFiles:    2,
    endpoint:    "studio/merge",
  },
  {
    id:          "pdf_split",
    label:       "Split PDF",
    badge:       "Pages",
    desc:        "Extract a specific page range from any PDF",
    icon:        Scissors,
    color:       "#d97706",
    bg:          "#fffbeb",
    accept:      { "application/pdf": [] },
    acceptLabel: "PDF only",
    multi:       false,
    hasPageRange: true,
    endpoint:    "studio/split",
  },
  {
    id:          "pdf_compress",
    label:       "Compress PDF",
    badge:       "Smaller",
    desc:        "Reduce file size with lossless compression — no quality loss",
    icon:        Minimize2,
    color:       "#dc2626",
    bg:          "#fef2f2",
    accept:      { "application/pdf": [] },
    acceptLabel: "PDF only",
    multi:       false,
    endpoint:    "jobs",
  },
  {
    id:          "images_to_pdf",
    label:       "Images → PDF",
    badge:       "Combine",
    desc:        "Combine one or more images into a single multi-page PDF",
    icon:        ImageIcon,
    color:       "#0891b2",
    bg:          "#ecfeff",
    accept:      { "image/jpeg": [], "image/png": [], "image/tiff": [], "image/webp": [] },
    acceptLabel: "JPG, PNG, TIFF, WEBP (1–10)",
    multi:       true,
    minFiles:    1,
    endpoint:    "studio/combine",
  },
];

/* Action card */
function ActionCard({ action, selected, onSelect }) {
  const Icon = action.icon;
  return (
    <div
      onClick={() => onSelect(action)}
      style={{
        padding: "1rem 1.1rem",
        borderRadius: 12,
        border: `2px solid ${selected ? action.color : "var(--border)"}`,
        background: selected ? action.bg : "var(--paper)",
        cursor: "pointer",
        transition: "all 0.15s",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{
          width: 36, height: 36, borderRadius: 9,
          background: selected ? action.color : action.bg,
          display: "flex", alignItems: "center", justifyContent: "center",
          transition: "all 0.15s",
        }}>
          <Icon size={16} color={selected ? "#fff" : action.color} />
        </div>
        <span style={{
          fontSize: "0.68rem", fontWeight: 700,
          textTransform: "uppercase", letterSpacing: "0.06em",
          color: action.color, background: action.bg,
          padding: "2px 7px", borderRadius: 20,
        }}>
          {action.badge}
        </span>
      </div>
      <div>
        <div style={{ fontWeight: 700, fontSize: "0.88rem", color: "var(--ink)", marginBottom: 3 }}>
          {action.label}
        </div>
        <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)", lineHeight: 1.5 }}>
          {action.desc}
        </div>
      </div>
    </div>
  );
}

/* Result panel */
function ResultPanel({ job, action, onReset }) {
  const [downloading, setDownloading] = useState(false);

  const download = async () => {
    if (!job.result_file_path && !job.id) return;
    setDownloading(true);
    try {
      // Redirect to presigned URL
      window.open(`/api/v1/jobs/${job.id}/download`, "_blank");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="card" style={{ padding: "1.5rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: "1.25rem" }}>
        <div style={{
          width: 44, height: 44, borderRadius: 12,
          background: action.bg,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <Check size={20} color={action.color} />
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: "1rem", color: "var(--ink)" }}>
            {action.label} complete
          </div>
          <div style={{ fontSize: "0.78rem", color: "var(--ink-muted)", marginTop: 2 }}>
            {job.text || job.original_filename}
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button
          onClick={download}
          disabled={downloading}
          style={{
            display: "flex", alignItems: "center", gap: 7,
            background: action.color, color: "#fff",
            border: "none", borderRadius: 8,
            padding: "0.55rem 1.1rem",
            cursor: "pointer", fontWeight: 600, fontSize: "0.875rem",
          }}
        >
          {downloading ? <Spinner size={14} /> : <Download size={14} />}
          Download {action.badge}
        </button>
        <button
          onClick={onReset}
          style={{
            display: "flex", alignItems: "center", gap: 7,
            background: "none", border: "1px solid var(--border)",
            borderRadius: 8, padding: "0.55rem 1rem",
            cursor: "pointer", color: "var(--ink-secondary)", fontSize: "0.875rem",
          }}
        >
          <RotateCcw size={13} /> New operation
        </button>
      </div>
    </div>
  );
}

/*  Active operation panel  */
function OperationPanel({ action, onComplete }) {
  const [files, setFiles]         = useState([]);
  const [fromPage, setFromPage]   = useState(1);
  const [toPage, setToPage]       = useState(5);
  const [processing, setProcessing] = useState(false);
  const [statusMsg, setStatusMsg]   = useState("");

  const onDrop = useCallback((accepted) => {
    if (action.multi) {
      setFiles(prev => [...prev, ...accepted]);
    } else {
      setFiles(accepted.slice(0, 1));
    }
  }, [action.multi]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: action.accept,
    multiple: action.multi,
    maxFiles: action.multi ? 10 : 1,
    disabled: processing,
  });

  const removeFile = (idx) => setFiles(f => f.filter((_, i) => i !== idx));

  const canSubmit = action.multi
    ? files.length >= (action.minFiles || 1)
    : files.length === 1;

  const process = async () => {
    if (!canSubmit) return;
    setProcessing(true);
    setStatusMsg("Uploading…");

    try {
      let jobData;

      if (action.endpoint === "studio/merge" || action.endpoint === "studio/combine") {
        // Multi-file endpoints
        const form = new FormData();
        files.forEach(f => form.append("files", f));
        const endpoint = action.endpoint === "studio/merge" ? "/studio/merge" : "/studio/combine";
        const { data } = await api.post(endpoint, form, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        jobData = data;

      } else if (action.endpoint === "studio/split") {
        // Split with page range
        const form = new FormData();
        form.append("file", files[0]);
        form.append("from_page", fromPage);
        form.append("to_page", toPage);
        const { data } = await api.post("/studio/split", form, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        jobData = data;

      } else {
        // Standard /jobs/upload
        const form = new FormData();
        form.append("file", files[0]);
        form.append("job_type", action.id);
        const { data } = await api.post("/jobs/upload", form, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        jobData = data;
      }

      setStatusMsg("Processing…");
      const done = await waitForJobSSE(jobData.id, api);

      if (done.status === "completed") {
        // Fetch full job for result info
        const { data: fullJob } = await api.get(`/jobs/${done.id || jobData.id}`);
        toast.success(`${action.label} complete`);
        onComplete({ ...fullJob, text: done.result_text || fullJob.result_text });
      } else {
        toast.error(done.error_message || `${action.label} failed`);
      }
    } catch (err) {
      toast.error(errMsg(err, `${action.label} failed`));
    } finally {
      setProcessing(false);
      setStatusMsg("");
    }
  };

  return (
    <div className="card" style={{ padding: "1.5rem" }}>
      <div style={{ marginBottom: "1.25rem" }}>
        <h3 style={{ margin: 0, fontSize: "0.95rem", fontWeight: 700, color: "var(--ink)" }}>
          {action.label}
        </h3>
        <p style={{ margin: "0.25rem 0 0", fontSize: "0.8rem", color: "var(--ink-muted)" }}>
          {action.desc}
        </p>
      </div>

      {/* Page range config for split */}
      {action.hasPageRange && (
        <div style={{ display: "flex", gap: "1rem", marginBottom: "1.25rem" }}>
          {[
            { label: "From page", val: fromPage, set: setFromPage },
            { label: "To page",   val: toPage,   set: setToPage   },
          ].map(({ label, val, set }) => (
            <div key={label} style={{ flex: 1 }}>
              <label style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: "var(--ink-muted)", marginBottom: 5 }}>
                {label}
              </label>
              <input
                type="number" min={1} value={val}
                onChange={e => set(Math.max(1, parseInt(e.target.value) || 1))}
                className="form-input"
                style={{ textAlign: "center" }}
              />
            </div>
          ))}
        </div>
      )}

      {/* Drop zone */}
      <div
        {...getRootProps()}
        className={`dropzone ${isDragActive ? "active" : ""}`}
        style={{ minHeight: 120, marginBottom: files.length ? "1rem" : 0 }}
      >
        <input {...getInputProps()} />
        {processing ? (
          <div style={{ textAlign: "center" }}>
            <Spinner size={26} />
            <p style={{ marginTop: 10, color: "var(--ink-muted)", fontSize: "0.82rem" }} className="pulsing">
              {statusMsg}
            </p>
          </div>
        ) : (
          <>
            <div className="dropzone-icon" style={{ marginBottom: 6 }}>
              <Upload size={22} />
            </div>
            <div className="dropzone-title" style={{ fontSize: "0.88rem" }}>
              {action.multi ? "Drop files here" : "Drop file here"} or click to browse
            </div>
            <div className="dropzone-sub">{action.acceptLabel}</div>
          </>
        )}
      </div>

      {/* Staged file list */}
      {files.length > 0 && (
        <div style={{ marginBottom: "1rem", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
          {files.map((f, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "0.5rem 0.875rem",
              borderBottom: i < files.length - 1 ? "1px solid var(--border)" : "none",
              background: "var(--paper)",
            }}>
              <FileText size={13} style={{ color: action.color, flexShrink: 0 }} />
              <span style={{ flex: 1, fontSize: "0.82rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--ink-secondary)" }}>
                {f.name}
              </span>
              <span style={{ fontSize: "0.72rem", color: "var(--ink-muted)" }}>
                {(f.size / 1024).toFixed(0)} KB
              </span>
              {!processing && (
                <button onClick={() => removeFile(i)} style={{ background: "none", border: "none", cursor: "pointer", padding: 2, color: "var(--ink-muted)", flexShrink: 0 }}>
                  <X size={13} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Submit */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <span style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>
          {action.multi && files.length > 0 && `${files.length} file${files.length > 1 ? "s" : ""} selected`}
          {action.multi && action.minFiles && files.length < action.minFiles && ` (need at least ${action.minFiles})`}
        </span>
        <button
          onClick={process}
          disabled={!canSubmit || processing}
          style={{
            display: "flex", alignItems: "center", gap: 7,
            background: canSubmit && !processing ? action.color : "var(--border)",
            color: canSubmit && !processing ? "#fff" : "var(--ink-muted)",
            border: "none", borderRadius: 8,
            padding: "0.55rem 1.25rem",
            cursor: canSubmit && !processing ? "pointer" : "not-allowed",
            fontWeight: 600, fontSize: "0.875rem",
            transition: "all 0.15s",
          }}
        >
          {processing ? <><Spinner size={13} /> Processing…</> : <>{action.label} <ChevronRight size={14} /></>}
        </button>
      </div>
    </div>
  );
}

/* Main page */

export default function DocumentStudioPage() {
  const [activeAction, setActiveAction] = useState(null);
  const [result, setResult]             = useState(null);

  const reset = () => { setResult(null); setActiveAction(null); };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Document Studio</h1>
          <p className="page-subtitle">
            Convert, merge, split, compress — all your document operations in one place
          </p>
        </div>
      </div>

      {/* Action grid */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
        gap: "0.875rem",
        marginBottom: "1.5rem",
      }}>
        {ACTIONS.map(action => (
          <ActionCard
            key={action.id}
            action={action}
            selected={activeAction?.id === action.id}
            onSelect={(a) => { setActiveAction(a); setResult(null); }}
          />
        ))}
      </div>

      {/* Operation panel or result */}
      {result ? (
        <ResultPanel job={result} action={activeAction} onReset={reset} />
      ) : activeAction ? (
        <OperationPanel
          action={activeAction}
          onComplete={(job) => setResult(job)}
        />
      ) : (
        <div className="card" style={{ padding: "2rem", textAlign: "center" }}>
          <div style={{ color: "var(--ink-muted)", fontSize: "0.9rem" }}>
            Select an operation above to get started
          </div>
        </div>
      )}
    </div>
  );
}