import { useState, useEffect } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, ChevronRight, RotateCcw, Download, Copy, Check, AlertTriangle, FileSpreadsheet, Pencil, Sparkles, X } from "lucide-react";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Button, Spinner, Badge } from "../components/ui";
import { useAgent } from "../lib/AgentContext";
import DrivePickerModal from "../components/DrivePickerModal";

/* ── Auto-classifier suggestion banner ─────────────────────────────── */
function ClassifierBanner({ result, catalog, onAccept, onDismiss }) {
  if (!result) return null;
  const domainMeta = catalog?.[result.detected_domain];
  const pipelineMeta = domainMeta?.pipelines?.[result.detected_pipeline];
  if (!domainMeta || !pipelineMeta) return null;
  const conf = result.confidence || 0;
  const confColor = conf >= 80 ? "var(--success)" : conf >= 50 ? "var(--warning)" : "var(--danger)";

  return (
    <div style={{ background: "var(--paper-2,#f0f4ff)", border: "1px solid var(--accent)", borderRadius: 10, padding: "0.875rem 1.1rem", marginBottom: "1.25rem", display: "flex", alignItems: "flex-start", gap: 10 }}>
      <Sparkles size={16} style={{ color: "var(--accent)", flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--ink)", marginBottom: 3 }}>
          Auto-detected: {domainMeta.label} → {pipelineMeta.label}
        </div>
        <div style={{ fontSize: "0.78rem", color: "var(--ink-muted)", marginBottom: 8 }}>
          {result.reasoning} &nbsp;
          <span style={{ fontWeight: 600, color: confColor }}>{conf}% confidence</span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={onAccept} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 6, padding: "0.3rem 0.85rem", cursor: "pointer", fontSize: "0.8rem", fontWeight: 600 }}>
            Use this pipeline
          </button>
          <button onClick={onDismiss} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 6, padding: "0.3rem 0.75rem", cursor: "pointer", fontSize: "0.8rem", color: "var(--ink-muted)" }}>
            Choose manually
          </button>
        </div>
      </div>
      <button onClick={onDismiss} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)", padding: 2, flexShrink: 0 }}>
        <X size={14} />
      </button>
    </div>
  );
}

