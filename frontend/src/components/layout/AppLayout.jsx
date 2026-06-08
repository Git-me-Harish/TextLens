import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, History, LogOut, User, Menu, X,
  Image, FileText, FileOutput, FilePlus, MessageSquare,
  Cpu, ClipboardList, ChevronDown, ChevronRight,
} from "lucide-react";
import { useAuth } from "../../lib/AuthContext";

const NAV_SECTIONS = [
  {
    label: null,
    items: [
      { label: "Dashboard", icon: LayoutDashboard, to: "/dashboard" },
    ],
  },
  {
    label: "Process",
    items: [
      { label: "Image OCR", icon: Image, to: "/tools/ocr-image" },
      { label: "PDF Extract", icon: FileText, to: "/tools/pdf-extract" },
      { label: "Summarize", icon: FileOutput, to: "/tools/summarize" },
      { label: "PDF to Word", icon: FilePlus, to: "/tools/pdf-to-word" },
      { label: "PDF Chat", icon: MessageSquare, to: "/tools/pdf-chat" },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { label: "Domain Pipelines", icon: Cpu, to: "/pipelines" },
      { label: "Pipeline History", icon: ClipboardList, to: "/agent-history" },
    ],
  },
  {
    label: "Data",
    items: [
      { label: "Extraction History", icon: History, to: "/history" },
    ],
  },
];

function SidebarContent({ close }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState({});
  const toggle = (label) => setCollapsed(c => ({ ...c, [label]: !c[label] }));

  return (
    <>
      <div className="sidebar-logo">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: "1.3rem", fontWeight: 300, letterSpacing: "-0.01em" }}>
              Text<span style={{ color: "var(--accent)" }}>Lens</span>
            </div>
            <div style={{ fontSize: "0.62rem", color: "var(--ink-muted)", letterSpacing: "0.12em", textTransform: "uppercase", marginTop: 1 }}>
              Document Intelligence
            </div>
          </div>
          {close && (
            <button onClick={close} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)", padding: 4 }}>
              <X size={18} />
            </button>
          )}
        </div>
      </div>

      <nav className="sidebar-body">
        {NAV_SECTIONS.map((section, si) => (
          <div key={si}>
            {section.label && (
              <button onClick={() => toggle(section.label)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", background: "none", border: "none", cursor: "pointer", padding: 0 }}>
                <span className="sidebar-section-label">{section.label}</span>
                <span style={{ paddingRight: "0.5rem", color: "var(--ink-muted)" }}>
                  {collapsed[section.label] ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
                </span>
              </button>
            )}
            {!collapsed[section.label] && section.items.map(({ label, icon: Icon, to }) => (
              <NavLink key={to} to={to} className={({ isActive }) => `sidebar-link ${isActive ? "active" : ""}`} onClick={close}>
                <Icon size={15} className="link-icon" />
                {label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <NavLink to="/profile" className="sidebar-link" onClick={close} style={{ marginBottom: 2 }}>
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt="" style={{ width: 20, height: 20, borderRadius: "50%", objectFit: "cover" }} />
          ) : (
            <div style={{ width: 20, height: 20, borderRadius: "50%", background: "var(--accent-light)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.65rem", fontWeight: 700, color: "var(--accent)" }}>
              {user?.full_name?.[0] || "U"}
            </div>
          )}
          <span style={{ flex: 1, fontSize: "0.84rem" }}>{user?.full_name?.split(" ")[0] || "Profile"}</span>
          <User size={13} style={{ color: "var(--ink-muted)" }} />
        </NavLink>
        <button onClick={() => { logout(); }} className="sidebar-link" style={{ color: "var(--danger)" }}>
          <LogOut size={15} className="link-icon" />
          Sign out
        </button>
      </div>
    </>
  );
}

export default function AppLayout({ children }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? "open" : ""}`}>
        <SidebarContent close={mobileOpen ? () => setMobileOpen(false) : null} />
      </aside>
      {mobileOpen && <div onClick={() => setMobileOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.3)", zIndex: 150 }} />}
      <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
        <header className="mobile-header">
          <div style={{ fontFamily: "var(--font-display)", fontSize: "1.15rem", fontWeight: 300 }}>
            Text<span style={{ color: "var(--accent)" }}>Lens</span>
          </div>
          <button onClick={() => setMobileOpen(true)} style={{ background: "none", border: "none", cursor: "pointer" }}><Menu size={21} /></button>
        </header>
        <main className="main-content fade-in">{children}</main>
      </div>
    </div>
  );
}