/**
 * ConfirmDialog — a real confirmation step before destructive actions.
 *
 * Before this, deletes fell into two camps, both bad: Extraction History,
 * Pipeline History and Schedules deleted immediately on click with no
 * confirmation at all, and everywhere else used the native `confirm()`, which
 * is unstyled, unskippable by keyboard conventions, and looks nothing like
 * the rest of the app.
 *
 * Two deliberate choices:
 *  - The confirm button says what will happen ("Move to Trash", "Delete
 *    forever"), not "OK". A user skim-reading a dialog reads the button, not
 *    the prose, so the button has to carry the meaning on its own.
 *  - `tone="danger"` is reserved for irreversible actions. Ordinary deletes
 *    now go to Trash and are recoverable, so shouting at the user about a
 *    reversible action just trains them to click through warnings that matter.
 *
 * Escape closes, Enter confirms, focus lands on the confirm button, and the
 * backdrop click cancels — a dialog you can't dismiss with the keyboard is
 * worse than no dialog.
 */

import { useEffect, useRef } from "react";
import { AlertTriangle, Trash2 } from "lucide-react";
import { Button } from "./ui";

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",          // "default" | "danger"
  loading = false,
  onConfirm,
  onCancel,
}) {
  const confirmRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();

    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onCancel?.(); }
      // Enter confirms only while focus is on the dialog's own button, so a
      // stray Enter elsewhere on the page can't trigger a delete.
      if (e.key === "Enter" && document.activeElement === confirmRef.current) {
        e.preventDefault();
        if (!loading) onConfirm?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, loading, onConfirm, onCancel]);

  if (!open) return null;

  const danger = tone === "danger";
  const accent = danger ? "var(--danger, #dc2626)" : "var(--accent)";
  const Icon = danger ? AlertTriangle : Trash2;

  return (
    <div
      role="presentation"
      onClick={onCancel}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)",
        zIndex: 1200, display: "flex", alignItems: "center",
        justifyContent: "center", padding: "1rem",
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="card"
        style={{ width: "100%", maxWidth: 440, padding: "1.5rem" }}
      >
        <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10, flexShrink: 0,
            background: danger ? "rgba(220,38,38,0.10)" : "var(--surface-2, #f1f5f9)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <Icon size={19} color={accent} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: "1rem", color: "var(--ink)" }}>
              {title}
            </div>
            {message && (
              <div style={{
                fontSize: "0.85rem", color: "var(--ink-muted)",
                marginTop: 6, lineHeight: 1.6,
              }}>
                {message}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: "1.5rem" }}>
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            disabled={loading}
            className="btn"
            style={{
              background: accent, color: "#fff", border: "none",
              borderRadius: 8, padding: "0.5rem 1.1rem",
              fontWeight: 600, fontSize: "0.875rem",
              cursor: loading ? "default" : "pointer",
              opacity: loading ? 0.7 : 1,
            }}
          >
            {loading ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
