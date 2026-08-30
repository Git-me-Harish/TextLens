import { useState, useRef, useEffect, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useDropzone } from "react-dropzone";
import {
  Send, FileText, RotateCcw, Bot, User as UserIcon,
  Copy, Check, History, Download, Zap, Brain, MessageSquarePlus,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Spinner, Badge } from "../components/ui";
import { waitForJobSSE } from "../hooks/useSSE";

const MAX_QUESTION_LENGTH = 2000; // mirrors AskRequest on the backend

/*  Inline markdown renderer (unchanged from V1)  */
function InlineText({ text }) {
  const parts = []; const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0, match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const raw = match[0];
    if (raw.startsWith("**")) parts.push(<strong key={match.index} style={{ fontWeight: 600, color: "var(--ink)" }}>{raw.slice(2, -2)}</strong>);
    else if (raw.startsWith("*")) parts.push(<em key={match.index}>{raw.slice(1, -1)}</em>);
    else parts.push(<code key={match.index} style={{ background: "var(--paper-2,#f0f2f5)", borderRadius: 3, padding: "0 4px", fontSize: "0.8rem", fontFamily: "monospace", color: "var(--accent)" }}>{raw.slice(1, -1)}</code>);
    last = match.index + raw.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

function MarkdownBlock({ content }) {
  if (!content) return null;
  const lines = content.split("\n"); const elements = []; let i = 0;
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
      elements.push(<div key={i} style={{ overflowX: "auto", margin: "0.5rem 0" }}><table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.83rem" }}><thead><tr>{(head||[]).map((c,ci) => <th key={ci} style={{ padding: "0.4rem 0.75rem", background: "var(--paper-2,#f5f7f9)", border: "1px solid var(--border)", textAlign: "left", fontWeight: 600, whiteSpace: "nowrap" }}>{c}</th>)}</tr></thead><tbody>{body.map((row,ri) => <tr key={ri} style={{ background: ri%2===0?"#fff":"var(--paper-2,#f5f7f9)" }}>{row.map((cell,ci) => <td key={ci} style={{ padding: "0.4rem 0.75rem", border: "1px solid var(--border)", color: "var(--ink-secondary)" }}><InlineText text={cell} /></td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    const hMatch = line.match(/^(#{1,3})\s+(.+)/);
    if (hMatch) { const sizes = { 1: "1rem", 2: "0.95rem", 3: "0.9rem" }; elements.push(<div key={i} style={{ fontSize: sizes[hMatch[1].length], fontWeight: 700, color: "var(--ink)", margin: `${hMatch[1].length===1?"0.75rem":"0.5rem"} 0 0.25rem` }}><InlineText text={hMatch[2]} /></div>); i++; continue; }
    if (line.match(/^[-*]\s+/)) { const items = []; while (i < lines.length && lines[i].match(/^[-*]\s+/)) { items.push(lines[i].replace(/^[-*]\s+/, "")); i++; } elements.push(<ul key={i} style={{ margin: "0.25rem 0 0.25rem 1.25rem", padding: 0 }}>{items.map((it,li) => <li key={li} style={{ color: "var(--ink-secondary)", fontSize: "0.875rem", lineHeight: 1.6, marginBottom: 2 }}><InlineText text={it} /></li>)}</ul>); continue; }
    if (line.match(/^\d+\.\s+/)) { const items = []; while (i < lines.length && lines[i].match(/^\d+\.\s+/)) { items.push(lines[i].replace(/^\d+\.\s+/, "")); i++; } elements.push(<ol key={i} style={{ margin: "0.25rem 0 0.25rem 1.25rem", padding: 0 }}>{items.map((it,li) => <li key={li} style={{ color: "var(--ink-secondary)", fontSize: "0.875rem", lineHeight: 1.6, marginBottom: 2 }}><InlineText text={it} /></li>)}</ol>); continue; }
    if (line.trim() === "") { elements.push(<div key={i} style={{ height: 4 }} />); i++; continue; }
    elements.push(<p key={i} style={{ margin: "0.1rem 0", fontSize: "0.875rem", lineHeight: 1.65, color: "var(--ink-secondary)" }}><InlineText text={line} /></p>);
    i++;
  }
  return <div>{elements}</div>;
}

/*  Context source badge  */
function SourceBadge({ source }) {
  if (!source || source === "none") return null;
  const isRag = source === "rag";
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      fontSize: "0.65rem", fontWeight: 700,
      padding: "2px 7px", borderRadius: 20,
      background: isRag ? "#ede9fe" : "#fef9c3",
      color: isRag ? "#7c3aed" : "#92400e",
      border: `1px solid ${isRag ? "#c4b5fd" : "#fde68a"}`,
      letterSpacing: "0.04em",
    }}>
      {isRag ? <><Brain size={9} /> RAG</> : <><Zap size={9} /> Extractive</>}
    </span>
  );
}

