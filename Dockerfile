# Nexus Arcade - single container, no build step for the frontend.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    DB_PATH=/data/arcade.db

WORKDIR /app

# Dependencies first so layer caching survives source edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional provider extras. Uncomment the one you deploy with:
#   Vertex AI (GCP service-account / metadata auth):
# RUN pip install --no-cache-dir "google-auth>=2.30"
#   Anthropic:
# RUN pip install --no-cache-dir "anthropic>=0.40"

COPY server/ ./server/
COPY web/ ./web/

# SQLite lives on a writable volume; on Cloud Run this is instance-local.
RUN mkdir -p /data && \
    addgroup --system app && adduser --system --ingroup app app && \
    chown -R app:app /app /data
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','8080'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/api/health', timeout=4).status==200 else 1)"

# Cloud Run injects $PORT; the shell form expands it.
CMD exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-access-log
