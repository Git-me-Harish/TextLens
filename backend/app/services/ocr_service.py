"""
Multi-model OCR service.

Strategy per file type:
  PDF (text-based)  → PyMuPDF direct text extraction (fast, accurate)
  PDF (scanned)     → PyMuPDF page render → Tesseract OCR per page
  Image             → Preprocessing pipeline → Tesseract
  Mixed/fallback    → Try text first, if <50 chars/page fall back to OCR

Image preprocessing chain (improves accuracy significantly):
  1. Convert to grayscale
  2. Upscale to 300dpi-equivalent if small
  3. Deskew (rotate correction)
  4. Adaptive threshold / denoise
  5. Tesseract with best config
"""

import math
import os
import re
import tempfile
import time

import structlog

logger = structlog.get_logger(__name__)

try:
    import fitz  # PyMuPDF

    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

try:
    import pytesseract
    from pytesseract import Output

    HAS_TESSERACT = True
    try:
        pytesseract.get_tesseract_version()
        TESSERACT_BINARY_OK = True
    except Exception:
        TESSERACT_BINARY_OK = False
except ImportError:
    HAS_TESSERACT = False
    TESSERACT_BINARY_OK = False

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    # Pillow has no built-in HEIC/HEIF decoder (Apple's format is patent-
    # encumbered, unlike JPEG/PNG). Registering this plugin makes every
    # existing Image.open() call in this file handle .heic/.heif
    # transparently — iPhone/Android photos, the single most common "why
    # won't it upload" format this app was missing — with no other code
    # path needing to know the format exists.
    import pillow_heif

    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except ImportError:
    HAS_HEIF = False

try:
    import cv2
    import numpy as np

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    # PP-StructureV3 — table/formula/handwriting-aware document parsing.
    # Deliberately import-only here; the actual ~1.8GB model weights and the
    # pipeline object itself are loaded lazily by _get_paddle_pipeline()
    # below, not at module import time.
    #
    # Only ever installed into the Linux Docker image (see Dockerfile) —
    # PaddlePaddle's native CPU inference engine was verified to segfault
    # under native Windows. This import simply fails on a Windows dev venv
    # where the package was never installed, HAS_PADDLE lands False, and
    # every call below falls back to Tesseract — the same graceful
    # degradation every other optional dependency in this file already
    # follows, not a Windows-specific special case.
    from paddleocr import PPStructureV3

    HAS_PADDLE = True
except ImportError:
    HAS_PADDLE = False

try:
    from unidecode import unidecode
except ImportError:

    def unidecode(s):
        return s


try:
    from docx import Document as DocxDocument
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import pandas as pd

    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# Tesseract config — PSM 3 = fully automatic page segmentation
TESS_CONFIG = r"--oem 3 --psm 3"
# For single-column documents
TESS_CONFIG_SINGLE = r"--oem 3 --psm 6"
# For sparse text (receipts, forms)
TESS_CONFIG_SPARSE = r"--oem 3 --psm 11"

MIN_CHARS_PER_PAGE = 40  # below this → page is likely scanned, fall back to OCR

try:
    from app.core.config import settings

    TESS_LANG = settings.TESSERACT_LANGUAGES
    _STRUCTURED_OCR_SETTING = settings.ENABLE_STRUCTURED_OCR
except Exception:
    # ocr_service.py is also exercised by scripts/tests that don't load full
    # app settings — fall back to English-only rather than failing to import.
    TESS_LANG = "eng"
    _STRUCTURED_OCR_SETTING = False

# HAS_PADDLE (above) reflects only whether the package imports — true on
# every Linux Docker image, regardless of the host's available RAM.
# PADDLE_ENABLED is the actual usage gate: package present AND the operator
# opted in via ENABLE_STRUCTURED_OCR. Every call site below checks this, not
# HAS_PADDLE directly, so the default (flag off) is Tesseract-only for
# everyone who pulls this repo — no memory risk, no setup — and the heavier
# engine only ever runs for someone who explicitly turned it on.
PADDLE_ENABLED = HAS_PADDLE and _STRUCTURED_OCR_SETTING


