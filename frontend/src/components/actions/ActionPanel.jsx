/**
 * ActionPanel
 *
 * Shown on the AgentRun result page after document intelligence completes.
 * Fetches available actions for the detected domain, renders them as cards,
 * and initiates an action run when the user clicks one.
 *
 * Props:
 *   agentRunId  — completed AgentRun ID
 *   domain      — detected domain string (healthcare | hr | finance | legal | government | education)
 *   onActionStarted(actionRunId, actionLabel) — called when a run is successfully created
 */

import { useState, useEffect, useCallback } from "react";
import { Link2 } from "lucide-react";
import api from "../../lib/api";   // existing axios instance with auth headers
import { Badge, Spinner } from "../ui";
import { CatalogIcon } from "../../lib/actionIcons";

const DOMAIN_LABELS = {
  healthcare: "Healthcare",
  hr: "Career & Recruitment",
  finance: "Finance & Business",
  legal: "Legal & Compliance",
  government: "Government & Compliance",
  education: "Education & Knowledge",
};

export default function ActionPanel({ agentRunId, domain, onActionStarted }) {
  const [actions, setActions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(null);   // action_type being started
  const [userContext, setUserContext] = useState("");
  const [showContextFor, setShowContextFor] = useState(null);

  // ── Fetch available actions ────────────────────────────────────────────────
  useEffect(() => {
    if (!agentRunId) return;
    setLoading(true);
    api
      .get(`/actions/agent-run/${agentRunId}/available`)
      .then((res) => setActions(res.data.available_actions || []))
      .catch((err) => setError(err.response?.data?.detail || "Failed to load actions."))
      .finally(() => setLoading(false));
  }, [agentRunId]);

  // ── Start action ───────────────────────────────────────────────────────────
  const handleStart = useCallback(
    async (action) => {
      const actionType = action.action_type;
      setStarting(actionType);
      setError(null);
      try {
        const res = await api.post("/actions/run", {
          agent_run_id: agentRunId,
          action_type: actionType,
          user_context: userContext.trim() || null,
        });
        setShowContextFor(null);
        setUserContext("");
        onActionStarted?.(res.data.action_run_id, action.label);
      } catch (err) {
        setError(err.response?.data?.detail || "Failed to start action.");
      } finally {
        setStarting(null);
      }
    },
    [agentRunId, userContext, onActionStarted]
  );

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12, padding: "2.5rem 0" }}>
        <Spinner size={20} />
        <span style={{ fontSize: "0.855rem", color: "var(--ink-muted)" }}>Loading available actions…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ borderRadius: "var(--radius)", border: "1px solid var(--danger-light)", background: "var(--danger-light)", padding: "0.875rem 1rem", fontSize: "0.855rem", color: "var(--danger)" }}>
        {error}
      </div>
    );
  }

  const available = actions.filter((a) => a.is_available);
  const unavailable = actions.filter((a) => !a.is_available);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: "1.05rem", fontWeight: 400, fontFamily: "var(--font-display)", color: "var(--ink)" }}>
          What would you like to do?
        </span>
        <Badge variant="accent">{DOMAIN_LABELS[domain] || domain}</Badge>
      </div>

      {/* Available actions */}
      {available.length === 0 && unavailable.length === 0 && (
        <p style={{ fontSize: "0.855rem", color: "var(--ink-muted)" }}>No actions available for this document.</p>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {available.map((action) => (
          <ActionCard
            key={action.action_type}
            action={action}
            isStarting={starting === action.action_type}
            showContext={showContextFor === action.action_type}
            userContext={userContext}
            onUserContextChange={setUserContext}
            onClickStart={() => {
              if (showContextFor === action.action_type) {
                handleStart(action);
              } else {
                setShowContextFor(action.action_type);
                setUserContext("");
              }
            }}
            onCancel={() => {
              setShowContextFor(null);
              setUserContext("");
            }}
          />
        ))}
      </div>

      {/* Unavailable actions (missing credentials) */}
      {unavailable.length > 0 && (
        <div>
          <p style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--ink-muted)", marginBottom: 8 }}>
            Connect services to unlock
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
            {unavailable.map((action) => (
              <LockedActionCard key={action.action_type} action={action} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Available action card ──────────────────────────────────────────────────

function ActionCard({
  action,
  isStarting,
  showContext,
  userContext,
  onUserContextChange,
  onClickStart,
  onCancel,
}) {
  return (
    <div className="domain-card" style={{ padding: "1.1rem" }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <CatalogIcon name={action.icon} size={22} style={{ color: "var(--accent)", flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontWeight: 600, fontSize: "0.9rem", color: "var(--ink)", lineHeight: 1.3 }}>{action.label}</p>
          <p style={{ fontSize: "0.78rem", color: "var(--ink-muted)", marginTop: 3, lineHeight: 1.45 }}>
            {action.description}
          </p>
        </div>
      </div>

      {/* Optional context input */}
      {showContext && (
        <div style={{ marginTop: 10 }}>
          <label className="form-label" style={{ display: "block", marginBottom: 5 }}>
            Any additional instructions? (optional)
          </label>
          <textarea
            className="form-input"
            rows={2}
            maxLength={2000}
            placeholder="e.g. Prefer MedPlus pharmacy, morning slots only…"
            value={userContext}
            onChange={(e) => onUserContextChange(e.target.value)}
          />
        </div>
      )}

      {/* Action buttons */}
      <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
        <button
          onClick={onClickStart}
          disabled={isStarting}
          className="btn btn-primary btn-sm"
          style={{ flex: 1 }}
        >
          {isStarting ? (
            <>
              <span className="spinner" style={{ width: 13, height: 13 }} />
              Starting…
            </>
          ) : showContext ? (
            "Confirm & Start"
          ) : (
            <>
              <CatalogIcon name={action.icon} size={14} />
              {action.label}
            </>
          )}
        </button>
        {showContext && (
          <button onClick={onCancel} className="btn btn-ghost btn-sm">
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

// ── Locked action card (missing credentials) ───────────────────────────────

function LockedActionCard({ action }) {
  return (
    <div style={{ borderRadius: "var(--radius-lg)", border: "1.5px dashed var(--border)", background: "var(--paper)", padding: "1.1rem", opacity: 0.75 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <CatalogIcon name={action.icon} size={22} style={{ color: "var(--ink-muted)", flexShrink: 0 }} className="grayscale" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontWeight: 600, fontSize: "0.9rem", color: "var(--ink-secondary)" }}>{action.label}</p>
          <p style={{ fontSize: "0.78rem", color: "var(--ink-muted)", marginTop: 3 }}>{action.description}</p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 8 }}>
            {action.missing_credentials.map((svc) => (
              <span
                key={svc}
                style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: "0.72rem", padding: "2px 7px", borderRadius: 6, background: "var(--warning-light)", color: "var(--warning)", fontWeight: 500 }}
              >
                <Link2 size={11} /> {svc.replace(/_/g, " ")}
              </span>
            ))}
          </div>
        </div>
      </div>
      <a href="/settings/integrations" className="btn btn-ghost btn-sm" style={{ width: "100%", marginTop: 10 }}>
        Connect in Settings →
      </a>
    </div>
  );
}
