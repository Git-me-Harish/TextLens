import { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { useNavigate } from "react-router-dom";
import { Upload, RotateCcw, Copy, Check, Download, Sparkles } from "lucide-react";
import toast from "react-hot-toast";
import api, { errMsg } from "../../lib/api";
import { Spinner } from "../ui";
import { waitForJobSSE } from "../../hooks/useSSE";

const STYLES = [
  { id: "executive", label: "Executive",     icon: "📋", desc: "3–5 sentence overview for quick decisions", format: "paragraph" },
  { id: "bullets",   label: "Bullet Points",  icon: "•",  desc: "Scannable list of key takeaways",           format: "bullets"   },
  { id: "topics",    label: "Key Topics",     icon: "🏷", desc: "Main themes with brief explanations",       format: "topics"    },
  { id: "detailed",  label: "Detailed",       icon: "📄", desc: "Comprehensive summary covering all sections", format: "paragraph" },
];

function formatAsBullets(text) {
  const sentences = text.split(/(?<=[.!?])\s+/).map(s => s.trim()).filter(s => s.length > 20);
  return sentences.map((s, i) => (
    <div key={i} style={{ display: "flex", gap: 10, marginBottom: 6, alignItems: "flex-start" }}>
      <span style={{ color: "var(--accent)", fontWeight: 700, flexShrink: 0, marginTop: 2 }}>•</span>
      <span style={{ fontSize: "0.875rem", lineHeight: 1.65, color: "var(--ink-secondary)" }}>{s}</span>
    </div>
  ));
}

function formatAsTopics(text) {
  const paragraphs = text.split(/\n+/).map(p => p.trim()).filter(p => p.length > 30);
  if (paragraphs.length <= 1) return formatAsBullets(text);
  return paragraphs.map((para, i) => {
    const firstSentenceMatch = para.match(/^[^.!?]+[.!?]/);
    const topic = firstSentenceMatch ? firstSentenceMatch[0] : para.slice(0, 80) + "…";
    const rest  = firstSentenceMatch ? para.slice(firstSentenceMatch[0].length).trim() : "";
    return (
      <div key={i} style={{ marginBottom: "1rem", padding: "0.875rem 1rem", background: "var(--paper)", border: "1px solid var(--border)", borderLeft: "3px solid var(--accent)", borderRadius: "0 8px 8px 0" }}>
        <div style={{ fontWeight: 700, fontSize: "0.875rem", color: "var(--ink)", marginBottom: rest ? 5 : 0 }}>
          Topic {i + 1}: {topic}
        </div>
        {rest && <div style={{ fontSize: "0.82rem", color: "var(--ink-secondary)", lineHeight: 1.6 }}>{rest}</div>}
      </div>
    );
  });
}

function formatAsParagraph(text) {
  return text.split(/\n+/).map(p => p.trim()).filter(Boolean).map((p, i) => (
    <p key={i} style={{ margin: "0 0 0.625rem", fontSize: "0.875rem", lineHeight: 1.7, color: "var(--ink-secondary)" }}>{p}</p>
  ));
}

function SummaryResult({ text, styleId }) {
  const style = STYLES.find(s => s.id === styleId) || STYLES[0];
  if (!text) return null;
  if (style.format === "bullets") return <div>{formatAsBullets(text)}</div>;
  if (style.format === "topics") return <div>{formatAsTopics(text)}</div>;
  return <div>{formatAsParagraph(text)}</div>;
}

/**
 * AI summarization, as a Document Studio panel — same engine as the
 * former standalone /tools/summarize page, folded in here so every
 * document operation lives in one place.
 */
export default function SummarizePanel({ action }) {
  const navigate = useNavigate();
  const [selectedStyle, setSelectedStyle] = useState("executive");
  const [job, setJob] = useState(null);
  const [summaryText, setSummaryText] = useState("");
  const [uploading, setUploading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");

  const reset = () => { setJob(null); setSummaryText(""); setStatusMsg(""); };
  const currentStyle = STYLES.find(s => s.id === selectedStyle);

  const onDrop = useCallback(async ([file]) => {
    if (!file) return;
    setUploading(true);
    setStatusMsg("Uploading…");
    const form = new FormData();
    form.append("file", file);
    form.append("job_type", "pdf_summarize");
    try {
      const { data: submitted } = await api.post("/jobs/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setStatusMsg("Generating summary…");
      const done = await waitForJobSSE(submitted.id, api);
      if (done.status === "completed") {
        const { data: fullJob } = await api.get(`/jobs/${done.id || submitted.id}`);
        setJob(fullJob);
        setSummaryText(fullJob.result_text || "");
        toast.success("Summary complete");
      } else {
        toast.error(done.error_message || "Summarization failed");
      }
    } catch (err) {
      toast.error(errMsg(err, "Upload failed"));
    } finally {
      setUploading(false);
      setStatusMsg("");
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop, accept: { "application/pdf": [] }, maxFiles: 1, disabled: uploading,
  });

  const copy = () => {
    navigator.clipboard.writeText(summaryText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const download = () => {
    const blob = new Blob([summaryText], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `summary_${currentStyle.label.toLowerCase().replace(" ", "_")}.txt`;
    a.click();
  };

  if (job && summaryText) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        <div className="card" style={{ padding: "0.875rem 1.25rem", display: "flex", alignItems: "center", gap: "1rem", flexWrap: "wrap" }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: "0.88rem", color: "var(--ink)", marginBottom: 2 }}>{job.original_filename}</div>
            <div style={{ display: "flex", gap: 10, fontSize: "0.75rem", color: "var(--ink-muted)" }}>
              <span>{currentStyle?.icon} {currentStyle?.label} summary</span>
              {job.page_count && <span>· {job.page_count} pages</span>}
              {job.processing_time_ms && <span>· {job.processing_time_ms.toLocaleString()}ms</span>}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button onClick={reset} className="btn btn-ghost btn-sm"><RotateCcw size={13} /> New document</button>
            <button onClick={copy} className="btn btn-ghost btn-sm">{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Copied" : "Copy"}</button>
            <button onClick={download} className="btn btn-outline btn-sm"><Download size={13} /> Download</button>
            <button onClick={() => navigate("/pipelines", { state: { job_id: job.id } })} className="btn btn-primary btn-sm">
              <Sparkles size={13} /> Deeper analysis
            </button>
          </div>
        </div>
        <div className="card" style={{ padding: "1.5rem" }}>
          <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-muted)", marginBottom: "1rem" }}>
            {currentStyle?.icon} {currentStyle?.label} Summary
          </div>
          <SummaryResult text={summaryText} styleId={selectedStyle} />
        </div>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: "1.5rem" }}>
      <div style={{ marginBottom: "1.25rem" }}>
        <h3 style={{ margin: 0, fontSize: "0.95rem", fontWeight: 700, color: "var(--ink)" }}>{action.label}</h3>
        <p style={{ margin: "0.25rem 0 0", fontSize: "0.8rem", color: "var(--ink-muted)" }}>{action.desc}</p>
      </div>

      <div style={{ marginBottom: "1.25rem" }}>
        <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--ink-muted)", marginBottom: "0.625rem" }}>Summary style</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "0.625rem" }}>
          {STYLES.map(s => (
            <div
              key={s.id}
              onClick={() => setSelectedStyle(s.id)}
              style={{
                padding: "0.875rem",
                border: `2px solid ${selectedStyle === s.id ? "var(--accent)" : "var(--border)"}`,
                borderRadius: 10,
                background: selectedStyle === s.id ? "var(--accent-light)" : "var(--paper)",
                cursor: "pointer", transition: "all 0.15s", textAlign: "center",
              }}
            >
              <div style={{ fontSize: "1.25rem", marginBottom: 5 }}>{s.icon}</div>
              <div style={{ fontWeight: 700, fontSize: "0.82rem", color: selectedStyle === s.id ? "var(--accent)" : "var(--ink)", marginBottom: 3 }}>{s.label}</div>
              <div style={{ fontSize: "0.7rem", color: "var(--ink-muted)", lineHeight: 1.4 }}>{s.desc}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
        <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`} style={{ border: "none", borderRadius: 0, minHeight: 120 }}>
          <input {...getInputProps()} />
          {uploading ? (
            <div style={{ textAlign: "center" }}>
              <Spinner size={26} />
              <p style={{ marginTop: 10, color: "var(--ink-muted)", fontSize: "0.82rem" }} className="pulsing">{statusMsg || "Processing…"}</p>
            </div>
          ) : (
            <>
              <div className="dropzone-icon" style={{ marginBottom: 6 }}><Upload size={22} /></div>
              <div className="dropzone-title" style={{ fontSize: "0.88rem" }}>Drop a PDF to summarize</div>
              <div className="dropzone-sub">Will generate a {currentStyle?.label?.toLowerCase()} summary — max 50 MB</div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