def _missing_tesseract_languages() -> list[str]:
    """
    Cross-checks TESS_LANG against what Tesseract actually has installed —
    catches the case where TESSERACT_LANGUAGES is extended (e.g. to add
    Tamil) without also updating the Dockerfile's language packages, which
    would otherwise fail silently deep inside an OCR call instead of showing
    up here at startup/health-check time.
    """
    if not (HAS_TESSERACT and TESSERACT_BINARY_OK):
        return []
    try:
        installed = set(pytesseract.get_languages(config=""))
    except Exception:
        return []
    wanted = set(TESS_LANG.split("+"))
    return sorted(wanted - installed)


def check_dependencies() -> dict:
    return {
        "PyMuPDF": HAS_FITZ,
        "Tesseract binary": TESSERACT_BINARY_OK,
        "Pillow": HAS_PIL,
        "HEIC/HEIF support": HAS_HEIF,
        "PaddleOCR (structured parsing) installed": HAS_PADDLE,
        "PaddleOCR (structured parsing) enabled": PADDLE_ENABLED,
        "OpenCV": HAS_CV2,
        "python-docx": HAS_DOCX,
        "Tesseract languages configured": TESS_LANG,
        "Tesseract languages missing": _missing_tesseract_languages(),
    }


# Image preprocessing
def _pil_to_cv2(pil_img):
    import numpy as np

    return cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2BGR)


def _cv2_to_pil(cv2_img):
    return Image.fromarray(cv2.cvtColor(cv2_img, cv2.COLOR_BGR2RGB))


def _deskew(img_cv2):
    """Detect and correct skew angle using Hough transform."""
    import numpy as np

    gray = cv2.cvtColor(img_cv2, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, math.pi / 180, threshold=100, minLineLength=100, maxLineGap=10
    )
    if lines is None:
        return img_cv2
    angles = []
    for line in lines:
        # cv2.HoughLinesP is documented as returning shape (N, 1, 4), so each
        # `line` is a single-row 2D array and `line[0]` is the (x1,y1,x2,y2)
        # vector. This build (opencv 5.0.0) instead returns shape (N, 4) —
        # `line` is already that vector, and `line[0]` is just x1, a lone
        # numpy.int32 that can't be unpacked into four names. Reproduced
        # directly: lines.shape came back (4, 4), not (4, 1, 4). Flattening
        # first makes this correct under either shape rather than betting on
        # one OpenCV version's convention.
        x1, y1, x2, y2 = np.asarray(line).reshape(-1)[:4]
        if x2 != x1:
            angles.append(math.degrees(math.atan2(y2 - y1, x2 - x1)))
    if not angles:
        return img_cv2
    # Median angle, ignore near-vertical lines
    angles = [a for a in angles if abs(a) < 45]
    if not angles:
        return img_cv2
    median_angle = sorted(angles)[len(angles) // 2]
    if abs(median_angle) < 0.5:
        return img_cv2
    h, w = img_cv2.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), median_angle, 1.0)
    return cv2.warpAffine(
        img_cv2, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _upscale_if_small(img: Image.Image, min_dpi_width: int = 1200) -> Image.Image:
    """Upscale small images — Tesseract accuracy drops below ~150dpi."""
    w, h = img.size
    if w < min_dpi_width:
        scale = min_dpi_width / w
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
    return img


def preprocess_image(img: Image.Image) -> Image.Image:
    """
    Full preprocessing pipeline for OCR:
    grayscale → upscale → deskew → denoise → threshold
    """
    # Upscale if too small
    img = _upscale_if_small(img)

    if HAS_CV2:
        cv = _pil_to_cv2(img)

        # Deskew
        cv = _deskew(cv)

        # Convert to grayscale
        gray = cv2.cvtColor(cv, cv2.COLOR_BGR2GRAY)

        # Denoise
        gray = cv2.fastNlMeansDenoising(gray, h=10)

        # Adaptive threshold → binary image (handles uneven lighting)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
        )

        # Slight sharpening kernel
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharpened = cv2.filter2D(binary, -1, kernel)

        return Image.fromarray(sharpened)
    else:
        # PIL-only fallback
        img = img.convert("L")  # grayscale
        img = ImageOps.autocontrast(img, cutoff=2)  # contrast stretch
        img = img.filter(ImageFilter.SHARPEN)
        return img


