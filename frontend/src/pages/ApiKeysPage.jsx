import { useState, useEffect } from "react";
import {
  Key, Plus, Trash2, Copy, Check, Eye, EyeOff,
  Webhook, AlertTriangle, CheckCircle2, XCircle,
  RefreshCw, ToggleLeft, ToggleRight,
} from "lucide-react";
import toast from "react-hot-toast";
import { formatDistanceToNow, format } from "date-fns";
import api from "../lib/api";
import { Button, Spinner, Badge } from "../components/ui";

/* ─────────────────────────────── API Keys ────────────────────────── */

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button onClick={handleCopy} className="btn btn-ghost btn-sm" title="Copy">
      {copied ? <Check size={13} style={{ color: "var(--success)" }} /> : <Copy size={13} />}
    </button>
  );
}

function NewKeyModal({ catalog, onClose, onCreate }) {
  const [name, setName] = useState("");
  const [limit, setLimit] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!name.trim()) return toast.error("Name is required");
    setLoading(true);
    try {
      const { data } = await api.post("/keys", {
        name: name.trim(),
        monthly_limit: limit ? parseInt(limit) : null,
      });
      onCreate(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to create key");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{ width: "100%", maxWidth: 440, padding: "1.75rem" }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ fontWeight: 600, marginBottom: "1.25rem" }}>Create API Key</h3>

        <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>
          Key name *
        </label>
        <input
          className="form-input"
          placeholder="e.g. production-server"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ marginBottom: "1rem" }}
          autoFocus
        />

        <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>
          Monthly request limit <span style={{ fontWeight: 400 }}>(optional — blank = unlimited)</span>
        </label>
        <input
          className="form-input"
          type="number"
          placeholder="e.g. 1000"
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
          style={{ marginBottom: "1.5rem" }}
          min={1}
        />

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} loading={loading}>Create key</Button>
        </div>
      </div>
    </div>
  );
}

function RevealKey({ plainKey }) {
  const [visible, setVisible] = useState(true);

  return (
    <div
      style={{
        background: "#f0fdf4",
        border: "1px solid #86efac",
        borderRadius: 8,
        padding: "0.875rem 1rem",
        marginBottom: "1.25rem",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 6,
          color: "#16a34a",
          fontSize: "0.8rem",
          fontWeight: 600,
        }}
      >
        <CheckCircle2 size={14} /> Key created — copy it now, it won't be shown again
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <code
          style={{
            flex: 1,
            fontFamily: "var(--font-mono)",
            fontSize: "0.82rem",
            wordBreak: "break-all",
            color: "var(--ink)",
          }}
        >
          {visible ? plainKey : "•".repeat(Math.min(plainKey.length, 48))}
        </code>
        <button
          onClick={() => setVisible((v) => !v)}
          className="btn btn-ghost btn-sm"
        >
          {visible ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
        <CopyButton text={plainKey} />
      </div>
    </div>
  );
}

function APIKeysSection() {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [newKey, setNewKey] = useState(null);

  useEffect(() => {
    api.get("/keys").then(({ data }) => setKeys(data.keys)).finally(() => setLoading(false));
  }, []);

  const handleCreate = (data) => {
    setNewKey(data.plain_key);
    setKeys((prev) => [{ ...data, plain_key: undefined }, ...prev]);
    setShowModal(false);
    toast.success("API key created");
  };

  const handleRevoke = async (id) => {
    if (!confirm("Revoke this API key? Any apps using it will stop working.")) return;
    try {
      await api.delete(`/keys/${id}`);
      setKeys((prev) => prev.map((k) => k.id === id ? { ...k, is_active: false } : k));
      toast.success("Key revoked");
    } catch {
      toast.error("Failed to revoke key");
    }
  };

  if (loading) return <div style={{ padding: "2rem", textAlign: "center" }}><Spinner /></div>;

  return (
    <div>
      {newKey && <RevealKey plainKey={newKey} />}

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "1rem",
        }}
      >
        <div>
          <h2 style={{ fontSize: "0.95rem", fontWeight: 600 }}>API Keys</h2>
          <p style={{ fontSize: "0.8rem", color: "var(--ink-muted)", marginTop: 2 }}>
            Use the <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>X-API-Key</code> header to authenticate programmatic access.
          </p>
        </div>
        <Button onClick={() => setShowModal(true)} style={{ gap: 6 }}>
          <Plus size={14} /> New key
        </Button>
      </div>

      {keys.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">No API keys</div>
          <div className="empty-state-desc">Create a key to access TextLens via REST API or SDK.</div>
        </div>
      ) : (
        <div className="card" style={{ overflow: "hidden" }}>
          <table className="jobs-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Key</th>
                <th>Status</th>
                <th>Requests</th>
                <th>Last used</th>
                <th>Expires</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} style={{ opacity: k.is_active ? 1 : 0.5 }}>
                  <td style={{ fontWeight: 500 }}>{k.name}</td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>
                        {k.key_prefix}••••••••
                      </code>
                      <CopyButton text={k.key_prefix} />
                    </div>
                  </td>
                  <td>
                    <Badge variant={k.is_active ? "success" : "default"}>
                      {k.is_active ? "active" : "revoked"}
                    </Badge>
                  </td>
                  <td style={{ fontSize: "0.82rem" }}>
                    {k.total_requests.toLocaleString()}
                    {k.monthly_limit && (
                      <span style={{ color: "var(--ink-muted)" }}> / {k.monthly_limit.toLocaleString()}</span>
                    )}
                  </td>
                  <td style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>
                    {k.last_used_at
                      ? formatDistanceToNow(new Date(k.last_used_at), { addSuffix: true })
                      : "Never"}
                  </td>
                  <td style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>
                    {k.expires_at ? format(new Date(k.expires_at), "MMM d, yyyy") : "Never"}
                  </td>
                  <td>
                    {k.is_active && (
                      <button
                        onClick={() => handleRevoke(k.id)}
                        className="btn btn-ghost btn-sm"
                        style={{ color: "var(--danger)" }}
                        title="Revoke"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* SDK snippet */}
      <div className="card" style={{ padding: "1.25rem 1.5rem", marginTop: "1.25rem" }}>
        <div style={{ fontSize: "0.78rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-muted)", marginBottom: "0.75rem" }}>
          Quick start
        </div>
        <pre
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "0.78rem",
            background: "var(--paper-2)",
            borderRadius: 8,
            padding: "0.875rem 1rem",
            overflowX: "auto",
            color: "var(--ink-secondary)",
            margin: 0,
          }}
        >
{`# Python
import requests

response = requests.post(
    "https://your-textlens-domain.com/api/agents/run",
    headers={"X-API-Key": "tl_live_your_key_here"},
    json={
        "job_id": "<ocr_job_id>",
        "domain": "finance",
        "pipeline_type": "invoice_processor"
    }
)
print(response.json())`}
        </pre>
      </div>

      {showModal && (
        <NewKeyModal onClose={() => setShowModal(false)} onCreate={handleCreate} />
      )}
    </div>
  );
}

