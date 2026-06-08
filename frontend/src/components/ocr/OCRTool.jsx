import { useState, useCallback, useEffect } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, Download, Copy, Check } from "lucide-react";
import toast from "react-hot-toast";
import api from "../../lib/api";
import { Button, Badge, Spinner } from "../ui";

const STATUS_BADGE = { completed: "success", failed: "danger", processing: "processing", pending: "warning" };

export default function OCRTool({ title, desc, jobType, acceptedTypes, acceptLabel, children }) {
  const [job, setJob] = useState(null);
  const [polling, setPolling] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [copied, setCopied] = useState(false);

  // Poll until job completes
  useEffect(() => {
    if (!job || job.status === "completed" || job.status === "failed") {
      setPolling(false);
      return;
    }
    setPolling(true);
    const interval = setInterval(async () => {
      try {
        const { data } = await api.get(`/jobs/${job.id}`);
        setJob(data);
        if (data.status === "completed" || data.status === "failed") {
          clearInterval(interval);
          setPolling(false);
          if (data.status === "completed") toast.success("Processing complete");
          else toast.error("Processing failed");
        }
      } catch { clearInterval(interval); setPolling(false); }
    }, 2000);
    return () => clearInterval(interval);
  }, [job?.id, job?.status]);

  const onDrop = useCallback(async (accepted) => {
    const file = accepted[0];
    if (!file) return;
    setUploading(true);
    setJob(null);
    const form = new FormData();
    form.append("file", file);
    form.append("job_type", jobType);
    try {
      const { data } = await api.post("/jobs/upload", form, { headers: { "Content-Type": "multipart/form-data" } });
      setJob(data);
      toast("File uploaded, processing...", { icon: "..." });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [jobType]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop, accept: acceptedTypes, maxFiles: 1 });

  const copyText = () => {
    navigator.clipboard.writeText(job?.result_text || "");
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const downloadResult = () => {
    window.open(`/api/jobs/${job.id}/download`, "_blank");
  };

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">{title}</h1>
        <p className="page-subtitle">{desc}</p>
      </div>

      {/* Upload zone */}
      <div className="card" style={{ padding: "1.5rem", marginBottom: "1.5rem" }}>
        <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`}>
          <input {...getInputProps()} />
          {uploading ? (
            <div>
              <Spinner />
              <p style={{ marginTop: 12, color: "var(--ink-muted)" }}>Uploading...</p>
            </div>
          ) : (
            <>
              <div className="dropzone-icon"><Upload size={32} /></div>
              <div className="dropzone-title">Drop your file here or click to browse</div>
              <div className="dropzone-sub">{acceptLabel}</div>
            </>
          )}
        </div>
      </div>

      {/* Extra controls passed from parent */}
      {children && job && (
        <div className="card" style={{ padding: "1.5rem", marginBottom: "1.5rem" }}>
          {children({ job, setJob })}
        </div>
      )}

      {/* Result */}
      {job && (
        <div className="card fade-in" style={{ padding: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem", flexWrap: "wrap", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontWeight: 500, fontSize: "0.9rem" }}>{job.original_filename}</span>
              <Badge variant={STATUS_BADGE[job.status] || "default"}>{job.status}</Badge>
              {polling && <Spinner size={16} />}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              {job.result_text && (
                <Button variant="ghost" size="sm" onClick={copyText}>
                  {copied ? <Check size={15} /> : <Copy size={15} />}
                  {copied ? "Copied" : "Copy"}
                </Button>
              )}
              {job.result_file_path && (
                <Button variant="outline" size="sm" onClick={downloadResult}>
                  <Download size={15} /> Download
                </Button>
              )}
            </div>
          </div>

          {job.status === "processing" && (
            <div style={{ padding: "2rem", textAlign: "center", color: "var(--ink-muted)" }} className="pulsing">
              Processing your document...
            </div>
          )}

          {job.status === "failed" && (
            <div style={{ padding: "1rem", background: "var(--danger-light)", borderRadius: "var(--radius)", color: "var(--danger)", fontSize: "0.88rem" }}>
              {job.error_message || "Processing failed"}
            </div>
          )}

          {job.status === "completed" && job.result_text && (
            <div>
              {job.page_count && (
                <p style={{ fontSize: "0.78rem", color: "var(--ink-muted)", marginBottom: "0.75rem" }}>
                  {job.page_count} section{job.page_count !== 1 ? "s" : ""} · {job.processing_time_ms}ms
                </p>
              )}
              <div className="result-panel">{job.result_text}</div>
            </div>
          )}

          {job.status === "completed" && job.result_file_path && !job.result_text && (
            <div style={{ padding: "1.5rem", textAlign: "center" }}>
              <p style={{ marginBottom: "1rem", color: "var(--ink-secondary)" }}>Your file is ready</p>
              <Button onClick={downloadResult}><Download size={16} /> Download result</Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