# Core OCR functions
def _ocr_with_confidence(img: "Image.Image", config: str, lang: str) -> tuple[str, float | None]:
    """
    Run Tesseract via image_to_data (not image_to_string) so we get each
    word's real per-token confidence from the OCR engine itself, and
    aggregate to one score for the page/image.

    This is a genuine OCR-engine confidence signal — distinct from, and not
    to be confused with, the LLM's own self-reported "confidence" field in
    the later structured-extraction step (agent_service.py). That one is an
    LLM guessing a number about its own field extraction; this one is a
    measurement Tesseract makes about how sure it is of the characters it saw.

    Returns (text, mean_confidence_0_to_100). Confidence is None if no words
    were recognized (Tesseract reports -1 confidence for non-text elements,
    which are excluded from the average).
    """
    data = pytesseract.image_to_data(img, config=config, lang=lang, output_type=Output.DICT)
    words = []
    confidences = []
    for text, conf in zip(data["text"], data["conf"]):
        text = text.strip()
        if not text:
            continue
        words.append(text)
        conf = float(conf)
        if conf >= 0:  # Tesseract uses -1 for non-text layout elements
            confidences.append(conf)
    full_text = " ".join(words)
    mean_conf = round(sum(confidences) / len(confidences), 1) if confidences else None
    return full_text, mean_conf


# PP-StructureV3 — structured document parsing
#
# Primary engine for scanned/image content; Tesseract stays wired in as the
# fallback (HAS_PADDLE False, or any exception from the calls below). This is
# the same primary-with-fallback shape extract_pdf() already uses for
# native-text-vs-OCR — one more tier of a pattern already established in this
# file, not new architecture.

_paddle_pipeline = None  # lazy singleton — built once per worker process


def _get_paddle_pipeline():
    """
    Build (or return the cached) PP-StructureV3 pipeline.

    Model tier is deliberately "mobile", not the default "server" tier —
    validated directly: the server tier's 12-model cascade needs more RAM
    than this deployment's container budget and gets OOM-killed mid-run,
    while the mobile detector/recognizer still explicitly documents
    handwriting support ("supports... handwriting, vertical text, pinyin,
    and rare characters") — this trades some cell-level table precision for
    memory headroom, not for the underlying capability itself.

    Formula recognition (LaTeX) is on — a real requirement, not every
    document has math but the ones that do need it read correctly rather
    than come back as garbled Unicode. Pinned to the "S" (small) tier for
    the same reason as the det/rec/layout models above — validated directly
    that the default "plus-L" formula model pushes a real inference run
    (not just pipeline construction) over this deployment's memory budget
    and takes the whole container host down with it, not just the request.
    Seal and chart recognition are off — this app's real document types
    (invoices, prescriptions, waybills, contracts) never carry official
    seals or charts, and loading models for inputs that never occur only
    spends memory and latency for nothing.
    """
    global _paddle_pipeline
    if _paddle_pipeline is None:
        from paddleocr import PPStructureV3

        _paddle_pipeline = PPStructureV3(
            lang="en",
            device="cpu",
            use_formula_recognition=True,
            use_seal_recognition=False,
            use_chart_recognition=False,
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            layout_detection_model_name="PP-DocLayout-S",
            formula_recognition_model_name="PP-FormulaNet_plus-S",
        )
    return _paddle_pipeline


def _paddle_extract(image_path: str) -> str | None:
    """
    Run PP-StructureV3 on a single-page image, returning Markdown (real
    tables as Markdown/HTML, LaTeX for any formulas) or None on any failure.

    Deliberately never raises — every call site treats None as "fall back
    to Tesseract", so a Paddle-specific problem (a corrupt model cache, an
    unsupported image mode, anything) degrades to the existing OCR path
    instead of failing the whole extraction.
    """
    if not PADDLE_ENABLED:
        return None
    try:
        pipeline = _get_paddle_pipeline()
        pages_md = []
        for res in pipeline.predict(image_path):
            md = res.markdown.get("markdown_texts") if hasattr(res, "markdown") else None
            if md:
                pages_md.append(md)
        text = "\n\n".join(pages_md).strip()
        return text or None
    except Exception as exc:
        logger.warning("ocr.paddle_extract_failed", error=str(exc))
        return None


