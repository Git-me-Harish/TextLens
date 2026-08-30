"""
011 — Real OCR-engine confidence on ocr_jobs

Previously the only "confidence" anywhere in the system was AgentRun's,
which is the LLM self-reporting a number about its own structured-field
extraction — not grounded in the OCR engine at all. Adds ocr_jobs.ocr_confidence,
populated from Tesseract's actual per-word confidence scores
(ocr_service.py's _ocr_with_confidence, via image_to_data instead of
image_to_string). Null for jobs that never touched Tesseract (native-text
PDFs, non-OCR conversions).
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ocr_jobs", sa.Column("ocr_confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("ocr_jobs", "ocr_confidence")
