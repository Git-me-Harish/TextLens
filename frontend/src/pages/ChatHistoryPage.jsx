import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { MessageSquare, Trash2, ExternalLink, FileText, Clock } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import toast from "react-hot-toast";
import api from "../lib/api";
import { Spinner } from "../components/ui";

export default function ChatHistoryPage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(null);

  const load = () => {
    setLoading(true);
    api.get("/chat/sessions")
      .then(r => setSessions(r.data))
      .catch(() => toast.error("Failed to load chat history"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const resume = (sessionId) => navigate(`/tools/pdf-chat?session=${sessionId}`);

  const deleteSession = async (id) => {
    setDeleting(id);
    try {
      await api.delete(`/chat/sessions/${id}`);
      setSessions(prev => prev.filter(s => s.id !== id));
      toast.success("Session deleted");
    } catch {
      toast.error("Failed to delete session");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div className="page-header">
        <h1 className="page-title">Chat History</h1>
        <p className="page-subtitle">Resume any previous PDF conversation</p>
      </div>

      {loading ? (
        <div style={{ display: "flex", justifyContent: "center", paddingTop: "3rem" }}>
          <Spinner size={24} />
        </div>
      ) : sessions.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "3rem", color: "var(--ink-muted)" }}>
          <MessageSquare size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
          <p style={{ margin: 0, fontSize: "0.9rem" }}>No chat sessions yet</p>
          <p style={{ margin: "0.5rem 0 1.25rem", fontSize: "0.8rem" }}>Upload a PDF and start asking questions</p>
          <button onClick={() => navigate("/tools/pdf-chat")} style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 8, padding: "0.5rem 1.25rem", cursor: "pointer", fontSize: "0.85rem" }}>
            Start a chat
          </button>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.625rem" }}>
          {sessions.map(s => (
            <div key={s.id} className="card" style={{ display: "flex", alignItems: "center", gap: "1rem", padding: "0.875rem 1rem", cursor: "pointer", transition: "box-shadow 0.15s" }}
              onClick={() => resume(s.id)}
              onMouseEnter={e => e.currentTarget.style.boxShadow = "0 2px 8px rgba(0,0,0,0.1)"}
              onMouseLeave={e => e.currentTarget.style.boxShadow = ""}
            >
              {/* Icon */}
              <div style={{ width: 38, height: 38, borderRadius: 10, background: "var(--paper-2,#f0f4ff)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                <FileText size={18} color="var(--accent)" />
              </div>

              {/* Info */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--ink)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {s.original_filename}
                </div>
                <div style={{ display: "flex", gap: 12, marginTop: 3 }}>
                  <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)", display: "flex", alignItems: "center", gap: 4 }}>
                    <MessageSquare size={11} />
                    {Math.floor(s.message_count / 2)} exchange{Math.floor(s.message_count / 2) !== 1 ? "s" : ""}
                  </span>
                  <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)", display: "flex", alignItems: "center", gap: 4 }}>
                    <Clock size={11} />
                    {formatDistanceToNow(new Date(s.updated_at), { addSuffix: true })}
                  </span>
                </div>
              </div>

              {/* Actions */}
              <div style={{ display: "flex", gap: 8, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
                <button onClick={() => resume(s.id)} title="Resume" style={{ background: "none", border: "1px solid var(--border)", borderRadius: 7, padding: "0.35rem 0.65rem", cursor: "pointer", color: "var(--accent)", fontSize: "0.78rem", display: "flex", alignItems: "center", gap: 5 }}>
                  <ExternalLink size={12} /> Resume
                </button>
                <button onClick={() => deleteSession(s.id)} title="Delete" disabled={deleting === s.id} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 7, padding: "0.35rem 0.5rem", cursor: "pointer", color: "var(--ink-muted)", display: "flex", alignItems: "center" }}>
                  {deleting === s.id ? <Spinner size={12} /> : <Trash2 size={13} />}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}