/* ── Editable structured result ────────────────────────────────────── */
function EditableStructuredResult({ data, runId, depth = 0 }) {
  const [editing, setEditing] = useState(null); // "field.path"
  const [editVal, setEditVal] = useState("");
  const [saving, setSaving] = useState(false);
  const [corrections, setCorrections] = useState({}); // path → corrected value

  const saveCorrection = async (fieldPath) => {
    setSaving(true);
    try {
      await api.post(`/agents/${runId}/corrections`, {
        corrections: [{ field_path: fieldPath, corrected_value: editVal }],
      });
      setCorrections(prev => ({ ...prev, [fieldPath]: editVal }));
      toast.success("Correction saved");
      setEditing(null);
    } catch (err) {
      toast.error(errMsg(err, "Failed to save correction"));
    } finally {
      setSaving(false);
    }
  };

  const renderValue = (value, path) => {
    const corrected = corrections[path];
    const displayVal = corrected !== undefined ? corrected : value;

    if (displayVal === null || displayVal === undefined)
      return <span style={{ color: "var(--ink-muted)", fontStyle: "italic" }}>—</span>;
    if (typeof displayVal === "boolean")
      return <span style={{ color: "var(--accent)", fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{displayVal ? "true" : "false"}</span>;
    if (typeof displayVal === "object")
      return <EditableStructuredResult data={displayVal} runId={runId} depth={depth + 1} />;

    const isEditing = editing === path;
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        {isEditing ? (
          <>
            <input
              autoFocus
              value={editVal}
              onChange={e => setEditVal(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") saveCorrection(path); if (e.key === "Escape") setEditing(null); }}
              style={{ flex: 1, border: "1px solid var(--accent)", borderRadius: 5, padding: "2px 8px", fontSize: "0.83rem", outline: "none", fontFamily: "inherit" }}
            />
            <button onClick={() => saveCorrection(path)} disabled={saving} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 5, padding: "2px 8px", cursor: "pointer", fontSize: "0.75rem" }}>
              {saving ? "…" : "Save"}
            </button>
            <button onClick={() => setEditing(null)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)" }}><X size={12} /></button>
          </>
        ) : (
          <>
            <span style={{ color: corrected !== undefined ? "var(--accent)" : "var(--ink-secondary)", fontSize: "0.875rem" }}>
              {String(displayVal)}
              {corrected !== undefined && <span style={{ fontSize: "0.7rem", color: "var(--accent)", marginLeft: 5 }}>✎ corrected</span>}
            </span>
            {runId && (
              <button onClick={() => { setEditing(path); setEditVal(String(displayVal)); }}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)", padding: 2, opacity: 0, transition: "opacity 0.15s" }}
                className="edit-btn"
                title="Edit this field"
              >
                <Pencil size={11} />
              </button>
            )}
          </>
        )}
      </div>
    );
  };

  if (data === null || data === undefined) return <span style={{ color: "var(--ink-muted)", fontStyle: "italic" }}>—</span>;
  if (typeof data === "boolean") return <span style={{ color: "var(--accent)", fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{data ? "true" : "false"}</span>;

  if (Array.isArray(data)) {
    if (data.length === 0) return <span style={{ color: "var(--ink-muted)", fontStyle: "italic" }}>None</span>;
    if (typeof data[0] === "string") {
      return (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 2 }}>
          {data.map((item, i) => <span key={i} style={{ background: "var(--paper-2)", border: "1px solid var(--border)", borderRadius: 6, padding: "2px 8px", fontSize: "0.78rem", color: "var(--ink-secondary)" }}>{item}</span>)}
        </div>
      );
    }
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 4 }}>
        {data.map((item, i) => (
          <div key={i} style={{ background: "var(--paper)", border: "1px solid var(--border)", borderRadius: 8, padding: "0.75rem" }}>
            <EditableStructuredResult data={item} runId={runId} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  if (data.severity && data.issue) {
    return (
      <div className={`risk-flag ${data.severity}`}>
        <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
        <div>
          <div style={{ fontWeight: 700, fontSize: "0.72rem" }}>{data.severity.toUpperCase()}</div>
          <div style={{ fontSize: "0.82rem" }}>{data.issue}</div>
          {data.clause && <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: 2 }}>{data.clause}</div>}
        </div>
      </div>
    );
  }

  return (
    <div className="result-field-grid" style={{ "--edit-btn-opacity": 0 }}>
      <style>{`.result-field:hover .edit-btn { opacity: 1 !important; }`}</style>
      {Object.entries(data).map(([key, value]) => (
        <div key={key} className="result-field">
          <span className="result-field-key">{key.replace(/_/g, " ")}</span>
          <div className="result-field-value" style={{ fontFamily: "inherit" }}>
            {renderValue(value, key)}
          </div>
        </div>
      ))}
    </div>
  );
}

function Steps({ current }) {
  const steps = ["Extract", "Choose Pipeline", "Run Agent", "Results"];
  return (
    <div className="steps">
      {steps.map((label, i) => {
        const state = i < current ? "done" : i === current ? "active" : "idle";
        return (
          <div key={i} className="step" style={{ flex: i < steps.length - 1 ? 1 : undefined }}>
            <div className={`step-circle ${state}`}>{state === "done" ? "✓" : i + 1}</div>
            <span className={`step-label ${state}`}>{label}</span>
            {i < steps.length - 1 && <div className={`step-line ${state === "done" ? "done" : ""}`} />}
          </div>
        );
      })}
    </div>
  );
}

function ConfidenceBar({ score }) {
  const color = score >= 80 ? "var(--success)" : score >= 50 ? "var(--warning)" : "var(--danger)";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>Confidence</span>
        <span style={{ fontSize: "0.75rem", fontWeight: 600, color }}>{score}%</span>
      </div>
      <div className="confidence-bar">
        <div className="confidence-fill" style={{ width: `${score}%`, background: color }} />
      </div>
    </div>
  );
}