def ocr_image_file(image_path: str) -> tuple[str, float | None]:
    """
    OCR a single image file. Returns (text, ocr_confidence).

    Tries PP-StructureV3 first — real table structure, LaTeX formulas,
    handwriting support the Tesseract path below has none of. No 0-100
    confidence concept applies to it (nothing comparable to Tesseract's
    per-word confidence exists in its output), so confidence comes back
    None on that path — reported honestly as "unknown", not invented.
    Falls through to Tesseract on any Paddle failure or when it isn't
    installed (native Windows dev venv — see the HAS_PADDLE import above).
    """
    paddle_text = _paddle_extract(image_path)
    if paddle_text:
        return paddle_text, None

    if not HAS_TESSERACT or not TESSERACT_BINARY_OK:
        raise RuntimeError(
            "Tesseract not available. Install tesseract-ocr system package."
        )
    if not HAS_PIL:
        raise RuntimeError("Pillow not installed.")

    img = Image.open(image_path).convert("RGB")
    processed = preprocess_image(img)

    # Try multiple PSM configs, pick the one with most text
    results = []
    for config in [TESS_CONFIG, TESS_CONFIG_SINGLE, TESS_CONFIG_SPARSE]:
        try:
            results.append(_ocr_with_confidence(processed, config, TESS_LANG))
        except Exception:
            pass

    if not results:
        raise RuntimeError("Tesseract failed on this image.")

    # Keep the result with the most text extracted (same selection rule as before)
    best_text, best_conf = max(results, key=lambda r: len(r[0].strip()))
    return best_text.strip(), best_conf


def _pdf_page_to_image(page) -> Image.Image:
    """Render a PDF page to PIL Image at 300dpi."""
    mat = fitz.Matrix(300 / 72, 300 / 72)  # 300dpi
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return img


def extract_pdf(pdf_path: str) -> tuple[str, int, float | None]:
    """
    Smart PDF extraction:
    - Try native text first (fast) — no OCR confidence concept applies; it's exact
    - If page has <MIN_CHARS_PER_PAGE → render page and OCR it (scanned PDF)
    Returns (full_text, page_count, ocr_confidence).
    ocr_confidence is the mean Tesseract confidence across only the pages
    that were actually OCR'd (None if every page was native text, since
    there's nothing for an OCR confidence to describe in that case).
    """
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed. Run: pip install PyMuPDF")

    doc = fitz.open(pdf_path)
    page_texts = []
    ocr_pages = 0
    page_confidences: list[float] = []

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        native_text = page.get_text().strip()

        if len(native_text) >= MIN_CHARS_PER_PAGE:
            # Good native text
            page_texts.append(unidecode(native_text))
        elif PADDLE_ENABLED or (HAS_TESSERACT and TESSERACT_BINARY_OK):
            # Scanned page — render, then try PaddleOCR before Tesseract.
            img = _pdf_page_to_image(page)

            paddle_md = None
            if PADDLE_ENABLED:
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp_path = tmp.name
                    img.save(tmp_path)
                    paddle_md = _paddle_extract(tmp_path)
                finally:
                    if tmp_path:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

            if paddle_md:
                page_texts.append(paddle_md)
                ocr_pages += 1
                # No 0-100 confidence concept applies to Paddle's output —
                # page_confidences (Tesseract-specific) stays untouched, so
                # the aggregate below only ever averages genuine Tesseract
                # scores rather than mixing in a number that means nothing.
            elif HAS_TESSERACT and TESSERACT_BINARY_OK:
                processed = preprocess_image(img)
                try:
                    ocr_text, page_conf = _ocr_with_confidence(processed, TESS_CONFIG, TESS_LANG)
                    page_texts.append(ocr_text.strip())
                    ocr_pages += 1
                    if page_conf is not None:
                        page_confidences.append(page_conf)
                except Exception as e:
                    raise RuntimeError(f"Page {page_num + 1} OCR failed: {e}") from e
            else:
                # Paddle ran but returned nothing usable, and there's no
                # Tesseract to fall back to.
                raise RuntimeError(f"Page {page_num + 1}: PaddleOCR returned no text and no fallback OCR engine is available.")
        else:
            page_texts.append(
                native_text or f"[Page {page_num + 1}: no text, no OCR engine available]"
            )

    doc.close()
    full_text = "\n\n--- Page Break ---\n\n".join(t for t in page_texts if t)
    note = f"\n\n[{ocr_pages} page(s) processed via OCR]" if ocr_pages else ""
    ocr_confidence = round(sum(page_confidences) / len(page_confidences), 1) if page_confidences else None
    return full_text + note, len(page_texts), ocr_confidence


