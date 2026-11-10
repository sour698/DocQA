FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MODEL_CACHE_DIR=/opt/models

# OCR for scanned PDFs (small; optional but included)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-api.txt .
RUN pip install -r requirements-api.txt

# Bake the ONNX models into the image so the first request is not slow and never needs the network.
RUN python -c "import os; \
from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; \
d=os.environ['MODEL_CACHE_DIR']; \
TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir=d); \
TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2', cache_dir=d)" \
 && chmod -R a+rX /opt/models

# Unprivileged user. The container keeps NO state: everything lives in PostgreSQL (DATABASE_URL).
RUN useradd --create-home --uid 1000 app
COPY --chown=app:app . .
USER app

# Render sets PORT itself (10000 by default). One worker: each worker holds its own copy of the models.
ENV PORT=10000 \
    TRUST_PROXY=1 \
    WEB_CONCURRENCY=1
EXPOSE 10000

# /health is liveness only (does not touch the database), so a sleeping serverless DB never kills the API.
HEALTHCHECK --interval=30s --timeout=6s --start-period=120s --retries=3 \
  CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '10000'), timeout=4)"

CMD ["bash", "start.sh"]