/* ─────────────────────────────── Webhooks ────────────────────────── */

const ALL_EVENTS = [
  { value: "job.completed",   label: "Job completed" },
  { value: "job.failed",      label: "Job failed" },
  { value: "agent.completed", label: "Agent run completed" },
  { value: "batch.completed", label: "Batch job completed" },
];

function NewWebhookModal({ onClose, onCreate }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [events, setEvents] = useState(["job.completed", "agent.completed"]);
  const [loading, setLoading] = useState(false);

  const toggleEvent = (v) =>
    setEvents((prev) =>
      prev.includes(v) ? prev.filter((e) => e !== v) : [...prev, v]
    );

  const submit = async () => {
    if (!name.trim() || !url.trim()) return toast.error("Name and URL are required");
    if (!events.length) return toast.error("Select at least one event");
    setLoading(true);
    try {
      const { data } = await api.post("/webhooks", {
        name: name.trim(),
        target_url: url.trim(),
        events,
        secret: secret.trim() || null,
      });
      onCreate(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to create webhook");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{ width: "100%", maxWidth: 480, padding: "1.75rem" }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ fontWeight: 600, marginBottom: "1.25rem" }}>Register Webhook</h3>

        <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>Name *</label>
        <input className="form-input" placeholder="e.g. my-server-notify" value={name} onChange={(e) => setName(e.target.value)} style={{ marginBottom: "1rem" }} autoFocus />

        <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>Target URL *</label>
        <input className="form-input" placeholder="https://your-server.com/webhook" value={url} onChange={(e) => setUrl(e.target.value)} style={{ marginBottom: "1rem" }} />

        <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 6, color: "var(--ink-muted)" }}>
          Signing secret <span style={{ fontWeight: 400 }}>(optional — used for HMAC-SHA256 verification)</span>
        </label>
        <input className="form-input" placeholder="my-secret-value" value={secret} onChange={(e) => setSecret(e.target.value)} style={{ marginBottom: "1rem" }} />

        <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, marginBottom: 8, color: "var(--ink-muted)" }}>Events *</label>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: "1.5rem" }}>
          {ALL_EVENTS.map((ev) => (
            <label key={ev.value} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: "0.85rem" }}>
              <input
                type="checkbox"
                checked={events.includes(ev.value)}
                onChange={() => toggleEvent(ev.value)}
              />
              {ev.label}
              <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "var(--ink-muted)" }}>{ev.value}</code>
            </label>
          ))}
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} loading={loading}>Register</Button>
        </div>
      </div>
    </div>
  );
}

