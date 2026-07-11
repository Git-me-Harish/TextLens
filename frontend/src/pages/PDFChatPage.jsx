import { useState, useRef, useEffect, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useDropzone } from "react-dropzone";
import { Send, FileText, RotateCcw, Bot, User as UserIcon, Copy, Check, History } from "lucide-react";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Button, Spinner } from "../components/ui";

/* ─── Inline markdown renderer ──────────────────────────────────────── */
function InlineText({ text }) {
  const parts = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0, match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const raw = match[0];
    if (raw.startsWith("**"))
      parts.push(<strong key={match.index} style={{ fontWeight: 600, color: "var(--ink)" }}>{raw.slice(2, -2)}</strong>);
    else if (raw.startsWith("*"))
      parts.push(<em key={match.index}>{raw.slice(1, -1)}</em>);
    else
      parts.push(<code key={match.index} style={{ background: "var(--paper-2,#f0f2f5)", borderRadius: 3, padding: "0 4px", fontSize: "0.8rem", fontFamily: "monospace", color: "var(--accent)" }}>{raw.slice(1, -1)}</code>);
    last = match.index + raw.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

function MarkdownBlock({ content }) {
  if (!content) return null;
  const lines = content.split("\n");
  const elements = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const codeLines = []; i++;
      while (i < lines.length && !lines[i].startsWith("```")) { codeLines.push(lines[i]); i++; }
      elements.push(<pre key={i} style={{ background: "var(--paper-2,#f5f7f9)", borderRadius: 6, padding: "0.75rem 1rem", fontSize: "0.8rem", fontFamily: "monospace", overflowX: "auto", margin: "0.5rem 0", border: "1px solid var(--border)" }}><code>{codeLines.join("\n")}</code></pre>);
      i++; continue;
    }
    if (line.startsWith("|") && lines[i + 1]?.match(/^\|[-| :]+\|/)) {
      const tableLines = [];
      while (i < lines.length && lines[i].startsWith("|")) { tableLines.push(lines[i]); i++; }
      const rows = tableLines.filter(l => !l.match(/^\|[-| :]+\|/)).map(l => l.split("|").slice(1, -1).map(c => c.trim()));
      const [head, ...body] = rows;
      elements.push(
        <div key={i} style={{ overflowX: "auto", margin: "0.5rem 0" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.83rem" }}>
            <thead><tr>{(head||[]).map((c, ci) => <th key={ci} style={{ padding: "0.4rem 0.75rem", background: "var(--paper-2,#f5f7f9)", border: "1px solid var(--border)", textAlign: "left", fontWeight: 600, color: "var(--ink)", whiteSpace: "nowrap" }}>{c}</th>)}</tr></thead>
            <tbody>{body.map((row, ri) => <tr key={ri} style={{ background: ri%2===0?"#fff":"var(--paper-2,#f5f7f9)" }}>{row.map((cell, ci) => <td key={ci} style={{ padding: "0.4rem 0.75rem", border: "1px solid var(--border)", color: "var(--ink-secondary)" }}><InlineText text={cell} /></td>)}</tr>)}</tbody>
          </table>
        </div>
      );
      continue;
    }
    const hMatch = line.match(/^(#{1,3})\s+(.+)/);
    if (hMatch) {
      const sizes = { 1: "1rem", 2: "0.95rem", 3: "0.9rem" };
      elements.push(<div key={i} style={{ fontSize: sizes[hMatch[1].length], fontWeight: 700, color: "var(--ink)", margin: `${hMatch[1].length===1?"0.75rem":"0.5rem"} 0 0.25rem` }}><InlineText text={hMatch[2]} /></div>);
      i++; continue;
    }
    if (line.match(/^[-*]\s+/)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^[-*]\s+/)) { items.push(lines[i].replace(/^[-*]\s+/, "")); i++; }
      elements.push(<ul key={i} style={{ margin: "0.25rem 0 0.25rem 1.25rem", padding: 0 }}>{items.map((it, li) => <li key={li} style={{ color: "var(--ink-secondary)", fontSize: "0.875rem", lineHeight: 1.6, marginBottom: 2 }}><InlineText text={it} /></li>)}</ul>);
      continue;
    }
    if (line.match(/^\d+\.\s+/)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^\d+\.\s+/)) { items.push(lines[i].replace(/^\d+\.\s+/, "")); i++; }
      elements.push(<ol key={i} style={{ margin: "0.25rem 0 0.25rem 1.25rem", padding: 0 }}>{items.map((it, li) => <li key={li} style={{ color: "var(--ink-secondary)", fontSize: "0.875rem", lineHeight: 1.6, marginBottom: 2 }}><InlineText text={it} /></li>)}</ol>);
      continue;
    }
    if (line.trim() === "") { elements.push(<div key={i} style={{ height: 4 }} />); i++; continue; }
    elements.push(<p key={i} style={{ margin: "0.1rem 0", fontSize: "0.875rem", lineHeight: 1.65, color: "var(--ink-secondary)" }}><InlineText text={line} /></p>);
    i++;
  }
  return <div>{elements}</div>;
}