def extract_pdf_sections(pdf_path: str) -> list[dict]:
    """Extract heading/content pairs using font analysis."""
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed.")

    doc = fitz.open(pdf_path)
    rows = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = unidecode(span["text"]).strip()
                    if text:
                        rows.append(
                            {
                                "text": text,
                                "size": span["size"],
                                "bold": "bold" in span["font"].lower(),
                            }
                        )
    doc.close()

    if not rows:
        return [{"heading": "Full Text", "content": "No text found"}]

    sizes = [r["size"] for r in rows]
    body_size = max(set(sizes), key=sizes.count)
    sections, current_heading, current_body = [], "Document", []

    for row in rows:
        is_heading = row["size"] > body_size + 1.5 or (
            row["bold"] and row["size"] >= body_size + 0.5
        )
        if is_heading:
            if current_body:
                sections.append(
                    {"heading": current_heading, "content": " ".join(current_body)}
                )
            current_heading = row["text"]
            current_body = []
        else:
            current_body.append(row["text"])

    if current_body:
        sections.append({"heading": current_heading, "content": " ".join(current_body)})

    return sections or [
        {"heading": "Full Text", "content": " ".join(r["text"] for r in rows)}
    ]


def sections_to_word(sections: list[dict], output_path: str) -> str:
    if not HAS_DOCX:
        raise RuntimeError("python-docx not installed.")
    doc = DocxDocument()
    for sec in sections:
        doc.add_heading(sec["heading"], level=1)
        p = doc.add_paragraph(sec["content"])
        p.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        doc.add_paragraph("")
    doc.save(output_path)
    return output_path


def text_to_docx_bytes(text: str) -> bytes:
    """Plain text (blank-line-separated paragraphs) -> .docx bytes, in memory."""
    if not HAS_DOCX:
        raise RuntimeError("python-docx not installed.")
    import io

    doc = DocxDocument()
    for para in text.split("\n\n"):
        para = para.strip()
        if para:
            doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def text_to_pdf_bytes(text: str) -> bytes:
    """Plain text (blank-line-separated paragraphs) -> .pdf bytes, in memory."""
    import io

    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=1 * inch, rightMargin=1 * inch,
        topMargin=1 * inch, bottomMargin=1 * inch,
    )
    style = getSampleStyleSheet()["BodyText"]
    story = []
    for para in text.split("\n\n"):
        para = para.strip()
        if para:
            # reportlab's Paragraph markup treats bare & < > as XML — escape
            # them or a summary containing e.g. "A & B" silently truncates.
            escaped = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(escaped, style))
            story.append(Spacer(1, 12))
    doc.build(story)
    return buf.getvalue()


def summarize_text(text: str, ratio: float = 0.3) -> str:
    """Extractive summary by sentence TF scoring."""
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", text) if len(s.strip()) > 20]
    if not sentences:
        return text[:3000]
    words = re.findall(r"\w+", text.lower())
    freq: dict = {}
    for w in words:
        if len(w) > 3:
            freq[w] = freq.get(w, 0) + 1
    scored = sorted(
        [
            (sum(freq.get(w.lower(), 0) for w in re.findall(r"\w+", s)), s)
            for s in sentences
        ],
        key=lambda x: -x[0],
    )
    keep = max(3, int(len(scored) * ratio))
    return ". ".join(s for _, s in scored[:keep]) + "."


def answer_question(question: str, text: str) -> str:
    """Keyword-match sentence retrieval."""
    keywords = [w for w in re.findall(r"\w+", question.lower()) if len(w) > 3]
    if not keywords:
        return "Please ask a more specific question."
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", text) if len(s.strip()) > 15]
    scored = sorted(
        [
            (sum(1 for kw in keywords if kw in s.lower()), s)
            for s in sentences
            if any(kw in s.lower() for kw in keywords)
        ],
        key=lambda x: -x[0],
    )
    return (
        " ".join(s for _, s in scored[:5])
        if scored
        else "No relevant information found."
    )


