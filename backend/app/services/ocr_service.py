import os
import re
import time
from pathlib import Path

# --- optional imports with graceful fallback ---
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

try:
    import pytesseract
    from PIL import Image
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

try:
    from unidecode import unidecode
except ImportError:
    def unidecode(s): return s  # passthrough if not installed

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    from docx import Document as DocxDocument
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False


def check_dependencies() -> dict:
    """Return status of all OCR dependencies — used by /health/deps endpoint."""
    return {
        "PyMuPDF (fitz)": HAS_FITZ,
        "pytesseract": HAS_TESSERACT,
        "python-docx": HAS_DOCX,
        "pandas": HAS_PANDAS,
        "unidecode": True,  # always available (fallback defined above)
    }


# --- text extraction ---

def extract_text_from_image(image_path: str) -> str:
    if not HAS_TESSERACT:
        raise RuntimeError("pytesseract not installed. Run: pip install pytesseract Pillow")
    img = Image.open(image_path)
    return pytesseract.image_to_string(img)


def extract_pdf_text_simple(pdf_path: str) -> tuple[str, int]:
    """Extract raw text from PDF. Returns (text, page_count)."""
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed. Run: pip install PyMuPDF")
    doc = fitz.open(pdf_path)
    pages = [doc.load_page(i).get_text() for i in range(len(doc))]
    count = len(doc)
    doc.close()
    return "\n\n".join(pages), count


def extract_pdf_headings_content(pdf_path: str) -> list[dict]:
    """Extract heading/content pairs using font size analysis."""
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed. Run: pip install PyMuPDF")

    doc = fitz.open(pdf_path)
    rows = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = unidecode(span["text"]).strip()
                    if not text:
                        continue
                    rows.append({
                        "text": text,
                        "size": span["size"],
                        "bold": "bold" in span["font"].lower(),
                    })
    doc.close()

    if not rows:
        return [{"heading": "Full Text", "content": "No text found"}]

    # Determine body font size (most common)
    sizes = [r["size"] for r in rows]
    body_size = max(set(sizes), key=sizes.count)

    sections, current_heading, current_body = [], "Document", []
    for row in rows:
        is_heading = row["size"] > body_size + 1 or (row["bold"] and row["size"] >= body_size)
        if is_heading:
            if current_body:
                sections.append({"heading": current_heading, "content": " ".join(current_body)})
            current_heading = row["text"]
            current_body = []
        else:
            current_body.append(row["text"])

    if current_body:
        sections.append({"heading": current_heading, "content": " ".join(current_body)})

    return sections if sections else [{"heading": "Full Text", "content": " ".join(r["text"] for r in rows)}]


def sections_to_word(sections: list[dict], output_path: str) -> str:
    if not HAS_DOCX:
        raise RuntimeError("python-docx not installed. Run: pip install python-docx")
    doc = DocxDocument()
    for sec in sections:
        doc.add_heading(sec["heading"], level=1)
        p = doc.add_paragraph(sec["content"])
        p.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        doc.add_paragraph("")
    doc.save(output_path)
    return output_path


def summarize_text(text: str, ratio: float = 0.3) -> str:
    """Extractive summarization by sentence scoring."""
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", text) if len(s.strip()) > 20]
    if not sentences:
        return text[:2000]  # fallback: return first 2000 chars

    words = re.findall(r"\w+", text.lower())
    freq: dict = {}
    for w in words:
        if len(w) > 3:
            freq[w] = freq.get(w, 0) + 1

    scored = sorted(
        [(sum(freq.get(w.lower(), 0) for w in re.findall(r"\w+", s)), s) for s in sentences],
        key=lambda x: -x[0]
    )
    keep = max(1, int(len(scored) * ratio))
    return ". ".join(s for _, s in scored[:keep]) + "."


def answer_question(question: str, text: str) -> str:
    """Keyword-based sentence retrieval QA."""
    keywords = [w for w in re.findall(r"\w+", question.lower()) if len(w) > 3]
    if not keywords:
        return "Please ask a more specific question."

    sentences = [s.strip() for s in re.split(r"[.!?\n]+", text) if len(s.strip()) > 15]
    scored = sorted(
        [(sum(1 for kw in keywords if kw in s.lower()), s) for s in sentences if any(kw in s.lower() for kw in keywords)],
        key=lambda x: -x[0]
    )
    if not scored:
        return "No relevant information found for your question."
    return " ".join(s for _, s in scored[:5])


# --- main dispatcher ---

def process_job(job_type: str, file_path: str, extra: dict = None) -> dict:
    start = time.time()
    extra = extra or {}
    result = {"text": None, "file_path": None, "error": None, "page_count": None, "processing_time_ms": 0}

    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        if job_type == "ocr_image":
            result["text"] = extract_text_from_image(file_path)

        elif job_type == "pdf_extract":
            text, pages = extract_pdf_text_simple(file_path)
            result["text"] = text or "(No text found in PDF)"
            result["page_count"] = pages

        elif job_type == "pdf_summarize":
            text, pages = extract_pdf_text_simple(file_path)
            result["text"] = summarize_text(text, float(extra.get("ratio", 0.3)))
            result["page_count"] = pages

        elif job_type == "pdf_to_word":
            sections = extract_pdf_headings_content(file_path)
            out_path = file_path.rsplit(".", 1)[0] + "_extracted.docx"
            sections_to_word(sections, out_path)
            result["file_path"] = out_path
            result["text"] = f"Extracted {len(sections)} section(s)"
            result["page_count"] = len(sections)

        elif job_type == "pdf_qa":
            text, pages = extract_pdf_text_simple(file_path)
            result["text"] = answer_question(extra.get("question", ""), text)
            result["page_count"] = pages

        else:
            raise ValueError(f"Unknown job type: {job_type}")

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    result["processing_time_ms"] = int((time.time() - start) * 1000)
    return result