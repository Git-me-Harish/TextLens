import { useState } from "react";
import { NavLink, useNavigate, useLocation, Link } from "react-router-dom";
import {
  LayoutDashboard, History, LogOut, User, Menu, X,
  FileText, Cpu, ClipboardList, ChevronDown, ChevronRight,
  Layers, Key, Webhook, ShieldCheck, Zap, Upload,
  HeartPulse, Scale, Truck, GraduationCap, Building2, FileSearch,
  TrendingUp, MessageSquare,
} from "lucide-react";
import { useAuth } from "../../lib/AuthContext";
import { useAgent } from "../../lib/AgentContext";

/* ─────────────────────────────── nav config ──────────────────────── */

const NAV = [
  {
    label: null,
    items: [
      { label: "Dashboard", icon: LayoutDashboard, to: "/dashboard" },
    ],
  },
  {
    label: "Process",
    items: [
      { label: "Quick Extract",    icon: FileText,    to: "/tools/pdf-extract" },
      { label: "Image OCR",        icon: FileSearch,  to: "/tools/ocr-image" },
      { label: "Summarize",        icon: Zap,         to: "/tools/summarize" },
      { label: "PDF to Word",      icon: FileText,    to: "/tools/pdf-to-word" },
      { label: "PDF Chat",         icon: FileText,    to: "/tools/pdf-chat" },
      { label: "Chat History",     icon: MessageSquare, to: "/chat-history" },
    ],
  },
  {
    label: "Domain Pipelines",
    items: [
      { label: "Finance",          icon: TrendingUp,    to: "/pipelines?domain=finance" },
      { label: "Healthcare",       icon: HeartPulse,    to: "/pipelines?domain=healthcare" },
      { label: "Legal",            icon: Scale,         to: "/pipelines?domain=legal" },
      { label: "Logistics",        icon: Truck,         to: "/pipelines?domain=logistics" },
      { label: "HR & Education",   icon: GraduationCap, to: "/pipelines?domain=hr" },
      { label: "Government",       icon: Building2,     to: "/pipelines?domain=government" },
      { label: "All Pipelines",    icon: Cpu,           to: "/pipelines" },
    ],
  },
  {
    label: "Agents",
    items: [
      { label: "Run Agent",         icon: Cpu,          to: "/pipelines" },
      { label: "Agent History",     icon: ClipboardList,to: "/agent-history" },
    ],
  },
  {
    label: "Batch",
    items: [
      { label: "Batch Jobs",        icon: Layers,       to: "/batch" },
    ],
  },
  {
    label: "Data",
    items: [
      { label: "Extraction History",icon: History,      to: "/history" },
    ],
  },
  {
    label: "API",
    items: [
      { label: "API Keys",          icon: Key,          to: "/api-keys" },
      { label: "Webhooks",          icon: Webhook,      to: "/webhooks" },
    ],
  },
];

/* ─────────────────────────────── sidebar ─────────────────────────── */