# Main dispatcher
def process_job(job_type: str, file_path: str, extra: dict = None) -> dict:
    """
    Run OCR job synchronously (called in thread executor from async context).
    Always returns a result dict — never raises.
    """
    start = time.time()
    extra = extra or {}
    result = {
        "text": None,
        "file_path": None,
        "error": None,
        "page_count": None,
        "processing_time_ms": 0,
        # Real Tesseract engine confidence (mean per-word, 0-100) — distinct
        # from AgentRun.confidence_score, which is the LLM's own self-reported
        # guess about its structured-field extraction. This one is grounded
        # in what the OCR engine actually measured. None for job types that
        # never touch Tesseract (native-text PDFs, non-OCR conversions).
        "ocr_confidence": None,
    }

    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found on disk: {file_path}")

        if job_type == "ocr_image":
            text, confidence = ocr_image_file(file_path)
            result["text"] = text
            result["ocr_confidence"] = confidence

        elif job_type == "pdf_extract":
            text, pages, confidence = extract_pdf(file_path)
            result["text"] = text or "(No text extracted)"
            result["page_count"] = pages
            result["ocr_confidence"] = confidence

        elif job_type == "pdf_summarize":
            text, pages, confidence = extract_pdf(file_path)
            result["text"] = summarize_text(text, float(extra.get("ratio", 0.3)))
            result["page_count"] = pages
            result["ocr_confidence"] = confidence

        elif job_type == "pdf_to_word":
            sections = extract_pdf_sections(file_path)
            out_path = file_path.rsplit(".", 1)[0] + "_extracted.docx"
            sections_to_word(sections, out_path)
            result["file_path"] = out_path
            result["text"] = f"Extracted {len(sections)} section(s)"
            result["page_count"] = len(sections)

        elif job_type == "pdf_qa":
            text, pages, confidence = extract_pdf(file_path)
            result["text"] = answer_question(extra.get("question", ""), text)
            result["ocr_confidence"] = confidence
            result["page_count"] = pages

        elif job_type == "pdf_to_markdown":
            md_text, pages = pdf_to_markdown(file_path)
            out_path = file_path.rsplit(".", 1)[0] + ".md"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(md_text)
            result["text"] = md_text[:2000] + ("..." if len(md_text) > 2000 else "")
            result["file_path"] = out_path
            result["page_count"] = pages

        elif job_type == "pdf_merge":
            # extra["input_paths"] — list of additional local PDF paths to merge
            # The primary file_path is merged first
            input_paths = [file_path] + (extra.get("input_paths") or [])
            out_path = file_path.rsplit(".", 1)[0] + "_merged.pdf"
            _, total_pages = pdf_merge(input_paths, out_path)
            result["file_path"] = out_path
            result["text"] = (
                f"Merged {len(input_paths)} PDF(s) into {total_pages} pages"
            )
            result["page_count"] = total_pages

        elif job_type == "pdf_split":
            from_page = int(extra.get("from_page", 1))
            to_page = int(extra.get("to_page", 1))
            out_path = file_path.rsplit(".", 1)[0] + f"_pages_{from_page}-{to_page}.pdf"
            _, page_count = pdf_split(file_path, from_page, to_page, out_path)
            result["file_path"] = out_path
            result["text"] = (
                f"Extracted pages {from_page}–{to_page} ({page_count} page(s))"
            )
            result["page_count"] = page_count

        elif job_type == "pdf_compress":
            out_path = file_path.rsplit(".", 1)[0] + "_compressed.pdf"
            _, reduction_pct = pdf_compress(file_path, out_path)
            result["file_path"] = out_path
            result["text"] = f"Compressed PDF — {reduction_pct}% size reduction"
            result["page_count"] = 1

        elif job_type == "images_to_pdf":
            # extra["image_paths"] — additional image paths to include after file_path
            image_paths = [file_path] + (extra.get("image_paths") or [])
            out_path = file_path.rsplit(".", 1)[0] + "_combined.pdf"
            _, page_count = images_to_pdf(image_paths, out_path)
            result["file_path"] = out_path
            result["text"] = f"Combined {page_count} image(s) into PDF"
            result["page_count"] = page_count

        elif job_type == "image_to_pdf":
            out_path = file_path.rsplit(".", 1)[0] + "_converted.pdf"
            _, page_count = images_to_pdf([file_path], out_path)
            result["file_path"] = out_path
            result["text"] = "Converted image to PDF"
            result["page_count"] = page_count

        else:
            raise ValueError(f"Unknown job type: {job_type}")

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    result["processing_time_ms"] = int((time.time() - start) * 1000)
    return result


