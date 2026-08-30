/**
 * CredentialsSettingsPage
 *
 * Settings → Integrations page for connecting/disconnecting MCP services.
 * Route: /settings/integrations
 *
 * Layout: a service list on the left (styled like the app's own sidebar
 * nav), selected service's connection detail on the right.
 */

import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { Check, ExternalLink, Link2, Lock } from "lucide-react";
import api from "../lib/api";
import { Badge, Button, Card, Spinner } from "../components/ui";
import { SERVICE_ICONS } from "../lib/actionIcons";

const OAUTH_ERROR_MESSAGES = {
  access_denied: "Google connection cancelled — you didn't grant access.",
  missing_code_or_state: "Google didn't return the expected response. Please try again.",
  invalid_state: "That connection link expired or was invalid. Please try connecting again.",
  token_exchange_failed: "Google rejected the connection request. Please try again.",
  no_access_token_returned: "Google didn't return an access token. Please try again.",
};

export default function CredentialsSettingsPage() {
  const [services, setServices] = useState({});         // catalog from /services
  const [connected, setConnected] = useState(new Set()); // set of service_names
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [formValues, setFormValues] = useState({});
  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(null);
  const [connectingOAuth, setConnectingOAuth] = useState(null); // service_name mid-redirect
  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    Promise.all([
      api.get("/credentials/services"),
      api.get("/credentials/"),
    ])
      .then(([svcRes, connRes]) => {
        const svcs = svcRes.data.services || {};
        setServices(svcs);
        setConnected(new Set(connRes.data.map((c) => c.service_name)));
        setSelected((prev) => prev || Object.keys(svcs)[0] || null);
      })
      .catch(() => toast.error("Failed to load integrations."))
      .finally(() => setLoading(false));
  }, []);

  // Handle the redirect back from the Google OAuth callback
  useEffect(() => {
    const connectedService = searchParams.get("connected");
    const oauthError = searchParams.get("error");

    if (connectedService) {
      setConnected((prev) => new Set([...prev, connectedService]));
      setSelected(connectedService);
      toast.success(`${connectedService.replace(/_/g, " ")} connected successfully.`);
    } else if (oauthError) {
      toast.error(OAUTH_ERROR_MESSAGES[oauthError] || `Connection failed (${oauthError}).`);
    }

    if (connectedService || oauthError) {
      setSearchParams({}, { replace: true }); // scrub the query string
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleConnectOAuth = async (serviceName) => {
    setConnectingOAuth(serviceName);
    try {
      const res = await api.get(`/credentials/${serviceName}/connect-url`);
      window.location.href = res.data.authorization_url;
      // navigation away — no need to reset connectingOAuth
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to start Google connection.");
      setConnectingOAuth(null);
    }
  };

  const handleConnect = async (serviceName) => {
    setSaving(true);
    try {
      await api.post("/credentials/", {
        service_name: serviceName,
        credentials: formValues,
      });
      setConnected((prev) => new Set([...prev, serviceName]));
      setFormValues({});
      toast.success(`${serviceName.replace(/_/g, " ")} connected successfully.`);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to save credentials.");
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async (serviceName) => {
    if (!window.confirm(`Disconnect ${serviceName.replace(/_/g, " ")}? Any scheduled actions requiring this service will fail.`)) return;
    setDisconnecting(serviceName);
    try {
      await api.delete(`/credentials/${serviceName}`);
      setConnected((prev) => { const s = new Set(prev); s.delete(serviceName); return s; });
      toast.success(`${serviceName.replace(/_/g, " ")} disconnected.`);
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to disconnect service.");
    } finally {
      setDisconnecting(null);
    }
  };

  const selectedInfo = selected ? services[selected] : null;

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Integrations</h1>
        <p className="page-subtitle">
          Connect external services to unlock agentic actions on your documents.
          Credentials are encrypted at rest and never shared.
        </p>
      </div>

      {loading ? (
        <div style={{ padding: "3rem", textAlign: "center" }}><Spinner /></div>
      ) : (
        <div style={{ display: "flex", gap: "1.5rem", alignItems: "flex-start" }}>
          {/* Service list */}
          <Card style={{ width: 260, flexShrink: 0, padding: "0.5rem", overflow: "hidden" }}>
            {Object.entries(services).map(([serviceName, svcInfo]) => {
              const isConnected = connected.has(serviceName) || svcInfo.connection_type === "system";
              const ServiceIcon = SERVICE_ICONS[serviceName] || Link2;
              return (
                <button
                  key={serviceName}
                  onClick={() => { setSelected(serviceName); setFormValues({}); }}
                  className={`sidebar-link ${selected === serviceName ? "active" : ""}`}
                  style={{ marginBottom: 2 }}
                >
                  <ServiceIcon className="link-icon" />
                  <span style={{ flex: 1, textAlign: "left", textTransform: "capitalize", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {serviceName.replace(/_/g, " ")}
                  </span>
                  {isConnected && <Check size={14} style={{ color: "var(--success)", flexShrink: 0 }} />}
                </button>
              );
            })}
          </Card>

          {/* Detail panel */}
          <Card style={{ flex: 1, padding: "1.5rem", minWidth: 0 }}>
            {selectedInfo && (
              <ServiceDetail
                serviceName={selected}
                info={selectedInfo}
                isConnected={connected.has(selected) || selectedInfo.connection_type === "system"}
                disconnecting={disconnecting === selected}
                connectingOAuth={connectingOAuth === selected}
                saving={saving}
                formValues={formValues}
                onFormChange={(field, val) => setFormValues((prev) => ({ ...prev, [field]: val }))}
                onConnect={() => handleConnect(selected)}
                onConnectOAuth={() => handleConnectOAuth(selected)}
                onDisconnect={() => handleDisconnect(selected)}
              />
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

// ── Detail panel for the selected service ───────────────────────────────────

function ServiceDetail({
  serviceName,
  info,
  isConnected,
  disconnecting,
  connectingOAuth,
  saving,
  formValues,
  onFormChange,
  onConnect,
  onConnectOAuth,
  onDisconnect,
}) {
  const ServiceIcon = SERVICE_ICONS[serviceName] || Link2;
  const isOAuth = info.connection_type === "oauth";
  const isSystem = info.connection_type === "system";
  const isManual = info.connection_type === "manual";

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 4 }}>
        <ServiceIcon size={26} style={{ color: "var(--accent)", flexShrink: 0, marginTop: 2 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={{ fontSize: "1.15rem", fontWeight: 400, color: "var(--ink)", textTransform: "capitalize" }}>
            {serviceName.replace(/_/g, " ")}
          </h2>
          <p style={{ fontSize: "0.855rem", color: "var(--ink-muted)", marginTop: 2 }}>{info.description}</p>
        </div>
        {isConnected && (
          <Badge variant="success">
            <Check size={11} style={{ marginRight: 2 }} /> {isSystem ? "Always available" : "Connected"}
          </Badge>
        )}
      </div>

      {/* MCP endpoint URL */}
      {info.mcp_url && (
        <div style={{ marginTop: "1.5rem" }}>
          <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--ink-muted)", marginBottom: 6 }}>
            MCP endpoint
          </div>
          <div style={{ background: "var(--paper)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "0.6rem 0.875rem" }}>
            <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem", color: "var(--ink-secondary)", wordBreak: "break-all" }}>
              {info.mcp_url}
            </code>
          </div>
          <p style={{ fontSize: "0.76rem", color: "var(--ink-muted)", marginTop: 6 }}>
            Actions requiring this service call this URL. Set in the backend's{" "}
            <code className="font-mono">.env</code> — not editable from here.
          </p>
        </div>
      )}

      {/* Connection UI, by type */}
      <div style={{ marginTop: "1.5rem", paddingTop: "1.25rem", borderTop: "1px solid var(--border)" }}>
        {isSystem && (
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: "0.855rem", color: "var(--ink-muted)" }}>
            <Lock size={15} style={{ flexShrink: 0, marginTop: 2 }} />
            <p>
              This service sends through the platform's own account — there's
              nothing for you to connect. It's available to every action that needs it.
            </p>
          </div>
        )}

        {isOAuth && (
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Button onClick={onConnectOAuth} loading={connectingOAuth} disabled={connectingOAuth}>
              <ExternalLink size={14} />
              {connectingOAuth ? "Redirecting…" : isConnected ? "Re-authorize with Google" : "Connect with Google"}
            </Button>
            {isConnected && (
              <Button variant="ghost" size="sm" onClick={onDisconnect} disabled={disconnecting} style={{ color: "var(--danger)" }}>
                {disconnecting ? "Disconnecting…" : "Disconnect"}
              </Button>
            )}
          </div>
        )}

        {isManual && (
          <div>
            <p style={{ fontSize: "0.82rem", color: "var(--ink-muted)", marginBottom: 12 }}>
              {isConnected
                ? "Update your credentials for this service."
                : "Enter your credentials to connect this service."}{" "}
              These are encrypted and stored securely.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 360 }}>
              {Object.entries(info.fields || {}).map(([field, fieldLabel]) => (
                <div className="form-group" key={field}>
                  <label className="form-label" style={{ textTransform: "capitalize" }}>
                    {field.replace(/_/g, " ")}
                  </label>
                  <input
                    type={field.toLowerCase().includes("token") || field.toLowerCase().includes("key") || field.toLowerCase().includes("secret") ? "password" : "text"}
                    className="form-input"
                    placeholder={fieldLabel}
                    value={formValues[field] || ""}
                    onChange={(e) => onFormChange(field, e.target.value)}
                  />
                </div>
              ))}
            </div>
            <div style={{ marginTop: "1rem", display: "flex", gap: 8 }}>
              <Button onClick={onConnect} loading={saving} disabled={saving || Object.keys(formValues).length === 0}>
                {saving ? "Saving…" : isConnected ? "Save & Reconnect" : "Save & Connect"}
              </Button>
              {isConnected && (
                <Button variant="ghost" onClick={onDisconnect} disabled={disconnecting} style={{ color: "var(--danger)" }}>
                  {disconnecting ? "Disconnecting…" : "Disconnect"}
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