/* ─── Message bubble ─────────────────────────────────────────────── */
function MessageBubble({ msg }) {
  const [copied, setCopied] = useState(false);
  const isUser = msg.role === "user";
  if (msg.role === "system") {
    return (
      <div style={{ display: "flex", justifyContent: "center", margin: "0.25rem 0" }}>
        <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)", background: "var(--paper-2,#f5f7f9)", border: "1px solid var(--border)", borderRadius: 20, padding: "0.3rem 0.875rem" }}>{msg.content}</div>
      </div>
    );
  }
  const copy = () => {
    navigator.clipboard.writeText(msg.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div style={{ display: "flex", gap: 10, flexDirection: isUser ? "row-reverse" : "row", alignItems: "flex-start" }}>
      <div style={{ width: 28, height: 28, borderRadius: "50%", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: isUser ? "var(--accent)" : "var(--paper-2,#f0f2f5)", border: isUser ? "none" : "1px solid var(--border)", marginTop: 2 }}>
        {isUser ? <UserIcon size={13} color="#fff" /> : <Bot size={13} color="var(--accent)" />}
      </div>
      <div style={{ maxWidth: "78%", position: "relative" }} className="group">
        <div style={{ padding: "0.65rem 0.9rem", borderRadius: isUser ? "16px 4px 16px 16px" : "4px 16px 16px 16px", background: isUser ? "var(--accent)" : "#fff", border: isUser ? "none" : "1px solid var(--border)", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}>
          {isUser
            ? <p style={{ margin: 0, fontSize: "0.875rem", color: "#fff", lineHeight: 1.55 }}>{msg.content}</p>
            : <MarkdownBlock content={msg.content} />
          }
        </div>
        {/* Copy button — only on assistant messages */}
        {!isUser && (
          <button onClick={copy} title="Copy response" style={{ position: "absolute", top: 6, right: -32, background: "none", border: "none", cursor: "pointer", padding: 4, color: copied ? "var(--accent)" : "var(--ink-muted)", opacity: 0.7, transition: "opacity 0.15s" }}
            onMouseEnter={e => e.currentTarget.style.opacity = "1"}
            onMouseLeave={e => e.currentTarget.style.opacity = "0.7"}
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
          </button>
        )}
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
      <div style={{ width: 28, height: 28, borderRadius: "50%", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "var(--paper-2,#f0f2f5)", border: "1px solid var(--border)", marginTop: 2 }}>
        <Bot size={13} color="var(--accent)" />
      </div>
      <div style={{ padding: "0.65rem 1rem", borderRadius: "4px 16px 16px 16px", background: "#fff", border: "1px solid var(--border)", display: "flex", gap: 5, alignItems: "center" }}>
        {[0,1,2].map(i => <span key={i} style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--ink-muted)", animation: `pulse 1.2s ease-in-out ${i*0.2}s infinite` }} />)}
      </div>
    </div>
  );
}

/* ─── Main page ──────────────────────────────────────────────────── */
export default function PDFChatPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [session, setSession] = useState(null);       // {id, title, job_id, suggested_questions}
  const [messages, setMessages] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [input, setInput] = useState("");
  const [asking, setAsking] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, asking]);

  // Resume from history via ?session=ID
  useEffect(() => {
    const sid = searchParams.get("session");
    if (!sid) return;
    setResuming(true);
    api.get(`/chat/sessions/${sid}`)
      .then(({ data }) => {
        setSession({ id: data.id, title: data.title, job_id: data.job_id, suggested_questions: data.suggested_questions });
        setMessages([
          { role: "system", content: `Resumed: ${data.original_filename}` },
          ...data.messages,
        ]);
        setTimeout(() => inputRef.current?.focus(), 100);
      })
      .catch(() => toast.error("Could not load session"))
      .finally(() => setResuming(false));
  }, [searchParams]);

  // Upload + start session
  const onDrop = useCallback(async ([file]) => {
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append("file", file);
    form.append("job_type", "pdf_extract");
    try {
      const { data: job } = await api.post("/jobs/upload", form, { headers: { "Content-Type": "multipart/form-data" } });
      let polled = job;
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 1500));
        const res = await api.get(`/jobs/${polled.id}`);
        polled = res.data;
        if (polled.status === "completed" || polled.status === "failed") break;
      }
      if (polled.status !== "completed") { toast.error("Failed to extract document"); return; }

      const { data: sess } = await api.post("/chat/sessions", { job_id: polled.id });
      setSession({ id: sess.session_id, title: sess.title, job_id: polled.id, suggested_questions: sess.suggested_questions });
      setMessages([{ role: "system", content: `${file.name} · ${polled.page_count ?? "?"} pages · ready` }]);
      toast.success("Document ready");
      setTimeout(() => inputRef.current?.focus(), 100);
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409 && detail?.code === "duplicate_file") {
        // File already processed — offer to open existing job directly
        toast((t) => (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>Already processed</div>
            <div style={{ fontSize: "0.8rem", color: "#666" }}>
              <strong>{detail.original_filename}</strong> was uploaded before.
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                onClick={async () => {
                  toast.dismiss(t.id);
                  setUploading(true);
                  try {
                    const { data: sess } = await api.post("/chat/sessions", { job_id: detail.existing_job_id });
                    const { data: job } = await api.get(`/jobs/${detail.existing_job_id}`);
                    setSession({ id: sess.session_id, title: sess.title, job_id: detail.existing_job_id, suggested_questions: sess.suggested_questions });
                    setMessages([{ role: "system", content: `Loaded: ${detail.original_filename}` }]);
                  } catch {
                    toast.error("Failed to load existing document");
                  } finally {
                    setUploading(false);
                  }
                }}
                style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 6, padding: "0.3rem 0.75rem", cursor: "pointer", fontSize: "0.78rem" }}
              >
                Open existing
              </button>
              <button
                onClick={() => toast.dismiss(t.id)}
                style={{ background: "none", border: "1px solid #ddd", borderRadius: 6, padding: "0.3rem 0.75rem", cursor: "pointer", fontSize: "0.78rem" }}
              >
                Dismiss
              </button>
            </div>
          </div>
        ), { duration: 10000 });
      } else {
        toast.error(typeof detail === "string" ? detail : "Upload failed");
      }
    } finally {
      setUploading(false);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop, accept: { "application/pdf": [] }, maxFiles: 1, disabled: uploading });

  const send = async () => {
    const q = input.trim();
    if (!q || !session || asking) return;
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: q }]);
    setAsking(true);
    try {
      const { data } = await api.post("/chat/ask", { session_id: session.id, message: q });
      setMessages(prev => [...prev, { role: "assistant", content: data.answer }]);
    } catch (err) {
      const detail = err.response?.data?.detail || "Something went wrong";
      toast.error(errMsg(err, "Operation failed"));
      setMessages(prev => [...prev, { role: "assistant", content: `**Error:** ${detail}` }]);
    } finally {
      setAsking(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  const reset = () => { setSession(null); setMessages([]); setInput(""); navigate("/tools/pdf-chat"); };

  const chatMessages = messages.filter(m => m.role !== "system");
  const showSuggestions = session && chatMessages.length === 0 && (session.suggested_questions?.length > 0);

  if (resuming) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "60vh", gap: 12, color: "var(--ink-muted)" }}>
      <Spinner size={20} /> Loading session...
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 4rem)" }}>
      {/* Header */}
      <div className="page-header" style={{ paddingBottom: "0.75rem", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h1 className="page-title">PDF Chat</h1>
            <p className="page-subtitle">{session ? session.title : "Upload a PDF and ask anything about it"}</p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => navigate("/chat-history")} style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: "1px solid var(--border)", borderRadius: 8, padding: "0.4rem 0.75rem", cursor: "pointer", fontSize: "0.8rem", color: "var(--ink-muted)" }}>
              <History size={13} /> History
            </button>
            {session && (
              <button onClick={reset} style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: "1px solid var(--border)", borderRadius: 8, padding: "0.4rem 0.75rem", cursor: "pointer", fontSize: "0.8rem", color: "var(--ink-muted)" }}>
                <RotateCcw size={13} /> New
              </button>
            )}
          </div>
        </div>
      </div>

      {!session ? (
        <div className="card" style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          {uploading ? (
            <div style={{ textAlign: "center" }}>
              <Spinner size={32} />
              <p style={{ marginTop: 12, color: "var(--ink-muted)", fontSize: "0.875rem" }}>Extracting document...</p>
            </div>
          ) : (
            <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`} style={{ width: "100%", maxWidth: 480 }}>
              <input {...getInputProps()} />
              <div className="dropzone-icon"><FileText size={32} /></div>
              <div className="dropzone-title">Drop your PDF here</div>
              <div className="dropzone-sub">or click to browse · PDF only · max 50 MB</div>
            </div>
          )}
        </div>
      ) : (
        <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", padding: 0 }}>
          {/* Messages */}
          <div style={{ flex: 1, overflowY: "auto", padding: "1.25rem 1.25rem 0.5rem", display: "flex", flexDirection: "column", gap: "0.875rem" }}>
            {messages.map((msg, i) => <MessageBubble key={i} msg={msg} />)}
            {asking && <TypingIndicator />}
            <div ref={bottomRef} />
          </div>

          {/* Suggested questions — doc-specific, shown until first user message */}
          {showSuggestions && (
            <div style={{ padding: "0.5rem 1.25rem", display: "flex", gap: 8, flexWrap: "wrap" }}>
              {session.suggested_questions.map(q => (
                <button key={q} onClick={() => { setInput(q); inputRef.current?.focus(); }} style={{ background: "var(--paper-2,#f5f7f9)", border: "1px solid var(--border)", borderRadius: 20, padding: "0.3rem 0.8rem", fontSize: "0.78rem", color: "var(--ink-secondary)", cursor: "pointer", whiteSpace: "nowrap" }}>
                  {q}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div style={{ padding: "0.875rem 1.25rem", borderTop: "1px solid var(--border)", display: "flex", gap: 10, alignItems: "flex-end", background: "#fff", flexShrink: 0 }}>
            <textarea ref={inputRef} value={input} onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder="Ask anything about this document… (Enter to send, Shift+Enter for newline)"
              className="form-input" disabled={asking} rows={1}
              style={{ flex: 1, resize: "none", minHeight: 40, maxHeight: 120, overflowY: "auto", lineHeight: 1.5, paddingTop: "0.55rem", paddingBottom: "0.55rem" }}
            />
            <Button onClick={send} disabled={asking || !input.trim()} style={{ flexShrink: 0, height: 40, width: 40, padding: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
              {asking ? <Spinner size={15} /> : <Send size={16} />}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}