function SidebarContent({ close }) {
  const { user, logout } = useAuth();
  const { activeAgent } = useAgent();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState({});
  const toggle = (label) => setCollapsed((c) => ({ ...c, [label]: !c[label] }));

  // Query-param-aware active check
  const isActive = (to) => {
    const [path, qs] = to.split("?");
    if (location.pathname !== path) return false;
    if (!qs) return location.search === ""; // "All Pipelines" only active when no query
    return location.search === `?${qs}`;
  };

  return (
    <>
      {/* Logo */}
      <div className="sidebar-logo">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div
              style={{
                fontFamily: "var(--font-display)",
                fontSize: "1.3rem",
                fontWeight: 300,
                letterSpacing: "-0.01em",
              }}
            >
              Text<span style={{ color: "var(--accent)" }}>Lens</span>
            </div>
            <div
              style={{
                fontSize: "0.62rem",
                color: "var(--ink-muted)",
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                marginTop: 1,
              }}
            >
              Document Intelligence
            </div>
          </div>
          {close && (
            <button
              onClick={close}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                color: "var(--ink-muted)",
                padding: 4,
              }}
            >
              <X size={18} />
            </button>
          )}
        </div>
      </div>

      {/* Nav */}
      <nav className="sidebar-body">
        {NAV.map((section, si) => (
          <div key={si}>
            {section.label && (
              <button
                onClick={() => toggle(section.label)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  width: "100%",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                <span className="sidebar-section-label">{section.label}</span>
                <span style={{ paddingRight: "0.5rem", color: "var(--ink-muted)" }}>
                  {collapsed[section.label] ? (
                    <ChevronRight size={11} />
                  ) : (
                    <ChevronDown size={11} />
                  )}
                </span>
              </button>
            )}

            {!collapsed[section.label] &&
              section.items.map(({ label, icon: Icon, to }) => {
                const [, qs] = to.split("?");
                const domainParam = qs ? new URLSearchParams(qs).get("domain") : null;
                const isDomainLink = to.startsWith("/pipelines?domain=");
                const isRunning = activeAgent && domainParam && activeAgent.domain === domainParam;

                const inner = (
                  <>
                    <Icon size={15} className="link-icon" />
                    {label}
                    {isRunning && (
                      <span style={{
                        marginLeft: "auto",
                        display: "flex",
                        alignItems: "center",
                        gap: 4,
                        fontSize: "0.62rem",
                        fontWeight: 600,
                        color: "var(--accent)",
                        letterSpacing: "0.04em",
                        textTransform: "uppercase",
                      }}>
                        <span style={{
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: "var(--accent)",
                          animation: "pulse 1.2s ease-in-out infinite",
                          flexShrink: 0,
                        }} />
                        Running
                      </span>
                    )}
                  </>
                );

                if (isDomainLink) {
                  return (
                    <Link key={to} to={to} className="sidebar-link" onClick={close}>
                      {inner}
                    </Link>
                  );
                }

                return (
                  <NavLink
                    key={to}
                    to={to}
                    className={({ isActive }) => `sidebar-link ${isActive ? "active" : ""}`}
                    onClick={close}
                  >
                    {inner}
                  </NavLink>
                );
              })}
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="sidebar-footer">
        <NavLink
          to="/profile"
          className="sidebar-link"
          onClick={close}
          style={{ marginBottom: 2 }}
        >
          {user?.avatar_url ? (
            <img
              src={user.avatar_url}
              alt=""
              style={{
                width: 20,
                height: 20,
                borderRadius: "50%",
                objectFit: "cover",
              }}
            />
          ) : (
            <div
              style={{
                width: 20,
                height: 20,
                borderRadius: "50%",
                background: "var(--accent-light)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: "0.65rem",
                fontWeight: 700,
                color: "var(--accent)",
              }}
            >
              {user?.full_name?.[0] || "U"}
            </div>
          )}
          <span style={{ flex: 1, fontSize: "0.84rem" }}>
            {user?.full_name?.split(" ")[0] || "Profile"}
          </span>
          <User size={13} style={{ color: "var(--ink-muted)" }} />
        </NavLink>

        <button
          onClick={() => logout()}
          className="sidebar-link"
          style={{ color: "var(--danger)" }}
        >
          <LogOut size={15} className="link-icon" />
          Sign out
        </button>
      </div>
    </>
  );
}

/* ─────────────────────────────── shell ───────────────────────────── */

export default function AppLayout({ children }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? "open" : ""}`}>
        <SidebarContent close={mobileOpen ? () => setMobileOpen(false) : null} />
      </aside>

      {mobileOpen && (
        <div
          onClick={() => setMobileOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.3)",
            zIndex: 150,
          }}
        />
      )}

      <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
        <header className="mobile-header">
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "1.15rem",
              fontWeight: 300,
            }}
          >
            Text<span style={{ color: "var(--accent)" }}>Lens</span>
          </div>
          <button
            onClick={() => setMobileOpen(true)}
            style={{ background: "none", border: "none", cursor: "pointer" }}
          >
            <Menu size={21} />
          </button>
        </header>

        <main className="main-content fade-in">{children}</main>
      </div>
    </div>
  );
}