function WebhooksSection() {
  const [webhooks, setWebhooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    api.get("/webhooks").then(({ data }) => setWebhooks(data)).finally(() => setLoading(false));
  }, []);

  const handleCreate = (data) => {
    setWebhooks((prev) => [data, ...prev]);
    setShowModal(false);
    toast.success("Webhook registered");
  };

  const handleDelete = async (id) => {
    if (!confirm("Delete this webhook?")) return;
    try {
      await api.delete(`/webhooks/${id}`);
      setWebhooks((prev) => prev.filter((w) => w.id !== id));
      toast.success("Webhook deleted");
    } catch {
      toast.error("Delete failed");
    }
  };

  const handleToggle = async (id) => {
    try {
      const { data } = await api.patch(`/webhooks/${id}/toggle`);
      setWebhooks((prev) => prev.map((w) => (w.id === id ? data : w)));
    } catch {
      toast.error("Toggle failed");
    }
  };

  if (loading) return <div style={{ padding: "2rem", textAlign: "center" }}><Spinner /></div>;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
        <div>
          <h2 style={{ fontSize: "0.95rem", fontWeight: 600 }}>Webhooks</h2>
          <p style={{ fontSize: "0.8rem", color: "var(--ink-muted)", marginTop: 2 }}>
            Get notified via HTTP POST when jobs complete. Payloads signed with HMAC-SHA256.
          </p>
        </div>
        <Button onClick={() => setShowModal(true)} style={{ gap: 6 }}>
          <Plus size={14} /> Register
        </Button>
      </div>

      {webhooks.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">No webhooks</div>
          <div className="empty-state-desc">Register an endpoint to receive real-time notifications.</div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          {webhooks.map((wh) => (
            <div
              key={wh.id}
              className="card"
              style={{
                padding: "1rem 1.25rem",
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                opacity: wh.is_active ? 1 : 0.6,
              }}
            >
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 8,
                  background: wh.is_active ? "var(--accent-light)" : "var(--paper-2)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <Webhook size={14} style={{ color: wh.is_active ? "var(--accent)" : "var(--ink-muted)" }} />
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: 2 }}>{wh.name}</div>
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "0.75rem",
                    color: "var(--ink-muted)",
                    marginBottom: 6,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {wh.target_url}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {(wh.events || []).map((ev) => (
                    <span
                      key={ev}
                      style={{
                        background: "var(--paper-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 4,
                        padding: "1px 6px",
                        fontSize: "0.7rem",
                        fontFamily: "var(--font-mono)",
                        color: "var(--ink-secondary)",
                      }}
                    >
                      {ev}
                    </span>
                  ))}
                </div>
                {wh.last_triggered_at && (
                  <div style={{ fontSize: "0.72rem", color: "var(--ink-muted)", marginTop: 4 }}>
                    Last triggered {formatDistanceToNow(new Date(wh.last_triggered_at), { addSuffix: true })} · {wh.total_deliveries} deliveries
                  </div>
                )}
              </div>

              <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                <button
                  onClick={() => handleToggle(wh.id)}
                  className="btn btn-ghost btn-sm"
                  title={wh.is_active ? "Disable" : "Enable"}
                  style={{ color: wh.is_active ? "var(--success)" : "var(--ink-muted)" }}
                >
                  {wh.is_active ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
                </button>
                <button
                  onClick={() => handleDelete(wh.id)}
                  className="btn btn-ghost btn-sm"
                  style={{ color: "var(--danger)" }}
                  title="Delete"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showModal && <NewWebhookModal onClose={() => setShowModal(false)} onCreate={handleCreate} />}
    </div>
  );
}

/* ─────────────────────────────── page ────────────────────────────── */

export default function ApiKeysPage() {
  const [tab, setTab] = useState("keys");

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">API Access</h1>
        <p className="page-subtitle">Manage API keys and webhook endpoints for programmatic access</p>
      </div>

      <div className="tabs" style={{ marginBottom: "1.5rem" }}>
        <button className={`tab ${tab === "keys" ? "active" : ""}`} onClick={() => setTab("keys")}>
          <Key size={13} /> API Keys
        </button>
        <button className={`tab ${tab === "webhooks" ? "active" : ""}`} onClick={() => setTab("webhooks")}>
          <Webhook size={13} /> Webhooks
        </button>
      </div>

      {tab === "keys" && <APIKeysSection />}
      {tab === "webhooks" && <WebhooksSection />}
    </div>
  );
}