/*  Message bubble  */
function MessageBubble({ msg }) {
  const [copied, setCopied] = useState(false);
  const isUser = msg.role === "user";
  if (msg.role === "system") {
    return (
      <div style={{ display: "flex", justifyContent: "center", margin: "0.25rem 0" }}>
        <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)", background: "var(--paper-2,#f5f7f9)", border: "1px solid var(--border)", borderRadius: 20, padding: "0.3rem 0.875rem" }}>
          {msg.content}
        </div>
      </div>
    );
  }
  const copy = () => { navigator.clipboard.writeText(msg.content); setCopied(true); setTimeout(() => setCopied(false), 2000); };
  return (
    <div style={{ display: "flex", gap: 10, flexDirection: isUser ? "row-reverse" : "row", alignItems: "flex-start" }}>
      <div style={{ width: 28, height: 28, borderRadius: "50%", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: isUser ? "var(--accent)" : "var(--paper-2,#f0f2f5)", border: isUser ? "none" : "1px solid var(--border)", marginTop: 2 }}>
        {isUser ? <UserIcon size={13} color="#fff" /> : <Bot size={13} color="var(--accent)" />}
      </div>
      <div style={{ maxWidth: "78%", position: "relative" }}>
        <div style={{ padding: "0.65rem 0.9rem", borderRadius: isUser ? "16px 4px 16px 16px" : "4px 16px 16px 16px", background: isUser ? "var(--accent)" : "#fff", border: isUser ? "none" : "1px solid var(--border)", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}>
          {isUser
            ? <p style={{ margin: 0, fontSize: "0.875rem", color: "#fff", lineHeight: 1.55 }}>{msg.content}</p>
            : (
              <>
                <MarkdownBlock content={msg.content} />
                {msg.context_source && (
                  <div style={{ marginTop: 8 }}>
                    <SourceBadge source={msg.context_source} />
                  </div>
                )}
              </>
            )
          }
        </div>
        {!isUser && (
          <button onClick={copy} title="Copy response" style={{ position: "absolute", top: 6, right: -32, background: "none", border: "none", cursor: "pointer", padding: 4, color: copied ? "var(--accent)" : "var(--ink-muted)", opacity: 0.7 }}
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

const JOB_TYPE_LABEL = {
  ocr_image: "Image OCR",
  pdf_extract: "PDF Extract",
  pdf_to_markdown: "Markdown",
  pdf_summarize: "Summary",
};

/* Existing-document picker — lets a user start a chat session against a
   document they already processed, instead of always re-uploading. */
function DocumentPicker({ documents, loading, onSelect, selectingId }) {
  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 52, borderRadius: 8 }} />)}
      </div>
    );
  }
  if (documents.length === 0) {
    return (
      <div style={{ fontSize: "0.8rem", color: "var(--ink-muted)", textAlign: "center", padding: "1.5rem 0" }}>
        No processed documents yet — upload one to get started.
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 320, overflowY: "auto" }}>
      {documents.map(doc => (
        <button
          key={doc.id}
          onClick={() => onSelect(doc)}
          disabled={selectingId !== null}
          style={{
            display: "flex", alignItems: "center", gap: 10, textAlign: "left",
            padding: "0.625rem 0.75rem", borderRadius: 8, border: "1px solid var(--border)",
            background: "#fff", cursor: selectingId ? "default" : "pointer",
            opacity: selectingId && selectingId !== doc.id ? 0.5 : 1,
          }}
        >
          <FileText size={15} style={{ color: "var(--accent)", flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: "0.82rem", fontWeight: 500, color: "var(--ink)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {doc.original_filename}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
              <span style={{ fontSize: "0.7rem", color: "var(--ink-muted)" }}>
                {formatDistanceToNow(new Date(doc.created_at), { addSuffix: true })}
              </span>
              {doc.has_chunks && <Badge variant="accent">RAG-ready</Badge>}
            </div>
          </div>
          {selectingId === doc.id ? <Spinner size={16} /> : (
            <span style={{ fontSize: "0.68rem", color: "var(--ink-muted)", flexShrink: 0 }}>
              {JOB_TYPE_LABEL[doc.job_type] || doc.job_type}
            </span>
          )}
        </button>
      ))}
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

/*  Main page  */
export default function PDFChatPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [session, setSession]   = useState(null);
  const [messages, setMessages] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [resuming, setResuming]   = useState(false);
  const [input, setInput]         = useState("");
  const [asking, setAsking]       = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [documents, setDocuments] = useState([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [selectingId, setSelectingId] = useState(null);
  const bottomRef = useRef(null);
  const inputRef  = useRef(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, asking]);

  // Documents available for the "use an existing PDF" picker
  useEffect(() => {
    api.get("/chat/documents")
      .then(({ data }) => setDocuments(data))
      .catch(() => {}) // non-critical — picker just shows empty state
      .finally(() => setDocsLoading(false));
  }, []);

  const startSessionForJob = useCallback(async (jobId, filename) => {
    setSelectingId(jobId);
    try {
      const { data: sess } = await api.post("/chat/sessions", { job_id: jobId });
      setSession({ id: sess.id, title: sess.title, job_id: jobId, suggested_questions: sess.suggested_questions, filename });
      setMessages([{ role: "system", content: `Document loaded: ${filename}` }]);
      setTimeout(() => inputRef.current?.focus(), 100);
    } catch (err) {
      toast.error(errMsg(err, "Could not start chat session"));
    } finally {
      setSelectingId(null);
    }
  }, []);

  // Resume from ?session=ID
  useEffect(() => {
    const sid = searchParams.get("session");
    if (!sid) return;
    setResuming(true);
    api.get(`/chat/sessions/${sid}`)
      .then(({ data }) => {
        setSession({ id: data.id, title: data.title, job_id: data.job_id, suggested_questions: data.suggested_questions, filename: data.original_filename });
        setMessages([{ role: "system", content: `Resumed: ${data.original_filename}` }, ...data.messages]);
        setTimeout(() => inputRef.current?.focus(), 100);
      })
      .catch(() => toast.error("Could not load session"))
      .finally(() => setResuming(false));
  }, [searchParams]);

  // Upload + start session — waitForJobSSE replaces polling for loop
  const onDrop = useCallback(async ([file]) => {
    if (!file) return;
    setUploading(true);
    setStatusMsg("Uploading document…");
    const form = new FormData();
    form.append("file", file);
    form.append("job_type", "pdf_extract");
    try {
      const { data: submitted } = await api.post("/jobs/upload", form, { headers: { "Content-Type": "multipart/form-data" } });
      setStatusMsg("Extracting text…");

      // Resolve via SSE — instant, no polling
      const done = await waitForJobSSE(submitted.id, api);
      if (done.status !== "completed") { toast.error(done.error_message || "Extraction failed"); return; }

      setStatusMsg("Creating chat session…");
      await startSessionForJob(done.id || submitted.id, file.name);
      // Newly-uploaded documents won't have chunks yet (ingestion runs
      // async after this), so refresh the picker list in the background —
      // next visit will show it as available with its RAG-ready badge.
      api.get("/chat/documents").then(({ data }) => setDocuments(data)).catch(() => {});
      toast.success("Ready — start asking questions");
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409 && detail?.code === "duplicate_file") {
        toast(
          (t) => (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>Already processed</div>
              <div style={{ fontSize: "0.8rem", color: "#666" }}>Use the existing extracted text to start a chat.</div>
              <button onClick={() => {
                toast.dismiss(t.id);
                startSessionForJob(detail.existing_job_id, detail.original_filename);
              }} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 6, padding: "0.3rem 0.75rem", cursor: "pointer", fontSize: "0.78rem", alignSelf: "flex-start" }}>
                Use existing
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
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [] },
    maxFiles: 1,
    disabled: uploading,
  });

  const sendMessage = async (text) => {
    const question = text.trim();
    if (!question || !session || asking) return;
    if (question.length > MAX_QUESTION_LENGTH) {
      toast.error(`Keep questions under ${MAX_QUESTION_LENGTH} characters.`);
      return;
    }
    const userMsg = { role: "user", content: question };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setAsking(true);
    try {
      // Body key must be `message` — matches AskRequest on the backend
      // (backend/app/api/routes/chat.py). This previously sent `question`,
      // which the route never read, so every send here 404'd/validated-out.
      const { data } = await api.post(`/chat/sessions/${session.id}/ask`, { message: question });
      setMessages(prev => [...prev, {
        role: "assistant",
        content: data.answer || "I couldn't find an answer in the document.",
        context_source: data.context_source,
      }]);
    } catch (err) {
      toast.error(errMsg(err, "Failed to get answer"));
      // Roll back the optimistic user message so a failed send doesn't
      // silently look like it was accepted.
      setMessages(prev => prev.filter(m => m !== userMsg));
      setInput(question);
    } finally {
      setAsking(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  const exportConversation = () => {
    const lines = [`# Chat: ${session?.filename || session?.title || "PDF Chat"}`, ""];
    messages.forEach(msg => {
      if (msg.role === "system") return;
      lines.push(`## ${msg.role === "user" ? "You" : "Assistant"}`);
      lines.push(msg.content);
      if (msg.context_source) lines.push(`*Source: ${msg.context_source === "rag" ? "RAG retrieval" : "Extractive"}*`);
      lines.push("");
    });
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `chat_${(session?.filename || "conversation").replace(/\.pdf$/i, "")}.md`;
    a.click();
  };

  const reset = () => {
    setSession(null);
    setMessages([]);
    setInput("");
    api.get("/chat/documents").then(({ data }) => setDocuments(data)).catch(() => {});
  };

  const humanMessages = messages.filter(m => m.role !== "system");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 120px)", minHeight: 500 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.875rem" }}>
        <div>
          <h1 className="page-title" style={{ margin: 0 }}>PDF Chat</h1>
          {session && (
            <p style={{ margin: "2px 0 0", fontSize: "0.78rem", color: "var(--ink-muted)" }}>
              {session.filename || session.title}
            </p>
          )}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {session && humanMessages.length > 0 && (
            <button onClick={exportConversation} className="btn btn-ghost btn-sm" title="Export as Markdown">
              <Download size={13} /> Export
            </button>
          )}
          <button onClick={() => navigate("/chat-history")} className="btn btn-ghost btn-sm">
            <History size={13} /> History
          </button>
          {session && (
            <button onClick={reset} className="btn btn-ghost btn-sm">
              <RotateCcw size={13} /> New
            </button>
          )}
        </div>
      </div>

      {/* Upload state — new document, or pick one already processed */}
      {!session && (
        resuming ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Spinner size={28} />
          </div>
        ) : (
          <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.25rem", minHeight: 0 }}>
            <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`}>
              <input {...getInputProps()} />
              {uploading ? (
                <div style={{ textAlign: "center" }}>
                  <Spinner size={28} />
                  <p style={{ marginTop: 12, fontSize: "0.88rem", color: "var(--ink-muted)" }} className="pulsing">
                    {statusMsg || "Processing…"}
                  </p>
                </div>
              ) : (
                <>
                  <div className="dropzone-icon"><FileText size={28} /></div>
                  <div className="dropzone-title">Drop a new PDF to start chatting</div>
                  <div className="dropzone-sub">
                    Ask anything — answers are grounded in your document using RAG
                  </div>
                </>
              )}
            </div>

            <div className="card" style={{ padding: "1.25rem", overflowY: "auto" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: "0.875rem" }}>
                <MessageSquarePlus size={16} style={{ color: "var(--accent)" }} />
                <span style={{ fontWeight: 600, fontSize: "0.88rem" }}>Or chat with a document you already processed</span>
              </div>
              <DocumentPicker
                documents={documents}
                loading={docsLoading}
                onSelect={(doc) => startSessionForJob(doc.id, doc.original_filename)}
                selectingId={selectingId}
              />
            </div>
          </div>
        )
      )}

      {/* Chat interface */}
      {session && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 0, overflow: "hidden" }}>
          {/* Suggested questions */}
          {session.suggested_questions?.length > 0 && humanMessages.length === 0 && (
            <div style={{ padding: "0.75rem 0", marginBottom: "0.5rem" }}>
              <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-muted)", marginBottom: "0.625rem" }}>
                Suggested questions
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {session.suggested_questions.map((q, i) => (
                  <button key={i} onClick={() => sendMessage(q)} style={{ background: "var(--paper)", border: "1px solid var(--border)", borderRadius: 20, padding: "0.35rem 0.875rem", cursor: "pointer", fontSize: "0.8rem", color: "var(--ink-secondary)", transition: "all 0.15s" }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.color = "var(--accent)"; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.color = "var(--ink-secondary)"; }}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Messages */}
          <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "0.875rem", padding: "0.5rem 0", paddingBottom: "0.5rem" }}>
            {messages.map((msg, i) => <MessageBubble key={i} msg={msg} />)}
            {asking && <TypingIndicator />}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div style={{ paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                ref={inputRef}
                className="form-input"
                value={input}
                onChange={e => setInput(e.target.value.slice(0, MAX_QUESTION_LENGTH))}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(input); } }}
                placeholder="Ask a question about your document…"
                disabled={asking}
                maxLength={MAX_QUESTION_LENGTH}
                style={{ flex: 1 }}
              />
              <button
                onClick={() => sendMessage(input)}
                disabled={!input.trim() || asking}
                style={{
                  width: 40, height: 40, borderRadius: 10, flexShrink: 0,
                  background: input.trim() && !asking ? "var(--accent)" : "var(--border)",
                  border: "none", cursor: input.trim() && !asking ? "pointer" : "not-allowed",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  transition: "background 0.15s",
                }}
              >
                <Send size={16} color={input.trim() && !asking ? "#fff" : "var(--ink-muted)"} />
              </button>
            </div>
            {input.length > MAX_QUESTION_LENGTH * 0.85 && (
              <div style={{ textAlign: "right", fontSize: "0.68rem", color: input.length >= MAX_QUESTION_LENGTH ? "var(--danger)" : "var(--ink-muted)", marginTop: 4 }}>
                {input.length} / {MAX_QUESTION_LENGTH}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}