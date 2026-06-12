import { useState, useEffect } from "react";
import { Search, FileText, Image, X, HardDrive, Loader } from "lucide-react";
import api, { errMsg } from "../lib/api";
import toast from "react-hot-toast";

export default function DrivePickerModal({ isOpen, onClose, onImport, jobType = "pdf_extract" }) {
  const [query, setQuery] = useState("");
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(null); // file id being imported

  const load = async (q = "") => {
    setLoading(true);
    try {
      const { data } = await api.get(`/drive/files${q ? `?query=${encodeURIComponent(q)}` : ""}`);
      setFiles(data.files || []);
    } catch (err) {
      toast.error(errMsg(err, "Could not load Drive files"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (isOpen) load(); }, [isOpen]);

  const handleImport = async (file) => {
    setImporting(file.id);
    try {
      const { data } = await api.post("/drive/import", { file_id: file.id, job_type: jobType });
      toast.success(`Importing ${file.name}…`);
      onImport(data);
      onClose();
    } catch (err) {
      toast.error(errMsg(err, "Import failed"));
    } finally {
      setImporting(null);
    }
  };

  if (!isOpen) return null;

  const FileIcon = ({ mime }) => mime?.includes("image") ? <Image size={15} /> : <FileText size={15} />;

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem" }}>
      <div style={{ background: "#fff", borderRadius: 14, width: "100%", maxWidth: 540, maxHeight: "80vh", display: "flex", flexDirection: "column", boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "1.1rem 1.25rem", borderBottom: "1px solid var(--border)" }}>
          <HardDrive size={18} style={{ color: "var(--accent)" }} />
          <span style={{ fontWeight: 700, fontSize: "0.95rem", flex: 1 }}>Import from Google Drive</span>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-muted)", padding: 4 }}><X size={16} /></button>
        </div>

        {/* Search */}
        <div style={{ padding: "0.875rem 1.25rem", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", gap: 8 }}>
            <div style={{ position: "relative", flex: 1 }}>
              <Search size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--ink-muted)" }} />
              <input
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === "Enter" && load(query)}
                placeholder="Search PDFs and images…"
                className="form-input"
                style={{ paddingLeft: 32, width: "100%" }}
              />
            </div>
            <button onClick={() => load(query)} className="btn btn-primary btn-sm" style={{ flexShrink: 0 }}>
              Search
            </button>
          </div>
        </div>

        {/* File list */}
        <div style={{ flex: 1, overflowY: "auto", padding: "0.5rem 0" }}>
          {loading ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, padding: "2.5rem", color: "var(--ink-muted)", fontSize: "0.875rem" }}>
              <Loader size={18} style={{ animation: "spin 0.7s linear infinite" }} /> Loading files…
            </div>
          ) : files.length === 0 ? (
            <div style={{ textAlign: "center", padding: "2.5rem", color: "var(--ink-muted)", fontSize: "0.875rem" }}>
              No PDF or image files found
            </div>
          ) : files.map(file => (
            <div key={file.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "0.625rem 1.25rem", cursor: "pointer", transition: "background 0.12s" }}
              onMouseEnter={e => e.currentTarget.style.background = "var(--paper-2,#f5f7f9)"}
              onMouseLeave={e => e.currentTarget.style.background = ""}
            >
              <div style={{ width: 32, height: 32, borderRadius: 8, background: "var(--paper-2,#f0f4ff)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, color: "var(--accent)" }}>
                <FileIcon mime={file.mimeType} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "0.85rem", fontWeight: 500, color: "var(--ink)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {file.name}
                </div>
                <div style={{ fontSize: "0.73rem", color: "var(--ink-muted)" }}>
                  {file.mimeType?.split("/")[1]?.toUpperCase()}
                  {file.size ? ` · ${Math.round(file.size / 1024)} KB` : ""}
                  {file.modifiedTime ? ` · ${new Date(file.modifiedTime).toLocaleDateString()}` : ""}
                </div>
              </div>
              <button
                onClick={() => handleImport(file)}
                disabled={importing === file.id}
                className="btn btn-outline btn-sm"
                style={{ flexShrink: 0 }}
              >
                {importing === file.id ? <Loader size={13} style={{ animation: "spin 0.7s linear infinite" }} /> : "Import"}
              </button>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div style={{ padding: "0.75rem 1.25rem", borderTop: "1px solid var(--border)", fontSize: "0.75rem", color: "var(--ink-muted)", textAlign: "center" }}>
          Only PDF and image files are shown · Files are imported and processed privately
        </div>
      </div>
    </div>
  );
}