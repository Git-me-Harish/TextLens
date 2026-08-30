/**
 * ActionRunner
 *
 * Orchestrates the full action run UX after an action has been started:
 *   1. Opens an SSE connection to /api/v1/actions/{id}/stream
 *   2. Renders real-time progress (planning → approval → executing → done)
 *   3. Mounts ApprovalModal when approval is required
 *   4. Shows final result or error state
 *
 * Props:
 *   actionRunId  — the action run to track
 *   actionLabel  — human-readable label for the action (for display)
 *   onComplete(result) — called when action completes successfully
 *   onClose()          — called when user dismisses the runner
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { Check, X } from "lucide-react";
import ApprovalModal from "./ApprovalModal";
import { STATUS_ICONS } from "../../lib/actionIcons";

const STATUS_CONFIG = {
  PENDING:           { icon: STATUS_ICONS.PENDING,            label: "Starting…",         color: "var(--ink-muted)" },
  PLANNING:          { icon: STATUS_ICONS.PLANNING,           label: "Building plan…",    color: "var(--accent)" },
  AWAITING_APPROVAL: { icon: STATUS_ICONS.AWAITING_APPROVAL,  label: "Awaiting approval",  color: "var(--warning)" },
  EXECUTING:         { icon: STATUS_ICONS.EXECUTING,          label: "Executing…",        color: "var(--accent)" },
  COMPLETED:         { icon: STATUS_ICONS.COMPLETED,          label: "Completed",         color: "var(--success)" },
  FAILED:            { icon: STATUS_ICONS.FAILED,             label: "Failed",            color: "var(--danger)" },
  REJECTED:          { icon: STATUS_ICONS.REJECTED,           label: "Rejected",          color: "var(--ink-muted)" },
  CANCELLED:         { icon: STATUS_ICONS.CANCELLED,          label: "Cancelled",         color: "var(--ink-muted)" },
};

export default function ActionRunner({ actionRunId, actionLabel, onComplete, onClose }) {
  const [status, setStatus] = useState("PENDING");
  const [events, setEvents] = useState([]);           // timeline of SSE events
  const [approval, setApproval] = useState(null);     // { plan, token, expiresAt }
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [toolCalls, setToolCalls] = useState([]);
  const eventsEndRef = useRef(null);
  const esRef = useRef(null);

  const pushEvent = useCallback((label, detail = null) => {
    setEvents((prev) => [
      ...prev,
      { id: Date.now(), label, detail, ts: new Date().toLocaleTimeString() },
    ]);
  }, []);

  // ── SSE subscription ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!actionRunId) return;

    // EventSource with auth — use a custom fetch-based SSE if needed
    // For simplicity here we pass the token via cookie (HttpOnly) or query param
    const token = localStorage.getItem("access_token");
    const url = `/api/v1/actions/${actionRunId}/stream?token=${encodeURIComponent(token || "")}`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.addEventListener("executing", (e) => {
      const data = JSON.parse(e.data);
      setStatus(data.status === "PLANNING" ? "PLANNING" : "EXECUTING");
      pushEvent(data.message || "Executing…");
    });

    es.addEventListener("plan_ready", (e) => {
      const data = JSON.parse(e.data);
      setStatus("AWAITING_APPROVAL");
      setApproval({
        plan: data.plan,
        token: data.approval_token,
        expiresAt: data.approval_expires_at,
      });
      pushEvent("Plan ready — waiting for your approval");
    });

    es.addEventListener("approval_required", () => {
      setStatus("AWAITING_APPROVAL");
    });

    es.addEventListener("tool_called", (e) => {
      const data = JSON.parse(e.data);
      const entry = {
        name: data.tool_name,
        success: data.success,
        latency_ms: data.latency_ms,
        ts: new Date().toLocaleTimeString(),
      };
      setToolCalls((prev) => [...prev, entry]);
      pushEvent(
        `Tool: ${data.tool_name}`,
        data.success ? `${data.latency_ms}ms` : "failed"
      );
    });

    es.addEventListener("completed", (e) => {
      const data = JSON.parse(e.data);
      setStatus("COMPLETED");
      setResult(data.action_result);
      pushEvent("Action completed successfully");
      onComplete?.(data.action_result);
      es.close();
    });

    es.addEventListener("failed", (e) => {
      const data = JSON.parse(e.data);
      setStatus("FAILED");
      setError(data.error_message);
      pushEvent(`Failed: ${data.error_message}`, data.recoverable ? "recoverable" : null);
      es.close();
    });

    es.addEventListener("cancelled", () => {
      setStatus("CANCELLED");
      pushEvent("Action was cancelled");
      es.close();
    });

    es.addEventListener("heartbeat", () => {/* keep-alive — no UI update */});

    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) return;
      pushEvent("Connection lost — retrying…");
    };

    return () => {
      es.close();
    };
  }, [actionRunId, pushEvent, onComplete]);

  // Scroll timeline to bottom on new events
  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.PENDING;
  const isTerminal = ["COMPLETED", "FAILED", "REJECTED", "CANCELLED"].includes(status);

  return (
    <>
      {/* Approval modal — rendered outside the runner card */}
      {approval && status === "AWAITING_APPROVAL" && (
        <ApprovalModal
          actionRunId={actionRunId}
          plan={approval.plan}
          approvalToken={approval.token}
          expiresAt={approval.expiresAt}
          onApproved={() => {
            setApproval(null);
            setStatus("EXECUTING");
            pushEvent("Approved — executing plan…");
          }}
          onRejected={() => {
            setApproval(null);
            setStatus("REJECTED");
            pushEvent("Action rejected by user");
            esRef.current?.close();
          }}
        />
      )}

      {/* Runner card */}
      <div className="card" style={{ overflow: "hidden" }}>

        {/* Status header */}
        <div style={{ padding: "1rem 1.25rem", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <cfg.icon size={20} style={{ color: cfg.color }} />
            <div>
              <p style={{ fontWeight: 600, fontSize: "0.9rem", color: "var(--ink)" }}>{actionLabel}</p>
              <p style={{ fontSize: "0.76rem", fontWeight: 500, color: cfg.color }}>{cfg.label}</p>
            </div>
          </div>
          {!isTerminal && (
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span className="pulsing" style={{ height: 7, width: 7, borderRadius: "50%", background: "var(--accent)" }} />
              <span style={{ fontSize: "0.72rem", color: "var(--ink-muted)" }}>Live</span>
            </div>
          )}
        </div>

        {/* Event timeline */}
        <div style={{ padding: "0.75rem 1.25rem", maxHeight: 192, overflowY: "auto", display: "flex", flexDirection: "column", gap: 6, background: "var(--paper)" }}>
          {events.length === 0 ? (
            <p style={{ fontSize: "0.76rem", color: "var(--ink-muted)", padding: "0.5rem 0" }}>Waiting for events…</p>
          ) : (
            events.map((ev) => (
              <div key={ev.id} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: "0.76rem" }}>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink-muted)", flexShrink: 0, opacity: 0.7 }}>{ev.ts}</span>
                <span style={{ color: "var(--ink-secondary)" }}>{ev.label}</span>
                {ev.detail && (
                  <span style={{ color: "var(--ink-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ev.detail}</span>
                )}
              </div>
            ))
          )}
          <div ref={eventsEndRef} />
        </div>

        {/* Tool calls summary */}
        {toolCalls.length > 0 && (
          <div style={{ padding: "0.75rem 1.25rem", borderTop: "1px solid var(--border)" }}>
            <p style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--ink-muted)", marginBottom: 8 }}>
              Tool calls ({toolCalls.length})
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {toolCalls.map((tc, i) => (
                <span
                  key={i}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    fontSize: "0.72rem", padding: "2px 8px", borderRadius: 100, fontWeight: 500,
                    background: tc.success ? "var(--success-light)" : "var(--danger-light)",
                    color: tc.success ? "var(--success)" : "var(--danger)",
                  }}
                >
                  {tc.success ? <Check size={11} /> : <X size={11} />} {tc.name}
                  {tc.latency_ms ? ` ${tc.latency_ms}ms` : ""}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Final result */}
        {result && (
          <div style={{ padding: "1rem 1.25rem", borderTop: "1px solid var(--success-light)", background: "var(--success-light)" }}>
            <p style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--success)", marginBottom: 6 }}>
              Result
            </p>
            <p style={{ fontSize: "0.855rem", fontWeight: 500, color: "var(--success)", lineHeight: 1.5 }}>
              {result.summary}
            </p>
            {result.next_steps?.length > 0 && (
              <ul style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                {result.next_steps.map((step, i) => (
                  <li key={i} style={{ fontSize: "0.76rem", color: "var(--success)", display: "flex", gap: 6 }}>
                    <span>→</span><span>{step}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Error state */}
        {error && (
          <div style={{ padding: "1rem 1.25rem", borderTop: "1px solid var(--danger-light)", background: "var(--danger-light)" }}>
            <p style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--danger)", marginBottom: 4 }}>
              Error
            </p>
            <p style={{ fontSize: "0.855rem", color: "var(--danger)" }}>{error}</p>
          </div>
        )}

        {/* Footer */}
        {isTerminal && (
          <div style={{ padding: "0.75rem 1.25rem", borderTop: "1px solid var(--border)", display: "flex", justifyContent: "flex-end" }}>
            <button onClick={onClose} className="btn btn-ghost btn-sm">
              Close
            </button>
          </div>
        )}
      </div>
    </>
  );
}
