FROM python:3.11-slim

# Install system deps for OCR
# Language packs here must match settings.TESSERACT_LANGUAGES (config.py) —
# add more (tesseract-ocr-tam, -tel, -kan, -mal, -ben, -mar, -guj, -pan, ...)
# and extend TESSERACT_LANGUAGES together; each unlisted language silently
# fails to load if referenced without its package installed.
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-hin \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgl1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# PaddleOCR (PP-StructureV3) — table/formula/handwriting-aware document
# parsing, the primary extraction engine for scanned pages (Tesseract stays
# wired in as the fallback, see ocr_service.py). Installed as a separate step
# because the inference engine (paddlepaddle) ships from Paddle's own index,
# not PyPI, and pillow-heif/paddleocr[doc-parser] both live in requirements.txt.
#
# Deliberately NOT installed into the native Windows dev venv (backend/.venv)
# — validated live that PaddlePaddle's native CPU inference engine segfaults
# under Windows; it is stable under Linux, which is what this container is.
# On Windows, paddleocr's absence there means the import in ocr_service.py
# fails cleanly (HAS_PADDLE=False) and the pipeline uses Tesseract instead —
# the same graceful-degradation pattern every other optional dependency in
# this file already follows, not a special case.
RUN pip install --no-cache-dir paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/

# Download spacy model
RUN python -m spacy download en_core_web_sm

COPY . .

# Bake PP-StructureV3's model weights (~12 models, det/rec/layout/table/formula)
# into the image at build time instead of leaving them to download on a
# worker's first real job. Two reasons, both verified live in this container:
#   1. PaddleX's own connectivity probe (is_available()) reports "no hoster
#      available" here even though the container has real internet access
#      (huggingface.co returns 200) — PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK
#      skips that broken probe and downloads directly instead.
#   2. Without baking, weights land in /root/.paddlex inside the container's
#      writable layer and vanish on every recreate — every fresh container
#      would re-download ~500MB+ and depend on runtime network access, the
#      same failure mode this works around at build time.
# MODEL_SOURCE pins HuggingFace as the download source (bos, Paddle's default
# CDN, returned 403 in this environment).
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
ENV PADDLE_PDX_MODEL_SOURCE=huggingface
RUN python -c "from app.services.ocr_service import _get_paddle_pipeline; _get_paddle_pipeline()"

RUN mkdir -p uploads

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
