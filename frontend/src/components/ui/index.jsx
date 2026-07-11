import { forwardRef } from "react";

export function Button({ children, variant = "primary", size = "md", loading, disabled, className = "", ...props }) {
  const vars = { primary: "btn-primary", ghost: "btn-ghost", danger: "btn-danger", outline: "btn-outline" };
  const sizes = { sm: "btn-sm", md: "", lg: "btn-lg" };
  return (
    <button className={`btn ${vars[variant] || ""} ${sizes[size]} ${className}`} disabled={disabled || loading} {...props}>
      {loading && <span className="spinner" style={{ width: 16, height: 16, marginRight: 8 }} />}
      {children}
    </button>
  );
}

// forwardRef required for react-hook-form register() to attach refs
export const Input = forwardRef(function Input({ label, error, className = "", ...props }, ref) {
  return (
    <div className={`form-group ${className}`}>
      {label && <label className="form-label">{label}</label>}
      <input ref={ref} className={`form-input ${error ? "form-input--error" : ""}`} {...props} />
      {error && <span className="form-error">{error}</span>}
    </div>
  );
});

export function Badge({ children, variant = "default" }) {
  const colors = {
    default: "background:var(--paper-3);color:var(--ink-secondary)",
    success: "background:var(--success-light);color:var(--success)",
    warning: "background:var(--warning-light);color:var(--warning)",
    danger: "background:var(--danger-light);color:var(--danger)",
    accent: "background:var(--accent-light);color:var(--accent)",
    processing: "background:#fef3c7;color:#92400e",
  };
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 10px", borderRadius: 100,
      fontSize: "0.72rem", fontWeight: 500, letterSpacing: "0.03em",
      textTransform: "uppercase",
      ...(Object.fromEntries((colors[variant] || colors.default).split(";").map(p => p.split(":")))),
    }}>
      {children}
    </span>
  );
}

export function Card({ children, className = "", style }) {
  return <div className={`card ${className}`} style={style}>{children}</div>;
}

export function Divider() {
  return <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "1.5rem 0" }} />;
}

export function Spinner({ size = 24 }) {
  return (
    <div style={{
      display: "inline-block", width: size, height: size,
      border: "2px solid var(--border)", borderTopColor: "var(--accent)",
      borderRadius: "50%", animation: "spin 0.7s linear infinite",
    }} />
  );
}