import fitz
import pytest
from PIL import Image
from fastapi import HTTPException

from app.api.routes.jobs import _allowed, _validate_job_type
from app.models.models import JobType
from app.services import ocr_service


def test_image_to_pdf_dispatch(tmp_path):
    image_path = tmp_path / "source.png"
    Image.new("RGB", (20, 20), "white").save(image_path)

    result = ocr_service.process_job(JobType.image_to_pdf.value, str(image_path))

    output_path = tmp_path / "source_converted.pdf"
    assert result["error"] is None
    assert result["page_count"] == 1
    assert result["file_path"] == str(output_path)
    assert output_path.exists()


def test_scanned_page_ocr_failure_fails_job(tmp_path, monkeypatch):
    pdf_path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    monkeypatch.setattr(ocr_service, "HAS_TESSERACT", True)
    monkeypatch.setattr(ocr_service, "TESSERACT_BINARY_OK", True)

    def fail_ocr(*args):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(ocr_service, "_ocr_with_confidence", fail_ocr)

    result = ocr_service.process_job(JobType.pdf_extract.value, str(pdf_path))

    assert result["error"] == "RuntimeError: Page 1 OCR failed: forced failure"
    assert result["text"] is None


def test_generic_upload_rejects_studio_pdf_edit():
    with pytest.raises(HTTPException):
        _validate_job_type(JobType.pdf_edit.value)


def test_image_to_pdf_requires_image_content():
    assert _allowed("image/png", JobType.image_to_pdf.value)
    assert not _allowed("application/pdf", JobType.image_to_pdf.value)