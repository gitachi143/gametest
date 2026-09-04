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

# Optional provider extras. Azure OpenAI needs none of these - it is spoken
# over plain httpx. Uncomment the one you deploy with:
#   Vertex AI (GCP service-account / metadata auth):
# RUN pip install --no-cache-dir "google-auth>=2.30"
#   Anthropic:
# RUN pip install --no-cache-dir "anthropic>=0.40"

COPY server/ ./server/
COPY web/ ./web/

# SQLite lives on a writable volume; on Container Apps and Cloud Run alike
# this is instance-local and does not survive a new revision.
RUN mkdir -p /data && \
    addgroup --system app && adduser --system --ingroup app app && \
    chown -R app:app /app /data
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','8080'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/api/health', timeout=4).status==200 else 1)"

# Container Apps and Cloud Run both address the port they are told; the shell
# form expands $PORT, which defaults to 8080 above.
CMD exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-access-log
