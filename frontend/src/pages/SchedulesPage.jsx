import { useState, useEffect } from "react";
import { Clock, Plus, Trash2, Play, Pause, Calendar, HardDrive, Cpu } from "lucide-react";
import { formatDistanceToNow, format } from "date-fns";
import toast from "react-hot-toast";
import api, { errMsg } from "../lib/api";
import { Spinner } from "../components/ui";

const DOMAIN_LABELS = {
  finance: "Finance", healthcare: "Healthcare", legal: "Legal",
  logistics: "Logistics", hr: "HR & Education", government: "Government", general: "General",
};

function CreateScheduleModal({ onClose, onCreate, catalog }) {
  const [form, setForm] = useState({
    name: "", cron_expr: "weekly_mon", domain: "finance",
    pipeline_type: "", drive_folder_id: "", user_instructions: "",
  });
  const [saving, setSaving] = useState(false);
  const [presets, setPresets] = useState([]);

  useEffect(() => {
    api.get("/schedules/presets").then(r => setPresets(r.data)).catch(() => {});
  }, []);

  const domainPipelines = form.domain && catalog?.[form.domain]?.pipelines
    ? Object.entries(catalog[form.domain].pipelines)
    : [];

  useEffect(() => {
    if (domainPipelines.length && !form.pipeline_type) {
      setForm(f => ({ ...f, pipeline_type: domainPipelines[0][0] }));
    }
  }, [form.domain, catalog]);

  const submit = async () => {
    if (!form.name || !form.pipeline_type) return toast.error("Name and pipeline required");
    setSaving(true);
    try {
      const { data } = await api.post("/schedules", {
        ...form,
        drive_folder_id: form.drive_folder_id || null,
        user_instructions: form.user_instructions || null,
      });
      onCreate(data);
      toast.success("Schedule created");
      onClose();
    } catch (err) {
      toast.error(errMsg(err, "Failed to create schedule"));
    } finally {
      setSaving(false);
    }
  };

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem" }}>
      <div style={{ background: "#fff", borderRadius: 14, width: "100%", maxWidth: 520, boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }}>
        <div style={{ padding: "1.1rem 1.25rem", borderBottom: "1px solid var(--border)", fontWeight: 700, fontSize: "0.95rem" }}>
          New Scheduled Batch
        </div>
        <div style={{ padding: "1.25rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <label className="form-label">Schedule name</label>
            <input className="form-input" value={form.name} onChange={e => set("name", e.target.value)} placeholder="e.g. Weekly invoice processing" style={{ width: "100%" }} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
            <div>
              <label className="form-label">Domain</label>
              <select className="form-input" value={form.domain} onChange={e => { set("domain", e.target.value); set("pipeline_type", ""); }} style={{ width: "100%" }}>
                {Object.entries(DOMAIN_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div>
              <label className="form-label">Pipeline</label>
              <select className="form-input" value={form.pipeline_type} onChange={e => set("pipeline_type", e.target.value)} style={{ width: "100%" }}>
                {domainPipelines.map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            </div>
          </div>

          <div>
            <label className="form-label">Schedule</label>
            <select className="form-input" value={form.cron_expr} onChange={e => set("cron_expr", e.target.value)} style={{ width: "100%" }}>
              {presets.map(p => <option key={p.key} value={p.key}>{p.label} ({p.cron})</option>)}
              <option value="custom">Custom cron expression…</option>
            </select>
            {form.cron_expr === "custom" && (
              <input className="form-input" placeholder="e.g. 0 9 * * 1" style={{ width: "100%", marginTop: 6, fontFamily: "monospace" }}
                onChange={e => set("cron_expr", e.target.value)} />
            )}
          </div>

          <div>
            <label className="form-label">Google Drive folder ID <span style={{ color: "var(--ink-muted)", fontWeight: 400 }}>(optional)</span></label>
            <input className="form-input" value={form.drive_folder_id} onChange={e => set("drive_folder_id", e.target.value)}
              placeholder="Paste Drive folder ID — files will be imported each run" style={{ width: "100%" }} />
          </div>

          <div>
            <label className="form-label">Instructions <span style={{ color: "var(--ink-muted)", fontWeight: 400 }}>(optional)</span></label>
            <textarea className="form-input" rows={2} value={form.user_instructions} onChange={e => set("user_instructions", e.target.value)}
              placeholder="Any specific extraction focus…" style={{ width: "100%", resize: "none" }} />
          </div>
        </div>
        <div style={{ padding: "0.875rem 1.25rem", borderTop: "1px solid var(--border)", display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button onClick={onClose} className="btn btn-outline">Cancel</button>
          <button onClick={submit} disabled={saving} className="btn btn-primary">
            {saving ? <Spinner size={14} /> : <Plus size={14} />} Create schedule
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [catalog, setCatalog] = useState(null);
  const [toggling, setToggling] = useState(null);
  const [deleting, setDeleting] = useState(null);

  useEffect(() => {
    Promise.all([
      api.get("/schedules"),
      api.get("/agents/catalog"),
    ]).then(([s, c]) => { setSchedules(s.data); setCatalog(c.data); })
      .catch(() => toast.error("Failed to load schedules"))
      .finally(() => setLoading(false));
  }, []);

  const toggle = async (id) => {
    setToggling(id);
    try {
      const { data } = await api.patch(`/schedules/${id}/toggle`);
      setSchedules(prev => prev.map(s => s.id === id ? data : s));
    } catch (err) { toast.error(errMsg(err, "Failed to toggle")); }
    finally { setToggling(null); }
  };

  const remove = async (id) => {
    setDeleting(id);
    try {
      await api.delete(`/schedules/${id}`);
      setSchedules(prev => prev.filter(s => s.id !== id));
      toast.success("Schedule deleted");
    } catch (err) { toast.error(errMsg(err, "Failed to delete")); }
    finally { setDeleting(null); }
  };

  return (
    <div>
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h1 className="page-title">Schedules</h1>
          <p className="page-subtitle">Automated recurring batch pipeline runs</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn btn-primary" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Plus size={15} /> New schedule
        </button>
      </div>

      {loading ? (
        <div style={{ display: "flex", justifyContent: "center", paddingTop: "3rem" }}><Spinner size={24} /></div>
      ) : schedules.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "3rem" }}>
          <Clock size={32} style={{ opacity: 0.3, marginBottom: 12 }} />
          <p style={{ color: "var(--ink-muted)", fontSize: "0.9rem", margin: 0 }}>No scheduled runs yet</p>
          <p style={{ color: "var(--ink-muted)", fontSize: "0.8rem", margin: "0.5rem 0 1.25rem" }}>
            Automate batch processing — pull from Drive, run a pipeline, get results on a schedule
          </p>
          <button onClick={() => setShowCreate(true)} className="btn btn-primary">Create your first schedule</button>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.625rem" }}>
          {schedules.map(s => (
            <div key={s.id} className="card" style={{ padding: "1rem 1.25rem", display: "flex", alignItems: "center", gap: "1rem" }}>
              {/* Status dot */}
              <div style={{ width: 10, height: 10, borderRadius: "50%", background: s.is_active ? "var(--success)" : "var(--border)", flexShrink: 0,
                animation: s.is_active ? "pulse 2s ease-in-out infinite" : "none" }} />

              {/* Info */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--ink)" }}>{s.name}</span>
                  <span style={{ fontSize: "0.72rem", background: "var(--paper-2)", border: "1px solid var(--border)", borderRadius: 6, padding: "1px 7px", fontFamily: "monospace", color: "var(--ink-secondary)" }}>
                    {s.cron_expr}
                  </span>
                  {!s.is_active && <span style={{ fontSize: "0.72rem", color: "var(--ink-muted)" }}>paused</span>}
                </div>
                <div style={{ display: "flex", gap: 14, marginTop: 4, flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)", display: "flex", alignItems: "center", gap: 4 }}>
                    <Cpu size={11} /> {DOMAIN_LABELS[s.domain]} · {s.pipeline_type.replace(/_/g, " ")}
                  </span>
                  {s.drive_folder_id && (
                    <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)", display: "flex", alignItems: "center", gap: 4 }}>
                      <HardDrive size={11} /> Drive folder connected
                    </span>
                  )}
                  {s.next_run_at && (
                    <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)", display: "flex", alignItems: "center", gap: 4 }}>
                      <Calendar size={11} /> Next: {formatDistanceToNow(new Date(s.next_run_at), { addSuffix: true })}
                    </span>
                  )}
                  {s.run_count > 0 && (
                    <span style={{ fontSize: "0.75rem", color: "var(--ink-muted)" }}>
                      {s.run_count} run{s.run_count !== 1 ? "s" : ""}
                      {s.last_run_at ? ` · last ${formatDistanceToNow(new Date(s.last_run_at), { addSuffix: true })}` : ""}
                    </span>
                  )}
                </div>
              </div>

              {/* Actions */}
              <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                <button onClick={() => toggle(s.id)} disabled={toggling === s.id} title={s.is_active ? "Pause" : "Resume"}
                  className="btn btn-outline btn-sm" style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  {toggling === s.id ? <Spinner size={12} /> : s.is_active ? <Pause size={13} /> : <Play size={13} />}
                  {s.is_active ? "Pause" : "Resume"}
                </button>
                <button onClick={() => remove(s.id)} disabled={deleting === s.id} title="Delete"
                  className="btn btn-outline btn-sm" style={{ color: "var(--danger)", borderColor: "var(--danger)" }}>
                  {deleting === s.id ? <Spinner size={12} /> : <Trash2 size={13} />}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && catalog && (
        <CreateScheduleModal
          onClose={() => setShowCreate(false)}
          onCreate={s => setSchedules(prev => [s, ...prev])}
          catalog={catalog}
        />
      )}
    </div>
  );
}