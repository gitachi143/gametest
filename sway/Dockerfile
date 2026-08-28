# SWAY - one container. No frontend build step: the client is ES modules served
# straight from disk, so what runs in production is what you edited.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    DB_PATH=/data/sway.db

WORKDIR /srv

# Dependencies first so layer caching survives source edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# google-auth lets the Vertex provider use Application Default Credentials.
# On Cloud Run the metadata server is enough, but installing it keeps the same
# image working under Workload Identity and with a mounted key file.
RUN pip install --no-cache-dir "google-auth>=2.30,<3"

COPY app/ ./app/
COPY web/ ./web/

# SQLite lives on a writable volume; on Cloud Run that is instance-local.
RUN mkdir -p /data && \
    addgroup --system sway && adduser --system --ingroup sway sway && \
    chown -R sway:sway /srv /data
USER sway

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=8s --retries=3 \
  CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','8080'); \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/api/health', timeout=4).status==200 else 1)"

# Cloud Run injects $PORT, so the shell form is required to expand it.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-access-log
