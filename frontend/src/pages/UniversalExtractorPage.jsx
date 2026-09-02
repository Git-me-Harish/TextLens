/**
 * UniversalExtractorPage — merges ImageOCRPage + PDFExtractPage.
 *
 * Accepts: PDF, JPG, PNG, TIFF, WEBP, BMP
 * Auto-selects job_type based on file:
 *   PDF → pdf_extract (or pdf_to_markdown if Markdown format selected)
 *   Image → ocr_image
 *
 * Replaces polling with waitForJobSSE — resolves the instant Celery
 * publishes the job.completed event, no timer loops.
 */
import { useState, useEffect, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { useNavigate } from "react-router-dom";
import {
    Upload, FileText, Image, FileCode2, Copy, Check,
    Download, Sparkles, RotateCcw, Clock, Hash, Layers,
} from "lucide-react";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Spinner } from "../components/ui";
import { waitForJobSSE, isTerminalStatus } from "../hooks/useSSE";
import { usePersistedState, useHydratedRecord } from "../lib/usePersistedState";

/* File type resolution */

const PDF_MIME = "application/pdf";
const IMAGE_MIMES = new Set(["image/jpeg", "image/jpg", "image/png", "image/tiff", "image/tif", "image/webp", "image/bmp", "image/heic"]);
const ACCEPT = {
    "application/pdf": [],
    "image/jpeg": [],
    "image/png": [],
    "image/tiff": [],
    "image/webp": [],
    "image/bmp": [],
};

function resolveJobType(file, format) {
    const mime = (file.type || "").toLowerCase();
    const ext = ("." + (file.name || "").split(".").pop()).toLowerCase();

    const isPdf = mime === PDF_MIME || ext === ".pdf";
    const isImage = IMAGE_MIMES.has(mime) || IMAGE_MIMES.has(mime.replace("image/jpg", "image/jpeg"));

    if (isPdf) return format === "markdown" ? "pdf_to_markdown" : "pdf_extract";
    if (isImage) return "ocr_image";
    return "pdf_extract"; // fallback
}

function fileLabel(file) {
    const mime = (file.type || "").toLowerCase();
    if (mime === PDF_MIME || file.name?.toLowerCase().endsWith(".pdf")) return "PDF";
    if (IMAGE_MIMES.has(mime)) return "Image";
    return "Document";
}

/* Word count helper */
function wordCount(text) {
    return (text || "").trim().split(/\s+/).filter(Boolean).length;
}

/*    Result display */
function ResultPanel({ job, text, format, onReset }) {
    const [copied, setCopied] = useState(false);
    const navigate = useNavigate();

    const copy = () => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const download = (ext) => {
        const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = (job.original_filename?.replace(/\.[^.]+$/, "") || "extracted") + "." + ext;
        a.click();
        URL.revokeObjectURL(url);
    };

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {/* Stats bar */}
            <div className="card" style={{ padding: "0.875rem 1.25rem", display: "flex", alignItems: "center", gap: "1.5rem", flexWrap: "wrap" }}>
                <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--ink)", marginBottom: 2 }}>
                        {job.original_filename}
                    </div>
                    <div style={{ display: "flex", gap: "1rem", fontSize: "0.75rem", color: "var(--ink-muted)" }}>
                        {job.page_count && <span>{job.page_count} page{job.page_count !== 1 ? "s" : ""}</span>}
                        <span>{wordCount(text).toLocaleString()} words</span>
                        {job.processing_time_ms && (
                            <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
                                <Clock size={10} /> {job.processing_time_ms.toLocaleString()}ms
                            </span>
                        )}
                        <span style={{ color: "var(--success)", fontWeight: 600 }}>✓ Extracted</span>
                    </div>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button onClick={copy} className="btn btn-ghost btn-sm">
                        {copied ? <Check size={13} /> : <Copy size={13} />}
                        {copied ? "Copied" : "Copy"}
                    </button>
                    <button onClick={() => download("txt")} className="btn btn-outline btn-sm">
                        <Download size={13} /> .txt
                    </button>
                    {format === "markdown" && (
                        <button onClick={() => download("md")} className="btn btn-outline btn-sm">
                            <Hash size={13} /> .md
                        </button>
                    )}
                    {job.result_file_path && (
                        <button onClick={() => window.open(`/api/v1/jobs/${job.id}/download`, "_blank")} className="btn btn-outline btn-sm">
                            <Download size={13} /> Download file
                        </button>
                    )}
                    <button
                        onClick={() => navigate("/pipelines", { state: { job_id: job.id } })}
                        className="btn btn-primary btn-sm"
                    >
                        <Sparkles size={13} /> Analyze with AI
                    </button>
                    <button onClick={onReset} className="btn btn-ghost btn-sm">
                        <RotateCcw size={13} /> New
                    </button>
                </div>
            </div>

            {/* Text result */}
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                <div style={{ padding: "0.75rem 1.25rem", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
                    {format === "markdown"
                        ? <><Hash size={14} style={{ color: "var(--accent)" }} /><span style={{ fontSize: "0.82rem", fontWeight: 600 }}>Markdown output</span></>
                        : <><FileText size={14} style={{ color: "var(--accent)" }} /><span style={{ fontSize: "0.82rem", fontWeight: 600 }}>Extracted text</span></>
                    }
                </div>
                <pre style={{
                    margin: 0, padding: "1.25rem",
                    fontFamily: "var(--font-mono, monospace)",
                    fontSize: "0.82rem",
                    lineHeight: 1.7,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    color: "var(--ink-secondary)",
                    maxHeight: 520,
                    overflowY: "auto",
                    background: "var(--paper)",
                }}>
                    {text || "(No text extracted)"}
                </pre>
            </div>
        </div>
    );
}

