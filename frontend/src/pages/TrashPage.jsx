/**
 * TrashPage — recover deleted work, or remove it for good.
 *
 * Deleting from Extraction History or Pipeline History used to be immediate
 * and unrecoverable (and for extractions it also removed the underlying files
 * from storage). Deletes now land here for 30 days first.
 *
 * Scope note surfaced in the UI as well as the code: API keys, webhooks and
 * connected integrations are NOT here. Those are secrets and configuration
 * where "deleted" has to mean gone immediately, so they keep hard delete and
 * their own explicit danger confirmation.
 */

import { useCallback, useEffect, useState } from "react";
import { Trash2, RotateCcw, AlertTriangle, Inbox } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Spinner, Badge } from "../components/ui";
import { useConfirm } from "../lib/useConfirm";

const TYPE_LABELS = {
  job: "Extraction",
  agent_run: "Pipeline run",
  action_run: "Action run",
  chat_session: "Chat session",
  batch: "Batch",
};

export default function TrashPage() {
  const { confirm, confirmDialog } = useConfirm();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [retentionDays, setRetentionDays] = useState(30);
  const [busyId, setBusyId] = useState(null);
  const [filter, setFilter] = useState("all");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/trash");
      setItems(data.items || []);
      setRetentionDays(data.retention_days ?? 30);
    } catch (err) {
      toast.error(errMsg(err, "Could not load Trash"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const restore = async (item) => {
    setBusyId(item.id);
    try {
      await api.post(`/trash/${item.type}/${item.id}/restore`);
      toast.success(`Restored "${item.title}"`);
      setItems((prev) => prev.filter((i) => i.id !== item.id));
    } catch (err) {
      toast.error(errMsg(err, "Restore failed"));
    } finally {
      setBusyId(null);
    }
  };

  const purge = async (item) => {
    if (!await confirm({
      title: "Delete permanently?",
      message: `"${item.title}" will be deleted for good, along with any files it owns. This cannot be undone.`,
      confirmLabel: "Delete forever",
      tone: "danger",
    })) return;

    setBusyId(item.id);
    try {
      await api.delete(`/trash/${item.type}/${item.id}`);
      toast.success("Deleted permanently");
      setItems((prev) => prev.filter((i) => i.id !== item.id));
    } catch (err) {
      toast.error(errMsg(err, "Delete failed"));
    } finally {
      setBusyId(null);
    }
  };

  const emptyAll = async () => {
    if (!await confirm({
      title: `Empty Trash?`,
      message: `All ${items.length} item${items.length === 1 ? "" : "s"} will be deleted for good, along with their files. This cannot be undone.`,
      confirmLabel: "Empty Trash",
      tone: "danger",
    })) return;

    try {
      const { data } = await api.delete("/trash");
      toast.success(`Deleted ${data.count} item${data.count === 1 ? "" : "s"}`);
      setItems([]);
    } catch (err) {
      toast.error(errMsg(err, "Could not empty Trash"));
    }
  };

  const types = ["all", ...Array.from(new Set(items.map((i) => i.type)))];
  const visible = filter === "all" ? items : items.filter((i) => i.type === filter);

  return (
    <div>
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="page-title">Trash</h1>
          <p className="page-subtitle">
            Deleted extractions, pipeline runs, actions, chats and batches stay here
            for {retentionDays} days, then are removed automatically.
          </p>
        </div>
        {items.length > 0 && (
          <button onClick={emptyAll} className="btn btn-ghost btn-sm" style={{ color: "var(--danger, #dc2626)" }}>
            <Trash2 size={14} /> Empty Trash
          </button>
        )}
      </div>

      {/* API keys / webhooks / integrations are intentionally absent — say so,
          rather than leaving the user wondering where a revoked key went. */}
      <div className="card" style={{ padding: "0.75rem 1rem", marginBottom: "1rem", display: "flex", gap: 10, alignItems: "flex-start" }}>
        <AlertTriangle size={15} style={{ color: "var(--warning, #d97706)", flexShrink: 0, marginTop: 2 }} />
        <div style={{ fontSize: "0.8rem", color: "var(--ink-muted)", lineHeight: 1.6 }}>
          API keys, webhooks and connected integrations are never kept here — revoking
          those takes effect immediately and cannot be undone.
        </div>
      </div>

      {types.length > 2 && (
        <div style={{ display: "flex", gap: 8, marginBottom: "1rem", flexWrap: "wrap" }}>
          {types.map((t) => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={`btn btn-sm ${filter === t ? "btn-primary" : "btn-outline"}`}
            >
              {t === "all" ? `All (${items.length})` : `${TYPE_LABELS[t] || t} (${items.filter((i) => i.type === t).length})`}
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div style={{ display: "flex", justifyContent: "center", padding: "3rem" }}><Spinner size={28} /></div>
      ) : visible.length === 0 ? (
        <div className="card" style={{ padding: "3rem", textAlign: "center" }}>
          <Inbox size={34} style={{ color: "var(--ink-muted)", marginBottom: 12 }} />
          <div style={{ fontWeight: 600, color: "var(--ink)" }}>Trash is empty</div>
          <div style={{ fontSize: "0.83rem", color: "var(--ink-muted)", marginTop: 6 }}>
            Anything you delete will appear here for {retentionDays} days.
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          {visible.map((item, idx) => (
            <div
              key={`${item.type}:${item.id}`}
              style={{
                display: "flex", alignItems: "center", gap: 12,
                padding: "0.875rem 1.25rem",
                borderTop: idx === 0 ? "none" : "1px solid var(--border)",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                  <span style={{
                    fontWeight: 600, fontSize: "0.875rem", color: "var(--ink)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>
                    {item.title}
                  </span>
                  <Badge>{item.type_label || TYPE_LABELS[item.type] || item.type}</Badge>
                </div>
                <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>
                  Deleted {formatDistanceToNow(new Date(item.deleted_at), { addSuffix: true })}
                  {" · "}
                  <span style={{ color: item.days_remaining <= 3 ? "var(--danger, #dc2626)" : "inherit" }}>
                    {item.days_remaining === 0
                      ? "removed within a day"
                      : `${item.days_remaining} day${item.days_remaining === 1 ? "" : "s"} left`}
                  </span>
                </div>
              </div>

              <button
                onClick={() => restore(item)}
                disabled={busyId === item.id}
                className="btn btn-outline btn-sm"
              >
                <RotateCcw size={13} /> Restore
              </button>
              <button
                onClick={() => purge(item)}
                disabled={busyId === item.id}
                className="btn btn-ghost btn-sm"
                style={{ color: "var(--danger, #dc2626)" }}
                title="Delete permanently"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      {confirmDialog}
    </div>
  );
}
