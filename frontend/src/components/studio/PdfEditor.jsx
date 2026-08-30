import { useCallback, useEffect, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import * as pdfjsLib from "pdfjs-dist";
import pdfjsWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { PDFDocument, StandardFonts, degrees, rgb } from "pdf-lib";
import toast from "react-hot-toast";
import {
  Upload, RotateCw, Trash2, ArrowUp, ArrowDown, Type,
  ChevronLeft, ChevronRight, Save, X, FileText,
} from "lucide-react";
import api, { errMsg } from "../../lib/api";
import { Spinner } from "../ui";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerUrl;

const THUMB_WIDTH = 110;
const MAIN_WIDTH = 640;
const TEXT_SIZE_PT = 14;

let _uid = 0;
const nextId = () => `p${++_uid}`;

/**
 * Client-side PDF editor: page reorder/rotate/delete + text overlays.
 * Everything happens in-browser via pdfjs (render) + pdf-lib (export) —
 * only the finished PDF is sent to the backend (POST /studio/edit), which
 * just records it as a completed job for history/notifications/download,
 * same as every other Document Studio tool.
 *
 * Text can only be placed on pages with no pending rotation — an
 * unrotated on-screen click maps 1:1 to PDF point coordinates, but once a
 * page is rotated that mapping needs a per-orientation transform, which
 * would be easy to get subtly wrong. Simpler and honest to just disable
 * it, with a hint, rather than ship inaccurate placement.
 */
export default function PdfEditor({ action, onComplete }) {
  const [file, setFile] = useState(null);
  const [pages, setPages] = useState([]); // [{ key, originalIndex, rotation, widthPt, heightPt, thumb }]
  const [selectedKey, setSelectedKey] = useState(null);
  const [annotations, setAnnotations] = useState([]); // [{ id, pageKey, xRatio, yRatio, text }]
  const [addTextMode, setAddTextMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const srcDocRef = useRef(null); // pdfjs loaded document proxy
  const canvasRef = useRef(null);

  const onDrop = useCallback(async (accepted) => {
    const f = accepted[0];
    if (!f) return;
    setLoading(true);
    try {
      const buf = await f.arrayBuffer();
      const doc = await pdfjsLib.getDocument({ data: buf }).promise;
      srcDocRef.current = doc;

      const built = [];
      for (let i = 1; i <= doc.numPages; i++) {
        const page = await doc.getPage(i);
        const viewport = page.getViewport({ scale: 1 });
        const thumbScale = THUMB_WIDTH / viewport.width;
        const thumbViewport = page.getViewport({ scale: thumbScale });
        const canvas = document.createElement("canvas");
        canvas.width = thumbViewport.width;
        canvas.height = thumbViewport.height;
        await page.render({ canvasContext: canvas.getContext("2d"), viewport: thumbViewport }).promise;

        built.push({
          key: nextId(),
          originalIndex: i - 1,
          rotation: 0,
          widthPt: viewport.width,
          heightPt: viewport.height,
          thumb: canvas.toDataURL(),
        });
      }
      setFile(f);
      setPages(built);
      setSelectedKey(built[0]?.key || null);
      setAnnotations([]);
    } catch (err) {
      toast.error("Could not read that PDF — it may be corrupted or password-protected.");
    } finally {
      setLoading(false);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [] },
    multiple: false,
    disabled: loading,
  });

  const selected = pages.find((p) => p.key === selectedKey) || null;
  const selectedIndex = pages.findIndex((p) => p.key === selectedKey);

  // Render the selected page onto the main canvas whenever it changes
  useEffect(() => {
    if (!selected || !srcDocRef.current || !canvasRef.current) return;
    let cancelled = false;
    (async () => {
      const page = await srcDocRef.current.getPage(selected.originalIndex + 1);
      const scale = MAIN_WIDTH / page.getViewport({ scale: 1 }).width;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      if (!cancelled) {
        await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
      }
    })();
    return () => { cancelled = true; };
  }, [selected?.key]);

  const rotatePage = (key) => {
    setPages((prev) => prev.map((p) => p.key === key ? { ...p, rotation: (p.rotation + 90) % 360 } : p));
  };

  const deletePage = (key) => {
    if (pages.length <= 1) { toast.error("A PDF needs at least one page."); return; }
    setPages((prev) => {
      const next = prev.filter((p) => p.key !== key);
      if (selectedKey === key) setSelectedKey(next[0]?.key || null);
      return next;
    });
    setAnnotations((prev) => prev.filter((a) => a.pageKey !== key));
  };

  const movePage = (key, dir) => {
    setPages((prev) => {
      const idx = prev.findIndex((p) => p.key === key);
      const swapWith = idx + dir;
      if (swapWith < 0 || swapWith >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[swapWith]] = [next[swapWith], next[idx]];
      return next;
    });
  };

  const handleCanvasClick = (e) => {
    if (!addTextMode || !selected) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const xRatio = (e.clientX - rect.left) / rect.width;
    const yRatio = (e.clientY - rect.top) / rect.height;
    setAnnotations((prev) => [...prev, {
      id: nextId(), pageKey: selected.key, xRatio, yRatio, text: "Text",
    }]);
    setAddTextMode(false);
  };

  const updateAnnotationText = (id, text) => {
    setAnnotations((prev) => prev.map((a) => a.id === id ? { ...a, text } : a));
  };

  const removeAnnotation = (id) => {
    setAnnotations((prev) => prev.filter((a) => a.id !== id));
  };

  const reset = () => {
    setFile(null);
    setPages([]);
    setAnnotations([]);
    setSelectedKey(null);
    srcDocRef.current = null;
  };

  const saveAndFinish = async () => {
    if (!file || pages.length === 0) return;
    setSaving(true);
    try {
      const srcBytes = await file.arrayBuffer();
      const srcPdf = await PDFDocument.load(srcBytes);
      const outPdf = await PDFDocument.create();
      const font = await outPdf.embedFont(StandardFonts.Helvetica);

      const copied = await outPdf.copyPages(srcPdf, pages.map((p) => p.originalIndex));
      copied.forEach((copiedPage, i) => {
        const p = pages[i];
        outPdf.addPage(copiedPage);
        if (p.rotation !== 0) {
          const current = copiedPage.getRotation().angle;
          copiedPage.setRotation(degrees((current + p.rotation) % 360));
        }
        const pageAnnotations = annotations.filter((a) => a.pageKey === p.key);
        for (const a of pageAnnotations) {
          const x = a.xRatio * p.widthPt;
          const y = (1 - a.yRatio) * p.heightPt - TEXT_SIZE_PT;
          copiedPage.drawText(a.text || "", {
            x, y, size: TEXT_SIZE_PT, font, color: rgb(0.86, 0.15, 0.15),
          });
        }
      });

      const outBytes = await outPdf.save();
      const blob = new Blob([outBytes], { type: "application/pdf" });
      const editedName = file.name.replace(/\.pdf$/i, "") + "_edited.pdf";

      const form = new FormData();
      form.append("file", blob, editedName);
      form.append("original_filename", editedName);
      const { data: job } = await api.post("/studio/edit", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success("Edited PDF saved");
      onComplete(job);
    } catch (err) {
      toast.error(errMsg(err, "Could not save the edited PDF"));
    } finally {
      setSaving(false);
    }
  };

  if (!file) {
    return (
      <div className="card" style={{ padding: "1.5rem" }}>
        <div style={{ marginBottom: "1.25rem" }}>
          <h3 style={{ margin: 0, fontSize: "0.95rem", fontWeight: 700, color: "var(--ink)" }}>{action.label}</h3>
          <p style={{ margin: "0.25rem 0 0", fontSize: "0.8rem", color: "var(--ink-muted)" }}>{action.desc}</p>
        </div>
        <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
        <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`} style={{ minHeight: 120, border: "none", borderRadius: 0 }}>
          <input {...getInputProps()} />
          {loading ? (
            <div style={{ textAlign: "center" }}>
              <Spinner size={26} />
              <p style={{ marginTop: 10, color: "var(--ink-muted)", fontSize: "0.82rem" }}>Opening PDF…</p>
            </div>
          ) : (
            <>
              <div className="dropzone-icon" style={{ marginBottom: 6 }}><Upload size={22} /></div>
              <div className="dropzone-title" style={{ fontSize: "0.88rem" }}>Drop a PDF here or click to browse</div>
              <div className="dropzone-sub">PDF only</div>
            </>
          )}
        </div>
        </div>
      </div>
    );
  }

  const rotationLocked = selected && selected.rotation !== 0;

  return (
    <div className="card" style={{ padding: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem", flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <FileText size={15} style={{ color: action.color }} />
          <span style={{ fontWeight: 600, fontSize: "0.85rem" }}>{file.name}</span>
          <span style={{ fontSize: "0.72rem", color: "var(--ink-muted)" }}>{pages.length} page{pages.length === 1 ? "" : "s"}</span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={reset} className="btn btn-outline btn-sm" type="button">Start over</button>
          <button onClick={saveAndFinish} disabled={saving} className="btn btn-primary btn-sm" type="button" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {saving ? <Spinner size={13} /> : <Save size={13} />} Save & finish
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: "1rem" }}>
        {/* Page thumbnail strip */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 560, overflowY: "auto", paddingRight: 4 }}>
          {pages.map((p, i) => (
            <div
              key={p.key}
              onClick={() => setSelectedKey(p.key)}
              style={{
                border: `2px solid ${p.key === selectedKey ? action.color : "var(--border)"}`,
                borderRadius: 8, padding: 6, cursor: "pointer", background: "#fff",
              }}
            >
              <div style={{ display: "flex", justifyContent: "center", overflow: "hidden", borderRadius: 4 }}>
                <img
                  src={p.thumb}
                  alt={`Page ${i + 1}`}
                  style={{ maxWidth: "100%", transform: `rotate(${p.rotation}deg)`, transition: "transform 0.15s" }}
                />
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 5 }}>
                <span style={{ fontSize: "0.68rem", color: "var(--ink-muted)" }}>Page {i + 1}</span>
                <div style={{ display: "flex", gap: 2 }}>
                  <button type="button" title="Move up" onClick={(e) => { e.stopPropagation(); movePage(p.key, -1); }} className="icon-btn"><ArrowUp size={11} /></button>
                  <button type="button" title="Move down" onClick={(e) => { e.stopPropagation(); movePage(p.key, 1); }} className="icon-btn"><ArrowDown size={11} /></button>
                  <button type="button" title="Rotate 90°" onClick={(e) => { e.stopPropagation(); rotatePage(p.key); }} className="icon-btn"><RotateCw size={11} /></button>
                  <button type="button" title="Delete page" onClick={(e) => { e.stopPropagation(); deletePage(p.key); }} className="icon-btn" style={{ color: "var(--danger)" }}><Trash2 size={11} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Main preview */}
        <div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <button type="button" className="icon-btn" disabled={selectedIndex <= 0} onClick={() => setSelectedKey(pages[selectedIndex - 1].key)}><ChevronLeft size={14} /></button>
              <span style={{ fontSize: "0.78rem", color: "var(--ink-muted)" }}>Page {selectedIndex + 1} of {pages.length}</span>
              <button type="button" className="icon-btn" disabled={selectedIndex >= pages.length - 1} onClick={() => setSelectedKey(pages[selectedIndex + 1].key)}><ChevronRight size={14} /></button>
            </div>
            <button
              type="button"
              onClick={() => setAddTextMode((m) => !m)}
              disabled={rotationLocked}
              title={rotationLocked ? "Add text before rotating this page" : "Click the page to place text"}
              className={`btn btn-sm ${addTextMode ? "btn-primary" : "btn-outline"}`}
              style={{ display: "flex", alignItems: "center", gap: 6 }}
            >
              <Type size={13} /> {addTextMode ? "Click page to place…" : "Add text"}
            </button>
          </div>

          <div style={{ position: "relative", display: "inline-block", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", cursor: addTextMode ? "crosshair" : "default" }}>
            <canvas ref={canvasRef} onClick={handleCanvasClick} style={{ display: "block", maxWidth: "100%" }} />
            {selected && annotations.filter((a) => a.pageKey === selected.key).map((a) => (
              <div
                key={a.id}
                style={{
                  position: "absolute",
                  left: `${a.xRatio * 100}%`,
                  top: `${a.yRatio * 100}%`,
                  display: "flex", alignItems: "center", gap: 4,
                }}
              >
                <span
                  contentEditable
                  suppressContentEditableWarning
                  onBlur={(e) => updateAnnotationText(a.id, e.currentTarget.textContent)}
                  style={{
                    fontSize: 13, color: "#dc2626", fontWeight: 600,
                    background: "rgba(255,255,255,0.85)", padding: "1px 4px",
                    borderRadius: 3, outline: "1px dashed var(--accent)", minWidth: 20,
                  }}
                >
                  {a.text}
                </span>
                <button type="button" onClick={() => removeAnnotation(a.id)} className="icon-btn" style={{ background: "#fff" }}><X size={10} /></button>
              </div>
            ))}
          </div>
          {rotationLocked && (
            <p style={{ fontSize: "0.72rem", color: "var(--ink-muted)", marginTop: 6 }}>
              This page has a pending rotation — add any text before rotating for accurate placement.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
