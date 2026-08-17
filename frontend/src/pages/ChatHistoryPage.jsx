import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  MessageSquare, Search, Trash2, ExternalLink,
  ChevronRight, FileText, Clock, X,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Spinner } from "../components/ui";

/*  Data fetching  */
function useChatSessions() {
  return useQuery({
    queryKey: ["chat-sessions"],
    queryFn: () => api.get("/chat/sessions?per_page=100").then(r => r.data),
    staleTime: 30_000,
  });
}

/*  Grouping helper  */
function groupByDocument(sessions) {
  const groups = {};
  for (const sess of sessions) {
    const key = sess.job_id || "unknown";
    if (!groups[key]) {
      groups[key] = {
        job_id:   key,
        filename: sess.original_filename || sess.title || "Document",
        sessions: [],
      };
    }
    groups[key].sessions.push(sess);
  }
  // Sort each group's sessions by updated_at desc
  for (const g of Object.values(groups)) {
    g.sessions.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
  }
  // Sort groups by most-recent session
  return Object.values(groups).sort((a, b) => {
    const aDate = new Date(a.sessions[0]?.updated_at || 0);
    const bDate = new Date(b.sessions[0]?.updated_at || 0);
    return bDate - aDate;
  });
}

/*  Session card  */
function SessionCard({ session, onDelete, onResume }) {
  const msgCount = Array.isArray(session.messages) ? session.messages.length : 0;
  const lastMsg  = Array.isArray(session.messages) && session.messages.length
    ? session.messages[session.messages.length - 1]
    : null;
  const preview  = lastMsg?.content?.slice(0, 90) || "No messages yet";

  return (
    <div
      style={{
        display: "flex", alignItems: "flex-start", gap: 12,
        padding: "0.875rem 1rem",
        borderRadius: 10,
        background: "var(--paper)",
        border: "1px solid var(--border)",
        cursor: "pointer",
        transition: "border-color 0.15s, box-shadow 0.15s",
      }}
      onClick={() => onResume(session.id)}
      onMouseEnter={e => {
        e.currentTarget.style.borderColor = "var(--accent)";
        e.currentTarget.style.boxShadow   = "0 2px 8px rgba(0,0,0,0.06)";
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = "var(--border)";
        e.currentTarget.style.boxShadow   = "none";
      }}
    >
      {/* Icon */}
      <div style={{
        width: 34, height: 34, borderRadius: 9, flexShrink: 0,
        background: "var(--paper-2, #f0f4ff)",
        display: "flex", alignItems: "center", justifyContent: "center",
        marginTop: 1,
      }}>
        <MessageSquare size={15} style={{ color: "var(--accent)" }} />
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "0.85rem", color: "var(--ink)", marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {session.title || "Chat session"}
        </div>
        <div style={{ fontSize: "0.76rem", color: "var(--ink-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginBottom: 5 }}>
          {preview}…
        </div>
        <div style={{ display: "flex", gap: 12, fontSize: "0.72rem", color: "var(--ink-muted)" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <MessageSquare size={10} /> {msgCount} message{msgCount !== 1 ? "s" : ""}
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
            <Clock size={10} />
            {session.updated_at
              ? formatDistanceToNow(new Date(session.updated_at), { addSuffix: true })
              : "—"}
          </span>
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        <button
          title="Resume session"
          onClick={(e) => { e.stopPropagation(); onResume(session.id); }}
          style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--accent)" }}
        >
          <ExternalLink size={13} />
        </button>
        <button
          title="Delete session"
          onClick={(e) => { e.stopPropagation(); onDelete(session.id); }}
          style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "var(--danger, #dc2626)" }}
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}

/*  Document group  */
function DocumentGroup({ group, onDelete, onResume }) {
  const [expanded, setExpanded] = useState(true);
  const total = group.sessions.length;

  return (
    <div style={{ marginBottom: "1.25rem" }}>
      {/* Group header */}
      <div
        onClick={() => setExpanded(e => !e)}
        style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "0.625rem 0.75rem",
          marginBottom: expanded ? "0.5rem" : 0,
          borderRadius: 8,
          cursor: "pointer",
          background: "var(--paper-2, #f8fafc)",
          border: "1px solid var(--border)",
          userSelect: "none",
        }}
      >
        <FileText size={14} style={{ color: "var(--accent)", flexShrink: 0 }} />
        <span style={{ flex: 1, fontWeight: 700, fontSize: "0.85rem", color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {group.filename}
        </span>
        <span style={{ fontSize: "0.72rem", color: "var(--ink-muted)", flexShrink: 0 }}>
          {total} session{total !== 1 ? "s" : ""}
        </span>
        <ChevronRight
          size={14}
          style={{ color: "var(--ink-muted)", flexShrink: 0, transform: expanded ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}
        />
      </div>

      {/* Sessions */}
      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", paddingLeft: "0.75rem" }}>
          {group.sessions.map(sess => (
            <SessionCard
              key={sess.id}
              session={sess}
              onDelete={onDelete}
              onResume={onResume}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/*  Main page  */
export default function ChatHistoryPage() {
  const navigate      = useNavigate();
  const queryClient   = useQueryClient();
  const [search, setSearch] = useState("");

  const { data, isLoading } = useChatSessions();
  const sessions = data?.sessions ?? data ?? [];

  // Search — filter by document filename or session title
  const filtered = useMemo(() => {
    if (!search.trim()) return sessions;
    const q = search.toLowerCase();
    return sessions.filter(s =>
      (s.original_filename || "").toLowerCase().includes(q) ||
      (s.title || "").toLowerCase().includes(q)
    );
  }, [sessions, search]);

  const grouped = useMemo(() => groupByDocument(filtered), [filtered]);

  const handleDelete = async (sessionId) => {
    if (!confirm("Delete this chat session? This cannot be undone.")) return;
    try {
      await api.delete(`/chat/sessions/${sessionId}`);
      // Optimistic remove from cache
      queryClient.setQueryData(["chat-sessions"], (old) => {
        if (!old) return old;
        const list = Array.isArray(old) ? old : old.sessions ?? [];
        const updated = list.filter(s => s.id !== sessionId);
        return Array.isArray(old) ? updated : { ...old, sessions: updated };
      });
      toast.success("Session deleted");
    } catch (err) {
      toast.error(errMsg(err, "Delete failed"));
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
    }
  };

  const handleResume = (sessionId) => {
    navigate(`/tools/pdf-chat?session=${sessionId}`);
  };

  const totalSessions = sessions.length;
  const totalGroups   = grouped.length;

  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="page-title">Chat History</h1>
          <p className="page-subtitle">
            {isLoading ? "Loading…" : `${totalSessions} session${totalSessions !== 1 ? "s" : ""} across ${totalGroups} document${totalGroups !== 1 ? "s" : ""}`}
          </p>
        </div>
        <button
          onClick={() => navigate("/tools/pdf-chat")}
          className="btn btn-primary btn-sm"
        >
          <MessageSquare size={13} /> New chat
        </button>
      </div>

      {/* Search */}
      {totalSessions > 0 && (
        <div style={{ position: "relative", marginBottom: "1.5rem" }}>
          <Search size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-muted)" }} />
          <input
            className="form-input"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search by document name or session title…"
            style={{ paddingLeft: 34, paddingRight: search ? 34 : undefined }}
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)", padding: 2 }}
            >
              <X size={14} />
            </button>
          )}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div style={{ display: "flex", justifyContent: "center", paddingTop: "3rem" }}>
          <Spinner size={28} />
        </div>
      )}

      {/* Empty state */}
      {!isLoading && totalSessions === 0 && (
        <div className="empty-state">
          <div className="empty-state-title">No chat sessions yet</div>
          <div className="empty-state-desc">
            Upload a PDF in the PDF Chat tool to start a conversation with your documents.
          </div>
          <button
            onClick={() => navigate("/tools/pdf-chat")}
            className="btn btn-primary"
            style={{ marginTop: "1rem" }}
          >
            Start a chat
          </button>
        </div>
      )}

      {/* No search results */}
      {!isLoading && totalSessions > 0 && grouped.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-title">No sessions match "{search}"</div>
          <div className="empty-state-desc">Try searching for a different document name.</div>
          <button onClick={() => setSearch("")} className="btn btn-outline" style={{ marginTop: "1rem" }}>
            Clear search
          </button>
        </div>
      )}

      {/* Grouped sessions */}
      {!isLoading && grouped.map(group => (
        <DocumentGroup
          key={group.job_id}
          group={group}
          onDelete={handleDelete}
          onResume={handleResume}
        />
      ))}
    </div>
  );
}