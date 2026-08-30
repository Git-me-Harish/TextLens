import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useForm } from "react-hook-form";
import toast from "react-hot-toast";
import { format } from "date-fns";
import {
  ShieldCheck, ShieldAlert, Key, Zap, FileText, Cpu,
  ArrowUpRight, CalendarDays,
} from "lucide-react";
import { useAuth } from "../lib/AuthContext";
import api from "../lib/api";
import { Input, Button, Badge } from "../components/ui";

export default function ProfilePage() {
  const { user, reload } = useAuth();
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState(null);
  const [apiKeyCount, setApiKeyCount] = useState(0);
  const [integrations, setIntegrations] = useState([]);
  const { register, handleSubmit } = useForm({ defaultValues: { full_name: user?.full_name || "" } });

  useEffect(() => {
    Promise.all([
      api.get("/users/me/stats"),
      api.get("/keys"),
      api.get("/credentials/"),
    ]).then(([s, k, c]) => {
      setStats(s.data);
      setApiKeyCount((k.data.keys || []).length);
      setIntegrations(c.data || []);
    }).catch(() => {});
  }, []);

  const onSubmit = async (data) => {
    setLoading(true);
    try {
      await api.patch("/users/me", data);
      await reload();
      toast.success("Profile updated");
    } catch { toast.error("Update failed"); }
    finally { setLoading(false); }
  };

  return (
    <div style={{ maxWidth: 720 }}>
      <div className="page-header">
        <h1 className="page-title">Profile</h1>
        <p className="page-subtitle">Manage your account settings</p>
      </div>

      <div className="card" style={{ padding: "1.5rem", marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "1.25rem", flexWrap: "wrap" }}>
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt="" style={{ width: 56, height: 56, borderRadius: "50%", objectFit: "cover" }} />
          ) : (
            <div style={{ width: 56, height: 56, borderRadius: "50%", background: "var(--accent-light)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--font-display)", fontSize: "1.25rem", color: "var(--accent)" }}>
              {user?.full_name?.[0] || "U"}
            </div>
          )}
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontWeight: 500 }}>{user?.full_name}</span>
              <Badge variant="accent">{user?.role}</Badge>
            </div>
            <div style={{ fontSize: "0.85rem", color: "var(--ink-muted)" }}>{user?.email}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 6 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.75rem", color: user?.is_verified ? "var(--success)" : "var(--warning)" }}>
                {user?.is_verified ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
                {user?.is_verified ? "Verified account" : "Not verified"}
              </span>
              {user?.created_at && (
                <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.75rem", color: "var(--ink-muted)" }}>
                  <CalendarDays size={13} />
                  Member since {format(new Date(user.created_at), "MMM yyyy")}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="stats-row" style={{ marginBottom: "1.5rem" }}>
        <div className="stat-card">
          <div className="stat-value">{stats?.total_jobs ?? 0}</div>
          <div className="stat-label">Total extractions</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{stats?.agent_runs ?? 0}</div>
          <div className="stat-label">Pipeline runs</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{apiKeyCount}</div>
          <div className="stat-label">API keys</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{integrations.length}</div>
          <div className="stat-label">Integrations</div>
        </div>
      </div>

      <div className="card" style={{ padding: "1.5rem", marginBottom: "1.5rem" }}>
        <h2 style={{ fontSize: "1rem", fontWeight: 500, marginBottom: "1.25rem" }}>Edit details</h2>
        <form onSubmit={handleSubmit(onSubmit)} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <Input label="Full name" {...register("full_name")} />
          <Input label="Email" defaultValue={user?.email} disabled />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <Button type="submit" loading={loading}>Save changes</Button>
          </div>
        </form>
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "1rem 1.5rem", borderBottom: "1px solid var(--border)", fontWeight: 500, fontSize: "0.95rem" }}>
          Account
        </div>
        <Link to="/api-keys" className="profile-link-row">
          <Key size={16} style={{ color: "var(--accent)" }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "0.85rem", fontWeight: 500 }}>API keys</div>
            <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>
              {apiKeyCount} key{apiKeyCount === 1 ? "" : "s"} — manage programmatic access
            </div>
          </div>
          <ArrowUpRight size={14} style={{ color: "var(--ink-muted)" }} />
        </Link>
        <Link to="/settings/integrations" className="profile-link-row">
          <Zap size={16} style={{ color: "var(--accent)" }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "0.85rem", fontWeight: 500 }}>Connected integrations</div>
            <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>
              {integrations.length} connected — required for agentic actions
            </div>
          </div>
          <ArrowUpRight size={14} style={{ color: "var(--ink-muted)" }} />
        </Link>
        <Link to="/history" className="profile-link-row">
          <FileText size={16} style={{ color: "var(--accent)" }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "0.85rem", fontWeight: 500 }}>Extraction history</div>
            <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>View all past documents</div>
          </div>
          <ArrowUpRight size={14} style={{ color: "var(--ink-muted)" }} />
        </Link>
        <Link to="/actions/history" className="profile-link-row" style={{ borderBottom: "none" }}>
          <Cpu size={16} style={{ color: "var(--accent)" }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "0.85rem", fontWeight: 500 }}>Action history</div>
            <div style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>Review agentic actions taken on your behalf</div>
          </div>
          <ArrowUpRight size={14} style={{ color: "var(--ink-muted)" }} />
        </Link>
      </div>
    </div>
  );
}