/* Main page */
export default function UniversalExtractorPage() {
    // Persisted: the chosen format and the id of the extraction. The result
    // text itself is NOT persisted — it can be megabytes and the server is its
    // owner, so the id is stored and the body re-fetched (see below).
    const [format, setFormat] = usePersistedState("extractor:format", "text");   // "text" | "markdown"
    const [jobId, setJobId]   = usePersistedState("extractor:jobId", null);
    const [text, setText] = useState("");
    const [uploading, setUploading] = useState(false);
    const [statusMsg, setStatusMsg] = useState("");

    const { data: job, setData: setJob } = useHydratedRecord(
        jobId,
        (id) => api.get(`/jobs/${id}`).then((r) => r.data),
        { onMissing: () => setJobId(null) },   // deleted from history — drop it
    );

    // Rehydrating the job brings its text back after a reload.
    useEffect(() => {
        if (job?.result_text != null) setText(job.result_text);
    }, [job?.id, job?.result_text]);

    // An extraction still running when the page reloaded: resume the wait
    // rather than leaving the user with a stale, silent page.
    useEffect(() => {
        if (!job || isTerminalStatus(job.status)) return;
        let cancelled = false;
        setUploading(true);
        setStatusMsg("Extracting text…");
        waitForJobSSE(job.id, api)
            .then(async (done) => {
                if (cancelled) return;
                if (done.status === "completed") {
                    const { data: full } = await api.get(`/jobs/${job.id}`);
                    setJob(full);
                    setText(full.result_text || "");
                } else {
                    toast.error(done.error_message || "Extraction failed");
                }
            })
            .finally(() => { if (!cancelled) { setUploading(false); setStatusMsg(""); } });
        return () => { cancelled = true; };
    }, [job?.id, job?.status]);

    const reset = () => { setJobId(null); setJob(null); setText(""); setStatusMsg(""); };

    const onDrop = useCallback(async ([file]) => {
        if (!file) return;
        setUploading(true);
        setStatusMsg("Uploading…");

        const jobType = resolveJobType(file, format);
        const label = fileLabel(file);
        const form = new FormData();
        form.append("file", file);
        form.append("job_type", jobType);

        try {
            const { data: submitted } = await api.post("/jobs/upload", form, {
                headers: { "Content-Type": "multipart/form-data" },
            });

            setStatusMsg(`${label} uploaded — extracting text…`);
            const done = await waitForJobSSE(submitted.id, api);

            if (done.status === "completed") {
                // Fetch full job to get result_text
                const { data: fullJob } = await api.get(`/jobs/${done.id || submitted.id}`);
                setJob(fullJob);
                setJobId(fullJob.id);
                setText(fullJob.result_text || "");
                toast.success("Extraction complete");
            } else {
                toast.error(done.error_message || "Extraction failed");
            }
        } catch (err) {
            const detail = err.response?.data?.detail;
            if (err.response?.status === 409 && detail?.code === "duplicate_file") {
                toast(
                    (t) => (
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>Already extracted</div>
                            <div style={{ fontSize: "0.8rem", color: "#666" }}>
                                <strong>{detail.original_filename}</strong> is in your history.
                            </div>
                            <button
                                onClick={async () => {
                                    toast.dismiss(t.id);
                                    const { data: existing } = await api.get(`/jobs/${detail.existing_job_id}`);
                                    setJob(existing);
                                    setJobId(existing.id);
                                    setText(existing.result_text || "");
                                }}
                                style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 6, padding: "0.3rem 0.75rem", cursor: "pointer", fontSize: "0.78rem", alignSelf: "flex-start" }}
                            >
                                Use existing result
                            </button>
                        </div>
                    ),
                    { duration: 10000 }
                );
            } else {
                toast.error(errMsg(err, "Upload failed"));
            }
        } finally {
            setUploading(false);
            setStatusMsg("");
        }
    }, [format]);

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop,
        accept: ACCEPT,
        maxFiles: 1,
        disabled: uploading,
    });

    return (
        <div>
            <div className="page-header">
                <div>
                    <h1 className="page-title">Universal Extractor</h1>
                    <p className="page-subtitle">
                        Extract text from any document — PDF, JPG, PNG, TIFF, WEBP, BMP
                    </p>
                </div>
            </div>

            {!job && (
                <>
                    {/* Format toggle */}
                    <div style={{ display: "flex", gap: 8, marginBottom: "1.25rem" }}>
                        {[
                            { id: "text", icon: <FileText size={13} />, label: "Plain Text" },
                            { id: "markdown", icon: <Hash size={13} />, label: "Markdown (PDF only)" },
                        ].map(opt => (
                            <button
                                key={opt.id}
                                onClick={() => setFormat(opt.id)}
                                style={{
                                    display: "flex", alignItems: "center", gap: 6,
                                    padding: "0.4rem 0.9rem",
                                    border: `1px solid ${format === opt.id ? "var(--accent)" : "var(--border)"}`,
                                    borderRadius: 8,
                                    background: format === opt.id ? "var(--accent)" : "var(--paper)",
                                    color: format === opt.id ? "#fff" : "var(--ink-secondary)",
                                    cursor: "pointer", fontSize: "0.82rem", fontWeight: 500,
                                    transition: "all 0.15s",
                                }}
                            >
                                {opt.icon} {opt.label}
                            </button>
                        ))}
                    </div>

                    {/* Drop zone */}
                    <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`} style={{ minHeight: 220 }}>
                        <input {...getInputProps()} />
                        {uploading ? (
                            <div style={{ textAlign: "center" }}>
                                <Spinner size={30} />
                                <p style={{ marginTop: 14, color: "var(--ink-muted)", fontSize: "0.88rem" }} className="pulsing">
                                    {statusMsg || "Processing…"}
                                </p>
                            </div>
                        ) : (
                            <>
                                <div className="dropzone-icon">
                                    <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
                                        <FileText size={28} style={{ color: "var(--accent)" }} />
                                        <Image size={28} style={{ color: "var(--accent)", opacity: 0.6 }} />
                                    </div>
                                </div>
                                <div className="dropzone-title">
                                    {isDragActive ? "Drop it here" : "Drop any document here or click to browse"}
                                </div>
                                <div className="dropzone-sub">
                                    PDF · JPG · PNG · TIFF · WEBP · BMP — up to {import.meta?.env?.VITE_MAX_FILE_MB || 50} MB
                                </div>
                                {format === "markdown" && (
                                    <div style={{ marginTop: 8, fontSize: "0.75rem", color: "var(--accent)" }}>
                                        Markdown output is only available for PDF files
                                    </div>
                                )}
                            </>
                        )}
                    </div>

                    {/* File type hints */}
                    <div style={{ display: "flex", gap: "1rem", marginTop: "1.25rem", flexWrap: "wrap" }}>
                        {[
                            { icon: <FileText size={14} />, label: "Text PDF", hint: "Direct extraction" },
                            { icon: <Image size={14} />, label: "Scanned PDF", hint: "OCR per page" },
                            { icon: <Image size={14} />, label: "Image", hint: "Tesseract OCR" },
                        ].map((item, i) => (
                            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "0.5rem 0.875rem", background: "var(--paper)", border: "1px solid var(--border)", borderRadius: 8, fontSize: "0.78rem" }}>
                                <span style={{ color: "var(--accent)" }}>{item.icon}</span>
                                <span style={{ fontWeight: 600, color: "var(--ink)" }}>{item.label}</span>
                                <span style={{ color: "var(--ink-muted)" }}>{item.hint}</span>
                            </div>
                        ))}
                    </div>
                </>
            )}

            {job && (
                <ResultPanel job={job} text={text} format={format} onReset={reset} />
            )}
        </div>
    );
}