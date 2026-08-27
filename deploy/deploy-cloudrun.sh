#!/usr/bin/env bash
#
# Deploy Nexus Arcade to Cloud Run from source (Cloud Build does the image).
#
#   ./deploy/deploy-cloudrun.sh
#
# Configure with environment variables:
#   PROJECT_ID    GCP project            (default: gcloud's current project)
#   REGION        Cloud Run region       (default: us-central1)
#   SERVICE       Service name           (default: nexus-arcade)
#   LLM_PROVIDER  vertex | gemini | anthropic | openai   (default: vertex)
#   LLM_MODEL     model id               (default: gemini-2.5-flash)
#   GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY  (if not using vertex)
#
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_ID=${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}
REGION=${REGION:-us-central1}
SERVICE=${SERVICE:-nexus-arcade}
LLM_PROVIDER=${LLM_PROVIDER:-vertex}
LLM_MODEL=${LLM_MODEL:-gemini-2.5-flash}

if [ -z "${PROJECT_ID}" ]; then
  echo "No project set. Run: gcloud config set project YOUR_PROJECT" >&2
  exit 1
fi

echo "→ project=${PROJECT_ID} region=${REGION} service=${SERVICE} provider=${LLM_PROVIDER}"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  --project "${PROJECT_ID}" >/dev/null

# Player tokens are HMAC-signed with APP_SECRET. Generating a fresh one on every
# deploy would log every player out, so it is stored in Secret Manager once.
SECRET_NAME=${SECRET_NAME:-nexus-arcade-app-secret}
if [ "${USE_SECRET_MANAGER:-1}" = "1" ]; then
  gcloud services enable secretmanager.googleapis.com --project "${PROJECT_ID}" >/dev/null
  if ! gcloud secrets describe "${SECRET_NAME}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "→ creating secret ${SECRET_NAME}"
    python3 -c "import secrets;print(secrets.token_hex(32))" |
      gcloud secrets create "${SECRET_NAME}" --data-file=- --project "${PROJECT_ID}" >/dev/null
  fi
  SECRET_FLAGS=(--set-secrets "APP_SECRET=${SECRET_NAME}:latest")
else
  SECRET_FLAGS=(--set-env-vars "APP_SECRET=${APP_SECRET:?set APP_SECRET or USE_SECRET_MANAGER=1}")
fi

ENV_VARS="LLM_PROVIDER=${LLM_PROVIDER},LLM_MODEL=${LLM_MODEL},DB_PATH=/tmp/arcade.db"
case "${LLM_PROVIDER}" in
  vertex)
    ENV_VARS="${ENV_VARS},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},VERTEX_LOCATION=${VERTEX_LOCATION:-global}"
    gcloud services enable aiplatform.googleapis.com --project "${PROJECT_ID}" >/dev/null
    echo "→ vertex: the service account needs roles/aiplatform.user"
    ;;
  gemini)    ENV_VARS="${ENV_VARS},GEMINI_API_KEY=${GEMINI_API_KEY:?set GEMINI_API_KEY}" ;;
  anthropic) ENV_VARS="${ENV_VARS},ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}" ;;
  openai)    ENV_VARS="${ENV_VARS},OPENAI_API_KEY=${OPENAI_API_KEY:?set OPENAI_API_KEY},OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1}" ;;
esac

# --max-instances=1 keeps every player on one SQLite file, so the leaderboard is
# shared and streaks survive. Raise it only after swapping server/store.py for a
# real database (see README → Scaling).
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --min-instances "${MIN_INSTANCES:-0}" \
  --max-instances "${MAX_INSTANCES:-1}" \
  --concurrency "${CONCURRENCY:-40}" \
  --cpu 1 --memory 512Mi \
  --timeout 300 \
  --set-env-vars "${ENV_VARS}" \
  "${SECRET_FLAGS[@]}"

URL=$(gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format 'value(status.url)')
echo
echo "→ live: ${URL}"
echo "→ health: ${URL}/api/health"