function StructuredResult({ data, depth = 0 }) {
  if (data === null || data === undefined) return <span style={{ color: "var(--ink-muted)", fontStyle: "italic" }}>—</span>;
  if (typeof data === "boolean") return <span style={{ color: "var(--accent)", fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>{data ? "true" : "false"}</span>;
  if (typeof data !== "object") return <span style={{ color: "var(--ink-secondary)" }}>{String(data)}</span>;

  if (Array.isArray(data)) {
    if (data.length === 0) return <span style={{ color: "var(--ink-muted)", fontStyle: "italic" }}>None</span>;
    if (typeof data[0] === "string") {
      return (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 2 }}>
          {data.map((item, i) => (
            <span key={i} style={{ background: "var(--paper-2)", border: "1px solid var(--border)", borderRadius: 6, padding: "2px 8px", fontSize: "0.78rem", color: "var(--ink-secondary)" }}>{item}</span>
          ))}
        </div>
      );
    }
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 4 }}>
        {data.map((item, i) => (
          <div key={i} style={{ background: "var(--paper)", border: "1px solid var(--border)", borderRadius: 8, padding: "0.75rem" }}>
            <StructuredResult data={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  // Risk flag object
  if (data.severity && data.issue) {
    return (
      <div className={`risk-flag ${data.severity}`}>
        <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
        <div>
          <div style={{ fontWeight: 700, fontSize: "0.72rem" }}>{data.severity.toUpperCase()}</div>
          <div style={{ fontSize: "0.82rem" }}>{data.issue}</div>
          {data.clause && <div style={{ fontSize: "0.75rem", opacity: 0.75, marginTop: 2 }}>{data.clause}</div>}
        </div>
      </div>
    );
  }

  return (
    <div className="result-field-grid">
      {Object.entries(data).map(([key, value]) => (
        <div key={key} className="result-field">
          <span className="result-field-key">{key.replace(/_/g, " ")}</span>
          <div className="result-field-value" style={{ fontFamily: "inherit" }}>
            <StructuredResult data={value} depth={depth + 1} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function PipelinesPage() {
  const { startAgent, clearAgent } = useAgent();
  const [step, setStep] = useState(0);
  const [catalog, setCatalog] = useState(null);
  const [ocrJob, setOcrJob] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [selectedDomain, setSelectedDomain] = useState(null);
  const [selectedPipeline, setSelectedPipeline] = useState(null);
  const [instructions, setInstructions] = useState("");
  const [agentRun, setAgentRun] = useState(null);
  const [running, setRunning] = useState(false);
  const [polling, setPolling] = useState(false);
  const [activeTab, setActiveTab] = useState("structured");
  const [copied, setCopied] = useState(false);
  const [classifying, setClassifying] = useState(false);
  const [classifierResult, setClassifierResult] = useState(null);
  const [driveOpen, setDriveOpen] = useState(false);

  useEffect(() => { api.get("/agents/catalog").then(r => setCatalog(r.data)); }, []);

  useEffect(() => {
    if (!agentRun || agentRun.status === "completed" || agentRun.status === "failed") { setPolling(false); return; }
    setPolling(true);
    const t = setInterval(async () => {
      const { data } = await api.get(`/agents/${agentRun.id}`);
      setAgentRun(data);
      if (data.status === "completed" || data.status === "failed") {
        clearInterval(t); setPolling(false); setStep(3);
        clearAgent();
        data.status === "completed" ? toast.success("Pipeline complete") : toast.error("Pipeline failed");
      }
    }, 2000);
    return () => clearInterval(t);
  }, [agentRun?.id, agentRun?.status]);

  const onDrop = async ([file]) => {
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append("file", file);
    form.append("job_type", file.type === "application/pdf" ? "pdf_extract" : "ocr_image");
    try {
      const { data } = await api.post("/jobs/upload", form, { headers: { "Content-Type": "multipart/form-data" } });
      let job = data;
      for (let i = 0; i < 40; i++) {
        await new Promise(r => setTimeout(r, 1500));
        const res = await api.get(`/jobs/${job.id}`);
        job = res.data;
        if (job.status === "completed" || job.status === "failed") break;
      }
      if (job.status === "completed") {
        setOcrJob(job);
        setStep(1);
        toast.success("Text extracted — detecting best pipeline…");
        // Auto-classify in background
        setClassifying(true);
        api.post("/agents/classify", { job_id: job.id })
          .then(r => setClassifierResult(r.data))
          .catch(() => {}) // silent fail — user still picks manually
          .finally(() => setClassifying(false));
      }
      else toast.error(job.error_message || "Extraction failed");
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409 && detail?.code === "duplicate_file") {
        toast((t) => (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>Already processed</div>
            <div style={{ fontSize: "0.8rem", color: "#666" }}><strong>{detail.original_filename}</strong> already in your history.</div>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={async () => { toast.dismiss(t.id); const { data } = await api.get(`/jobs/${detail.existing_job_id}`); setOcrJob(data); setStep(1); }} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 6, padding: "0.3rem 0.75rem", cursor: "pointer", fontSize: "0.78rem" }}>Use existing</button>
              <button onClick={() => toast.dismiss(t.id)} style={{ background: "none", border: "1px solid #ddd", borderRadius: 6, padding: "0.3rem 0.75rem", cursor: "pointer", fontSize: "0.78rem" }}>Dismiss</button>
            </div>
          </div>
        ), { duration: 10000 });
      } else {
        toast.error(typeof detail === "string" ? detail : "Upload failed");
      }
    } finally { setUploading(false); }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [], "image/jpeg": [], "image/png": [], "image/webp": [], "image/tiff": [] },
    maxFiles: 1,
  });

  const runAgent = async () => {
    if (!selectedDomain || !selectedPipeline || !ocrJob) return;
    setRunning(true); setStep(2);
    try {
      const { data } = await api.post("/agents/run", { job_id: ocrJob.id, domain: selectedDomain, pipeline_type: selectedPipeline, user_instructions: instructions });
      setAgentRun(data);
      startAgent(selectedDomain, selectedPipeline, catalog[selectedDomain]?.pipelines[selectedPipeline]?.label);
    } catch (err) { toast.error(errMsg(err, "Failed to start agent")); setStep(1); }
    finally { setRunning(false); }
  };

  const reset = () => { setStep(0); setOcrJob(null); setSelectedDomain(null); setSelectedPipeline(null); setAgentRun(null); setInstructions(""); setClassifierResult(null); clearAgent(); };

  const copyJSON = () => {
    navigator.clipboard.writeText(JSON.stringify(agentRun?.structured_result, null, 2));
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  };

  if (!catalog) return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "60vh" }}><Spinner size={32} /></div>;

  return (
    <div>
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="page-title">Domain Pipelines</h1>
          <p className="page-subtitle">AI-powered extraction for Finance, Healthcare, Legal, Logistics, HR and more</p>
        </div>
        {step > 0 && <button onClick={reset} className="btn btn-ghost btn-sm"><RotateCcw size={14} /> Start over</button>}
      </div>

      <Steps current={step} />

      {/* Step 0 — Upload */}
      {step === 0 && (
        <div className="card" style={{ padding: "1.5rem" }}>
          <h2 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: "1rem" }}>Upload your document</h2>
          <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`}>
            <input {...getInputProps()} />
            {uploading ? (
              <div><Spinner size={28} /><p style={{ marginTop: 12, color: "var(--ink-muted)", fontSize: "0.88rem" }} className="pulsing">Extracting text...</p></div>
            ) : (
              <><div className="dropzone-icon"><Upload size={28} /></div>
              <div className="dropzone-title">Drop your file here or click to browse</div>
              <div className="dropzone-sub">PDF, JPG, PNG, TIFF — max 50MB</div></>
            )}
          </div>
          {/* Drive import */}
          <div style={{ textAlign: "center", marginTop: "1rem" }}>
            <span style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>or </span>
            <button onClick={() => setDriveOpen(true)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--accent)", fontSize: "0.82rem", fontWeight: 600, padding: 0, textDecoration: "underline" }}>
              import from Google Drive
            </button>
          </div>
        </div>
      )}

      <DrivePickerModal
        isOpen={driveOpen}
        onClose={() => setDriveOpen(false)}
        jobType="pdf_extract"
        onImport={(job) => {
          setOcrJob(job);
          setStep(1);
          toast.success("Importing from Drive — detecting pipeline…");
          setClassifying(true);
          api.post("/agents/classify", { job_id: job.id })
            .then(r => setClassifierResult(r.data))
            .catch(() => {})
            .finally(() => setClassifying(false));
        }}
      />

      {/* Step 1 — Choose */}
      {step === 1 && (
        <div>
          {/* Classifier banner — shown while classifying or when result ready */}
          {classifying && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0.75rem 1rem", background: "var(--paper-2)", borderRadius: 10, marginBottom: "1.25rem", border: "1px solid var(--border)", fontSize: "0.82rem", color: "var(--ink-muted)" }}>
              <Spinner size={14} /> Detecting best pipeline for this document…
            </div>
          )}
          {!classifying && classifierResult && (
            <ClassifierBanner
              result={classifierResult}
              catalog={catalog}
              onAccept={() => {
                setSelectedDomain(classifierResult.detected_domain);
                setSelectedPipeline(classifierResult.detected_pipeline);
                setClassifierResult(null);
                toast.success("Pipeline pre-selected — review and run");
              }}
              onDismiss={() => setClassifierResult(null)}
            />
          )}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem" }}>
          <div className="card" style={{ padding: "1.5rem" }}>
            <h2 style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "1rem" }}>Select domain</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {Object.entries(catalog).map(([key, domain]) => (
                <div key={key} onClick={() => { setSelectedDomain(key); setSelectedPipeline(null); }}
                  className={`domain-card ${selectedDomain === key ? "selected" : ""}`}
                  style={{ padding: "0.875rem 1rem", display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{ width: 32, height: 32, borderRadius: 8, background: domain.color, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <div style={{ width: 10, height: 10, borderRadius: "50%", background: domain.accent }} />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: "0.875rem", fontWeight: 600, color: "var(--ink)" }}>{domain.label}</div>
                    <div style={{ fontSize: "0.72rem", color: "var(--ink-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{Object.keys(domain.pipelines).length} pipeline{Object.keys(domain.pipelines).length > 1 ? "s" : ""}</div>
                  </div>
                  {selectedDomain === key && <ChevronRight size={15} style={{ color: "var(--accent)" }} />}
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            {selectedDomain && (
              <div className="card" style={{ padding: "1.5rem" }}>
                <h2 style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "1rem" }}>Select pipeline</h2>
                <div className="pipeline-list">
                  {Object.entries(catalog[selectedDomain].pipelines).map(([key, p]) => (
                    <div key={key} onClick={() => setSelectedPipeline(key)} className={`pipeline-item ${selectedPipeline === key ? "selected" : ""}`}>
                      <div className="pipeline-item-dot" style={{ background: catalog[selectedDomain].accent }} />
                      <div><div className="pipeline-item-label">{p.label}</div><div className="pipeline-item-desc">{p.desc}</div></div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {selectedPipeline && (
              <div className="card" style={{ padding: "1.5rem" }}>
                <h2 style={{ fontSize: "0.95rem", fontWeight: 600, marginBottom: "0.75rem" }}>Instructions <span style={{ fontWeight: 400, color: "var(--ink-muted)" }}>(optional)</span></h2>
                <textarea className="form-input" placeholder="Any specific focus or context for the agent..." value={instructions} onChange={e => setInstructions(e.target.value)} rows={3} />
                <Button onClick={runAgent} loading={running} disabled={running} style={{ width: "100%", marginTop: "0.875rem" }}>
                  Run {catalog[selectedDomain]?.pipelines[selectedPipeline]?.label}
                </Button>
              </div>
            )}
          </div>
        </div>
        </div>
      )}

      {/* Step 2 — Running */}
      {step === 2 && (
        <div className="card" style={{ padding: "3rem", textAlign: "center" }}>
          <Spinner size={36} />
          <h2 style={{ marginTop: "1.25rem", fontWeight: 300, fontSize: "1.25rem" }}>Agent is analyzing your document</h2>
          <p style={{ color: "var(--ink-muted)", marginTop: "0.5rem", fontSize: "0.9rem" }}>
            Running {catalog[selectedDomain]?.pipelines[selectedPipeline]?.label}...
          </p>
        </div>
      )}

      {/* Step 3 — Results */}
      {step === 3 && agentRun && agentRun.status !== "failed" && (
        <div>
          <div className="card" style={{ padding: "1.25rem 1.5rem", marginBottom: "1.25rem", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: "0.9rem", marginBottom: 4 }}>
                {catalog[selectedDomain]?.pipelines[selectedPipeline]?.label}
                <span style={{ marginLeft: 8 }}><Badge variant="success">Completed</Badge></span>
              </div>
              <div style={{ fontSize: "0.8rem", color: "var(--ink-muted)" }}>{agentRun.original_filename} · {agentRun.processing_time_ms}ms</div>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button onClick={copyJSON} className="btn btn-ghost btn-sm">
                {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy JSON"}
              </button>
              <button onClick={() => window.open(`/api/export/agent/${agentRun.id}/csv`, "_blank")} className="btn btn-outline btn-sm">
                <Download size={14} /> CSV
              </button>
              <button onClick={() => window.open(`/api/export/agent/${agentRun.id}/excel`, "_blank")} className="btn btn-primary btn-sm">
                <FileSpreadsheet size={14} /> Excel
              </button>
              <button onClick={async () => {
                try {
                  await api.post(`/drive/export/${agentRun.id}`, { folder_id: "root" });
                  toast.success("Saved to Google Drive");
                } catch (err) { toast.error(errMsg(err, "Drive export failed")); }
              }} className="btn btn-outline btn-sm">
                ☁ Save to Drive
              </button>
            </div>
          </div>

          {agentRun.summary && (
            <div className="card" style={{ padding: "1.25rem 1.5rem", marginBottom: "1.25rem" }}>
              <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-muted)", marginBottom: "0.5rem" }}>AI Summary</div>
              <p style={{ color: "var(--ink-secondary)", fontSize: "0.9rem", lineHeight: 1.6, marginBottom: "1rem" }}>{agentRun.summary}</p>
              <ConfidenceBar score={agentRun.confidence_score || 0} />
            </div>
          )}

          <div className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "0 1.5rem", borderBottom: "1px solid var(--border)" }}>
              <div className="tabs" style={{ marginBottom: 0 }}>
                {["structured", "raw", "source"].map(t => (
                  <button key={t} className={`tab ${activeTab === t ? "active" : ""}`} onClick={() => setActiveTab(t)}>
                    {t === "structured" ? "Extracted Fields" : t === "raw" ? "Raw JSON" : "Source Text"}
                  </button>
                ))}
              </div>
            </div>
            <div style={{ padding: "1.5rem" }}>
              {activeTab === "structured" && agentRun.structured_result && <EditableStructuredResult data={agentRun.structured_result} runId={agentRun.id} />}
              {activeTab === "raw" && <div className="result-raw">{JSON.stringify(agentRun.structured_result, null, 2)}</div>}
              {activeTab === "source" && <div className="result-raw">{agentRun.input_text || "Source text preview not available"}</div>}
            </div>
          </div>
        </div>
      )}

      {step === 3 && agentRun?.status === "failed" && (
        <div className="card" style={{ padding: "2rem", textAlign: "center" }}>
          <div style={{ color: "var(--danger)", marginBottom: "0.75rem", fontWeight: 500 }}>Pipeline failed</div>
          <p style={{ color: "var(--ink-muted)", fontSize: "0.88rem", marginBottom: "1.25rem" }}>{agentRun.error_message}</p>
          <Button onClick={reset} variant="outline"><RotateCcw size={14} /> Try again</Button>
        </div>
      )}
    </div>
  );
}