# Document Studio handlers
def pdf_to_markdown(pdf_path: str) -> tuple[str, int]:
    """
    Convert a PDF to Markdown format.
    Uses PyMuPDF's markdown output (available in PyMuPDF >= 1.24.0).
    Falls back to plain text with heading detection for older versions.
    """
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed.")

    doc = fitz.open(pdf_path)
    pages_md = []

    for i, page in enumerate(doc):
        # Try native markdown output first (PyMuPDF 1.24+)
        try:
            md = page.get_text("markdown")
            if md and md.strip():
                pages_md.append(md.strip())
                continue
        except Exception:
            pass

        # Fallback: simulate markdown from block structure
        lines = []
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = unidecode(span["text"]).strip()
                    if not text:
                        continue
                    size = span["size"]
                    bold = "bold" in span["font"].lower()
                    if size >= 18:
                        lines.append(f"\n# {text}")
                    elif size >= 14 or (bold and size >= 12):
                        lines.append(f"\n## {text}")
                    elif bold:
                        lines.append(f"\n**{text}**")
                    else:
                        lines.append(text)
        pages_md.append("\n".join(lines))

    doc.close()
    full_md = "\n\n---\n\n".join(
        f"<!-- Page {i+1} -->\n{md}" for i, md in enumerate(pages_md) if md.strip()
    )
    return full_md, len(pages_md)


def pdf_merge(input_paths: list[str], output_path: str) -> tuple[str, int]:
    """
    Merge multiple PDFs into a single output file.
    input_paths — list of local PDF file paths in desired order.
    Returns (output_path, total_page_count).
    """
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed.")
    if len(input_paths) < 2:
        raise ValueError("At least two PDFs required for merge.")

    merged = fitz.open()
    total_pages = 0

    for path in input_paths:
        src = fitz.open(path)
        merged.insert_pdf(src)
        total_pages += len(src)
        src.close()

    merged.save(output_path, deflate=True, garbage=3)
    merged.close()
    return output_path, total_pages


def pdf_split(
    pdf_path: str, from_page: int, to_page: int, output_path: str
) -> tuple[str, int]:
    """
    Extract a page range from a PDF (1-indexed, inclusive).
    Returns (output_path, page_count).
    """
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed.")

    src = fitz.open(pdf_path)
    total = len(src)

    from_page = max(1, min(from_page, total))
    to_page = max(from_page, min(to_page, total))

    out = fitz.open()
    out.insert_pdf(src, from_page=from_page - 1, to_page=to_page - 1)  # 0-indexed
    out.save(output_path, deflate=True, garbage=3)
    out.close()
    src.close()

    page_count = to_page - from_page + 1
    return output_path, page_count


def pdf_compress(pdf_path: str, output_path: str) -> tuple[str, int]:
    """
    Reduce PDF file size using PyMuPDF's garbage collection + deflate compression.
    garbage=4 removes redundant objects; deflate=True uses zlib on streams.
    """
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed.")

    doc = fitz.open(pdf_path)
    doc.save(
        output_path,
        garbage=4,  # aggressive cross-reference + object deduplication
        deflate=True,  # compress all streams
        clean=True,  # clean content streams
        deflate_images=True,
        deflate_fonts=True,
    )
    doc.close()

    original_size = os.path.getsize(pdf_path)
    compressed_size = os.path.getsize(output_path)
    reduction_pct = (
        round((1 - compressed_size / original_size) * 100, 1) if original_size else 0
    )

    return output_path, reduction_pct  # returns output_path + reduction %


def images_to_pdf(image_paths: list[str], output_path: str) -> tuple[str, int]:
    """
    Combine one or more images into a single PDF.
    Each image becomes one page.
    Returns (output_path, page_count).

    Decodes via Pillow, not fitz.open(path) directly — verified live that
    PyMuPDF's own image loader has no WEBP support at all (fails with
    FileDataError even on a WEBP fitz itself never touched otherwise), and
    HEIC only works anywhere in this app because pillow_heif patches PIL's
    opener, not fitz's. Pillow covers every format this app already accepts
    (JPEG/PNG/TIFF/BMP/WEBP/HEIC — see check_dependencies), so routing every
    image through it first and only handing fitz a real PDF to assemble is
    the one path that actually works for all of them, not just some.
    """
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF not installed.")
    if not HAS_PIL:
        raise RuntimeError("Pillow not installed.")
    if not image_paths:
        raise ValueError("No images provided.")

    import io

    doc = fitz.open()

    for img_path in image_paths:
        with Image.open(img_path) as im:
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="PDF")
            pdf_bytes = buf.getvalue()

        img_pdf = fitz.open("pdf", pdf_bytes)
        doc.insert_pdf(img_pdf)
        img_pdf.close()

    doc.save(output_path, deflate=True)
    doc.close()
    return output_path, len(image_paths)
