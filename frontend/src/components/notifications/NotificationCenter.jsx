import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { formatDistanceToNow } from "date-fns";
import { Bell, CheckCheck, AlertCircle, Clock3 } from "lucide-react";
import api, { errMsg } from "../../lib/api";
import { subscribeToSSE } from "../../hooks/useSSE";

const STATUS_META = {
  completed: { color: "var(--success)", Icon: CheckCheck },
  failed: { color: "var(--danger)", Icon: AlertCircle },
  awaiting_approval: { color: "var(--warning)", Icon: Clock3 },
};

export default function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get("/notifications?limit=20").then((r) => r.data),
    refetchInterval: 60_000, // SSE is primary; this is a safety net
  });

  const notifications = data?.notifications ?? [];
  const unreadCount = data?.unread_count ?? 0;

  // Live toast on any new notification, wherever the user currently is
  useEffect(() => {
    return subscribeToSSE("notification", (notif) => {
      const meta = STATUS_META[notif.status] || STATUS_META.completed;
      const run = notif.status === "failed" ? toast.error : toast.success;
      run(notif.title, { icon: undefined });
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    });
  }, [queryClient]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const markRead = async (id) => {
    try {
      await api.post(`/notifications/${id}/read`);
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    } catch (err) {
      toast.error(errMsg(err, "Could not update notification"));
    }
  };

  const markAllRead = async () => {
    try {
      await api.post("/notifications/read-all");
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    } catch (err) {
      toast.error(errMsg(err, "Could not update notifications"));
    }
  };

  const handleClick = (notif) => {
    setOpen(false);
    if (!notif.is_read) markRead(notif.id);
    if (notif.link) navigate(notif.link);
  };

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Notifications"
        style={{
          position: "relative",
          width: 34, height: 34,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: open ? "var(--paper-3)" : "none",
          border: "1px solid var(--border)",
          borderRadius: "50%",
          cursor: "pointer",
          color: "var(--ink-secondary)",
        }}
      >
        <Bell size={16} />
        {unreadCount > 0 && (
          <span style={{
            position: "absolute", top: -2, right: -2,
            minWidth: 16, height: 16, padding: "0 3px",
            borderRadius: 100, background: "var(--danger)", color: "#fff",
            fontSize: "0.62rem", fontWeight: 700,
            display: "flex", alignItems: "center", justifyContent: "center",
            border: "2px solid var(--paper)",
          }}>
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div style={{
          position: "absolute", right: 0, top: "calc(100% + 8px)",
          width: 360, maxHeight: 440,
          background: "#fff", border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-lg)",
          zIndex: 300, overflow: "hidden",
          display: "flex", flexDirection: "column",
        }}>
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "0.85rem 1.1rem", borderBottom: "1px solid var(--border)", flexShrink: 0,
          }}>
            <span style={{ fontWeight: 600, fontSize: "0.88rem" }}>Notifications</span>
            {unreadCount > 0 && (
              <button
                onClick={markAllRead}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--accent)", fontSize: "0.75rem", fontWeight: 500 }}
              >
                Mark all read
              </button>
            )}
          </div>

          <div style={{ overflowY: "auto", flex: 1 }}>
            {notifications.length === 0 ? (
              <div style={{ padding: "2.5rem 1rem", textAlign: "center", color: "var(--ink-muted)", fontSize: "0.83rem" }}>
                You're all caught up
              </div>
            ) : (
              notifications.map((notif) => {
                const meta = STATUS_META[notif.status] || STATUS_META.completed;
                const { Icon } = meta;
                return (
                  <button
                    key={notif.id}
                    onClick={() => handleClick(notif)}
                    style={{
                      display: "flex", gap: 10, width: "100%", textAlign: "left",
                      padding: "0.75rem 1.1rem",
                      background: notif.is_read ? "none" : "var(--accent-light)",
                      border: "none", borderBottom: "1px solid var(--border)",
                      cursor: "pointer",
                    }}
                  >
                    <Icon size={15} style={{ color: meta.color, flexShrink: 0, marginTop: 2 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "0.82rem", fontWeight: notif.is_read ? 500 : 700, color: "var(--ink)" }}>
                        {notif.title}
                      </div>
                      {notif.message && (
                        <div style={{
                          fontSize: "0.76rem", color: "var(--ink-muted)", marginTop: 2,
                          overflow: "hidden", textOverflow: "ellipsis",
                          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                        }}>
                          {notif.message}
                        </div>
                      )}
                      <div style={{ fontSize: "0.68rem", color: "var(--ink-muted)", marginTop: 4 }}>
                        {formatDistanceToNow(new Date(notif.created_at), { addSuffix: true })}
                      </div>
                    </div>
                    {!notif.is_read && (
                      <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--accent)", flexShrink: 0, marginTop: 5 }} />
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
