import { useState, useCallback, lazy, Suspense } from "react";
import { useDropzone } from "react-dropzone";
import {
  FileText, Hash, Layers, Scissors, Minimize2,
  Image as ImageIcon, Download, RotateCcw, Check,
  Upload, X, ChevronRight, ChevronLeft, PenLine, Sparkles,
} from "lucide-react";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Spinner } from "../components/ui";
import { waitForJobSSE } from "../hooks/useSSE";
import { usePersistedState, useHydratedRecord } from "../lib/usePersistedState";

// Lazy-loaded — pdfjs-dist + pdf-lib add ~900KB and only the PDF Editor
// tool needs them, so most Studio visitors never pay for this bundle.
const PdfEditor = lazy(() => import("../components/studio/PdfEditor"));
const SummarizePanel = lazy(() => import("../components/studio/SummarizePanel"));

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
  {
    id:          "pdf_edit",
    label:       "Edit PDF",
    badge:       "Correct",
    desc:        "Reorder, rotate, delete pages and add text before extraction",
    icon:        PenLine,
    color:       "#9333ea",
    bg:          "#faf5ff",
    accept:      { "application/pdf": [] },
    acceptLabel: "PDF only",
    multi:       false,
    panel:       "editor",
  },
  {
    id:          "pdf_summarize",
    label:       "Summarize",
    badge:       "AI",
    desc:        "Generate an executive, bullet, topic, or detailed summary",
    icon:        Sparkles,
    color:       "#ea580c",
    bg:          "#fff7ed",
    accept:      { "application/pdf": [] },
    acceptLabel: "PDF only",
    multi:       false,
    panel:       "summarize",
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

  const download = () => {
    if (!job.id) return;
    setDownloading(true);
    // Opens the presigned-redirect endpoint in a new tab — nothing to await
    // here (the browser navigates that tab independently), so this is only
    // ever a brief visual pulse rather than a real loading state.
    window.open(`/api/v1/jobs/${job.id}/download`, "_blank");
    setTimeout(() => setDownloading(false), 400);
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

      {/* Upload area — dropzone, staged files, and the submit button all live
          inside one bordered box so the action button reads as attached to
          the upload area instead of floating separately below it. */}
      <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
        <div
          {...getRootProps()}
          className={`dropzone ${isDragActive ? "active" : ""}`}
          style={{ minHeight: 120, border: "none", borderRadius: 0 }}
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
          <div style={{ borderTop: "1px solid var(--border)" }}>
            {files.map((f, i) => (
              <div key={i} style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "0.5rem 0.875rem",
                borderBottom: "1px solid var(--border)",
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

        {/* Action bar — the footer of the same box, so the button is
            visually anchored to the upload area rather than detached. */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
          padding: "0.75rem 1rem", borderTop: "1px solid var(--border)", background: "var(--paper)",
        }}>
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
    </div>
  );
}

/* Main page */

const PAGE_SIZE = 6;

export default function DocumentStudioPage() {
  // The selected tool and carousel page survive a reload. Only the action's
  // id is stored, never the action object — its `icon` is a React component,
  // which JSON silently drops, so a restored object would be missing the very
  // field the card renders. Deriving from ACTIONS keeps it whole.
  const [activeActionId, setActiveActionId] = usePersistedState("studio:actionId", null);
  const [page, setPage]                     = usePersistedState("studio:page", 0);
  const [resultJobId, setResultJobId]       = usePersistedState("studio:resultJobId", null);

  const activeAction = ACTIONS.find((a) => a.id === activeActionId) || null;

  const { data: result, setData: setResult } = useHydratedRecord(
    resultJobId,
    (id) => api.get(`/jobs/${id}`).then((r) => r.data),
    { onMissing: () => setResultJobId(null) },
  );

  const setActiveAction = (a) => setActiveActionId(a?.id ?? null);
  const onOperationComplete = (job) => {
    setResult(job);
    setResultJobId(job?.id ?? null);
  };

  const reset = () => {
    setResultJobId(null); setResult(null); setActiveActionId(null);
  };

  const pageCount = Math.ceil(ACTIONS.length / PAGE_SIZE);
  const visibleActions = ACTIONS.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  const lazyFallback = (
    <div className="card" style={{ padding: "2rem", textAlign: "center" }}><Spinner size={26} /></div>
  );

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Document Studio</h1>
          <p className="page-subtitle">
            Convert, merge, split, compress, edit, summarize — all your document operations in one place
          </p>
        </div>
      </div>

      {/* Action grid — a fixed set of tiles per page, paged with prev/next
          rather than a scrolling carousel (which had positioning problems). */}
      <div className="studio-carousel">
        {pageCount > 1 && (
          <button
            type="button"
            className="studio-carousel-nav"
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            aria-label="Previous options"
          >
            <ChevronLeft size={16} />
          </button>
        )}
        <div className="studio-carousel-track">
          {visibleActions.map(action => (
            <ActionCard
              key={action.id}
              action={action}
              selected={activeAction?.id === action.id}
              onSelect={(a) => { setActiveAction(a); setResult(null); setResultJobId(null); }}
            />
          ))}
        </div>
        {pageCount > 1 && (
          <button
            type="button"
            className="studio-carousel-nav"
            onClick={() => setPage(p => Math.min(pageCount - 1, p + 1))}
            disabled={page === pageCount - 1}
            aria-label="More options"
          >
            <ChevronRight size={16} />
          </button>
        )}
      </div>
      {pageCount > 1 && (
        <div style={{ textAlign: "center", fontSize: "0.75rem", fontWeight: 600, color: "var(--ink-muted)", marginTop: "0.625rem", marginBottom: "1.5rem" }}>
          Page {page + 1} of {pageCount}
        </div>
      )}

      {/* Operation panel or result */}
      {result ? (
        <ResultPanel job={result} action={activeAction} onReset={reset} />
      ) : activeAction?.panel === "editor" ? (
        <Suspense fallback={lazyFallback}>
          <PdfEditor action={activeAction} onComplete={onOperationComplete} />
        </Suspense>
      ) : activeAction?.panel === "summarize" ? (
        <Suspense fallback={lazyFallback}>
          <SummarizePanel action={activeAction} />
        </Suspense>
      ) : activeAction ? (
        <OperationPanel
          action={activeAction}
          onComplete={onOperationComplete}
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