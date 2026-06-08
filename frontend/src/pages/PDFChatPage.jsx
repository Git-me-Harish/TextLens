import { useState, useRef, useEffect } from "react";
import { useDropzone } from "react-dropzone";
import { Send, Upload } from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import { Button, Spinner } from "../components/ui";

export default function PDFChatPage() {
  const [baseJob, setBaseJob] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const onDrop = async ([file]) => {
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append("file", file);
    form.append("job_type", "pdf_extract");
    try {
      const { data } = await api.post("/jobs/upload", form, { headers: { "Content-Type": "multipart/form-data" } });
      let job = data;
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 1500));
        const res = await api.get(`/jobs/${job.id}`);
        job = res.data;
        if (job.status === "completed" || job.status === "failed") break;
      }
      if (job.status === "completed") {
        setBaseJob(job);
        setMessages([{ role: "system", text: `Document loaded: ${file.name}. Ask me anything about it.` }]);
        toast.success("Document ready for questions");
      } else {
        toast.error("Failed to process document");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop, accept: { "application/pdf": [] }, maxFiles: 1 });

  const ask = async () => {
    if (!question.trim() || !baseJob) return;
    const q = question;
    setQuestion("");
    setMessages(m => [...m, { role: "user", text: q }]);
    setAsking(true);
    try {
      const { data: qaJob } = await api.post("/jobs/ask", { question: q, job_id: baseJob.id });
      let job = qaJob;
      for (let i = 0; i < 20; i++) {
        await new Promise(r => setTimeout(r, 1200));
        const res = await api.get(`/jobs/${job.id}`);
        job = res.data;
        if (job.status === "completed" || job.status === "failed") break;
      }
      setMessages(m => [...m, { role: "assistant", text: job.result_text || "No answer found." }]);
    } catch {
      setMessages(m => [...m, { role: "assistant", text: "Error processing your question." }]);
    } finally {
      setAsking(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 4rem)" }}>
      <div className="page-header">
        <h1 className="page-title">PDF Chat</h1>
        <p className="page-subtitle">Upload a PDF and ask questions about its content</p>
      </div>

      {!baseJob ? (
        <div className="card" style={{ padding: "1.5rem", flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          {uploading ? (
            <div style={{ textAlign: "center" }}>
              <Spinner size={32} />
              <p style={{ marginTop: 12, color: "var(--ink-muted)" }}>Processing document...</p>
            </div>
          ) : (
            <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`} style={{ width: "100%", maxWidth: 500 }}>
              <input {...getInputProps()} />
              <div className="dropzone-icon"><Upload size={32} /></div>
              <div className="dropzone-title">Drop your PDF here</div>
              <div className="dropzone-sub">PDF files only</div>
            </div>
          )}
        </div>
      ) : (
        <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ flex: 1, overflowY: "auto", padding: "1.25rem", display: "flex", flexDirection: "column", gap: "0.875rem" }}>
            {messages.map((msg, i) => (
              <div key={i} style={{ display: "flex", justifyContent: msg.role === "user" ? "flex-end" : "flex-start" }}>
                <div style={{
                  maxWidth: "75%", padding: "0.6rem 1rem",
                  borderRadius: msg.role === "user" ? "16px 16px 4px 16px" : "4px 16px 16px 16px",
                  background: msg.role === "user" ? "var(--accent)" : msg.role === "system" ? "var(--paper-3)" : "#fff",
                  color: msg.role === "user" ? "#fff" : "var(--ink-secondary)",
                  border: msg.role === "assistant" ? "1px solid var(--border)" : "none",
                  fontSize: "0.875rem", lineHeight: 1.55,
                }}>
                  {msg.text}
                </div>
              </div>
            ))}
            {asking && (
              <div style={{ display: "flex", justifyContent: "flex-start" }}>
                <div style={{ padding: "0.6rem 1rem", borderRadius: "4px 16px 16px 16px", border: "1px solid var(--border)", background: "#fff" }} className="pulsing">
                  <Spinner size={16} />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
          <div style={{ padding: "1rem 1.25rem", borderTop: "1px solid var(--border)", display: "flex", gap: 10 }}>
            <input value={question} onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !e.shiftKey && ask()}
              placeholder="Ask a question about this document..." className="form-input" style={{ flex: 1 }} disabled={asking} />
            <Button onClick={ask} disabled={asking || !question.trim()}><Send size={16} /></Button>
          </div>
        </div>
      )}
    </div>
  );
}
