/**
 * ApprovalModal
 *
 * Displayed when an action run emits the `approval_required` SSE event.
 * Shows the structured ActionPlan — steps, external services, data to be sent —
 * and two buttons: Approve (fires POST /approve) or Reject (fires POST /reject).
 *
 * Props:
 *   actionRunId   — the action run awaiting approval
 *   plan          — ActionPlan object from the SSE plan_ready event
 *   approvalToken — short-lived JWT from the SSE event
 *   expiresAt     — ISO string — countdown timer shown to user
 *   onApproved()  — called after successful approval
 *   onRejected()  — called after rejection
 *   onClose()     — called when modal is dismissed without action
 */

import { useState, useEffect, useRef } from "react";
import { AlertTriangle, Check, Clock, Link2, ShieldCheck, X } from "lucide-react";
import api from "../../lib/api";
import { Badge } from "../ui";

const RISK_BADGE = { low: "success", medium: "warning", high: "danger" };

export default function ApprovalModal({
  actionRunId,
  plan,
  approvalToken,
  expiresAt,
  onApproved,
  onRejected,
  onClose,
}) {
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [error, setError] = useState(null);
  const [secondsLeft, setSecondsLeft] = useState(null);

  // ── Countdown timer ────────────────────────────────────────────────────────
  // On expiry this must actually tell the backend, not just flip local UI
  // state — the token is single-use/DB-hashed server-side (approval_service.py),
  // so without this call the ActionRun sits in AWAITING_APPROVAL forever even
  // though the UI already told the user it was rejected.
  const expiredRef = useRef(false);
  useEffect(() => {
    if (!expiresAt) return;
    const calc = () => {
      const diff = Math.max(0, Math.floor((new Date(expiresAt) - Date.now()) / 1000));
      setSecondsLeft(diff);
      if (diff === 0 && !expiredRef.current) {
        expiredRef.current = true;
        api.post(`/actions/${actionRunId}/reject`, { reason: "Approval window expired" })
          .catch(() => {}) // best-effort — the backend also auto-expires the token server-side
          .finally(() => onRejected?.());
      }
    };
    calc();
    const interval = setInterval(calc, 1000);
    return () => clearInterval(interval);
  }, [expiresAt, actionRunId, onRejected]);

  const timeLabel = secondsLeft !== null
    ? `${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}`
    : null;

  // ── Approve ────────────────────────────────────────────────────────────────
  const handleApprove = async () => {
    setApproving(true);
    setError(null);
    try {
      await api.post(`/actions/${actionRunId}/approve`, {
        approval_token: approvalToken,
      });
      onApproved?.();
    } catch (err) {
      setError(err.response?.data?.detail || "Approval failed. The token may have expired.");
    } finally {
      setApproving(false);
    }
  };

  // ── Reject ─────────────────────────────────────────────────────────────────
  const handleReject = async () => {
    if (!showRejectInput) {
      setShowRejectInput(true);
      return;
    }
    setRejecting(true);
    setError(null);
    try {
      await api.post(`/actions/${actionRunId}/reject`, {
        reason: rejectReason.trim() || null,
      });
      onRejected?.();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to reject action.");
    } finally {
      setRejecting(false);
    }
  };

  if (!plan) return null;

  const riskVariant = RISK_BADGE[plan.risk_level] || "default";

  return (
    /* Backdrop */
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem" }}>
      <div style={{ background: "#fff", borderRadius: "var(--radius-lg)", width: "100%", maxWidth: 560, maxHeight: "85vh", display: "flex", flexDirection: "column", boxShadow: "var(--shadow-lg)" }}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "1.1rem 1.5rem", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          <ShieldCheck size={19} style={{ color: "var(--accent)", flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 style={{ fontSize: "1.05rem", fontWeight: 400, color: "var(--ink)" }}>Review Action Plan</h2>
            <p style={{ fontSize: "0.76rem", color: "var(--ink-muted)", marginTop: 1 }}>
              No external calls have been made yet. Review and approve to proceed.
            </p>
          </div>
          {timeLabel && (
            <div style={{
              display: "inline-flex", alignItems: "center", gap: 5, flexShrink: 0,
              fontFamily: "var(--font-mono)", fontSize: "0.82rem", fontWeight: 600,
              padding: "3px 10px", borderRadius: "var(--radius)",
              color: secondsLeft < 60 ? "var(--danger)" : "var(--ink-secondary)",
              background: secondsLeft < 60 ? "var(--danger-light)" : "var(--paper-2)",
            }}>
              <Clock size={13} /> {timeLabel}
            </div>
          )}
        </div>

        {/* Body */}
        <div style={{ padding: "1.25rem 1.5rem", display: "flex", flexDirection: "column", gap: "1.1rem", overflowY: "auto" }}>

          {/* Summary + risk */}
          <div style={{ borderRadius: "var(--radius)", border: "1px solid var(--border)", padding: "0.75rem 1rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
              <Badge variant={riskVariant}>{plan.risk_level} risk</Badge>
              <span style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>
                ~{plan.estimated_duration_seconds}s estimated
              </span>
            </div>
            <p style={{ fontSize: "0.855rem", color: "var(--ink-secondary)", lineHeight: 1.55 }}>{plan.summary}</p>
          </div>

          {/* External services */}
          {plan.external_services?.length > 0 && (
            <div>
              <p style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--ink-muted)", marginBottom: 8 }}>
                External services that will be called
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {plan.external_services.map((svc) => (
                  <span key={svc} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: "0.76rem", fontWeight: 500, padding: "3px 10px", borderRadius: 100, background: "var(--accent-light)", color: "var(--accent)" }}>
                    <Link2 size={12} /> {svc.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Data that will be sent */}
          {Object.keys(plan.data_to_be_sent || {}).length > 0 && (
            <div>
              <p style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--ink-muted)", marginBottom: 8 }}>
                Document data that will be used
              </p>
              <div style={{ borderRadius: "var(--radius)", border: "1px solid var(--border)", background: "var(--paper)" }}>
                {Object.entries(plan.data_to_be_sent).map(([key, val], i, arr) => (
                  <div key={key} style={{ display: "flex", gap: 12, padding: "0.5rem 0.875rem", fontSize: "0.82rem", borderBottom: i < arr.length - 1 ? "1px solid var(--border)" : "none" }}>
                    <span style={{ color: "var(--ink-muted)", fontWeight: 500, minWidth: 130, flexShrink: 0, textTransform: "capitalize" }}>
                      {key.replace(/_/g, " ")}
                    </span>
                    <span style={{ color: "var(--ink-secondary)", wordBreak: "break-word" }}>
                      {Array.isArray(val) ? val.join(", ") : String(val ?? "—")}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Steps */}
          <div>
            <p style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--ink-muted)", marginBottom: 8 }}>
              Execution steps ({plan.steps?.length || 0})
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {(plan.steps || []).map((step) => (
                <div key={step.step_number} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <span className="step-circle">{step.step_number}</span>
                  <div style={{ flex: 1, minWidth: 0, paddingTop: 2 }}>
                    <p style={{ fontSize: "0.855rem", color: "var(--ink)" }}>{step.description}</p>
                    <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                      {step.requires_external_call && (
                        <span style={{ fontSize: "0.72rem", color: "var(--accent)", background: "var(--accent-light)", padding: "1px 7px", borderRadius: 5 }}>
                          external call
                        </span>
                      )}
                      {!step.is_reversible && (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: "0.72rem", color: "var(--warning)", background: "var(--warning-light)", padding: "1px 7px", borderRadius: 5 }}>
                          <AlertTriangle size={11} /> not reversible
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Error */}
          {error && (
            <div style={{ borderRadius: "var(--radius)", border: "1px solid var(--danger-light)", background: "var(--danger-light)", padding: "0.7rem 1rem", fontSize: "0.855rem", color: "var(--danger)" }}>
              {error}
            </div>
          )}

          {/* Reject reason input */}
          {showRejectInput && (
            <div className="form-group">
              <label className="form-label">Reason for rejection (optional)</label>
              <textarea
                className="form-input"
                rows={2}
                maxLength={500}
                placeholder="e.g. Wrong pharmacy selected, let me re-initiate…"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
              />
            </div>
          )}
        </div>

        {/* Footer actions */}
        <div style={{ padding: "1rem 1.5rem", background: "var(--paper)", borderTop: "1px solid var(--border)", display: "flex", gap: 10, flexShrink: 0 }}>
          <button
            onClick={handleReject}
            disabled={rejecting || approving}
            className="btn btn-outline"
            style={{ flex: 1 }}
          >
            {rejecting ? "Rejecting…" : showRejectInput ? "Confirm Reject" : (<><X size={15} /> Reject</>)}
          </button>
          <button
            onClick={handleApprove}
            disabled={approving || rejecting || secondsLeft === 0}
            className="btn btn-primary"
            style={{ flex: 1 }}
          >
            {approving ? (
              <>
                <span className="spinner" style={{ width: 14, height: 14 }} />
                Approving…
              </>
            ) : (
              <><Check size={15} /> Approve & Execute</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
