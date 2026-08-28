#!/usr/bin/env bash
#
# Deploy SWAY to Cloud Run from source (Cloud Build builds the image).
#
#   ./sway/deploy.sh
#
# Idempotent: run it again to redeploy. Everything it creates is reused. This is
# a separate service from anything else in the repo - it shares only the project.
#
# Configure with environment variables:
#   PROJECT_ID       GCP project          (default: gcloud's current project)
#   REGION           Cloud Run region     (default: us-central1)
#   SERVICE          service name         (default: sway)
#   LLM_PROVIDER     vertex | gemini | anthropic | openai | mock  (default: vertex)
#   LLM_MODEL        model id             (default: gemini-2.5-flash)
#   VERTEX_LOCATION  Vertex region        (default: global)
#   SERVICE_ACCOUNT  runtime SA email     (default: the compute default SA)
#   GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY  (non-vertex providers)
#   SKIP_IAM=1       don't touch IAM (when a platform team owns bindings)
#
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud not found. Install it: https://cloud.google.com/sdk/docs/install" >&2
  exit 1
fi

PROJECT_ID=${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}
# `gcloud config get-value` prints "(unset)" rather than nothing on some versions.
if [ -z "${PROJECT_ID}" ] || [ "${PROJECT_ID}" = "(unset)" ]; then
  echo "No project set. Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

REGION=${REGION:-us-central1}
SERVICE=${SERVICE:-sway}
LLM_PROVIDER=${LLM_PROVIDER:-vertex}
LLM_MODEL=${LLM_MODEL:-gemini-2.5-flash}
SECRET_NAME=${SECRET_NAME:-sway-app-secret}

# Validate the credential before creating anything, so a missing key is free to
# discover.
case "${LLM_PROVIDER}" in
  vertex|mock) ;;
  gemini)    : "${GEMINI_API_KEY:?set GEMINI_API_KEY (https://aistudio.google.com/apikey)}" ;;
  anthropic) : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}" ;;
  openai)    : "${OPENAI_API_KEY:?set OPENAI_API_KEY}" ;;
  *) echo "Unknown LLM_PROVIDER: ${LLM_PROVIDER}" >&2; exit 1 ;;
esac

echo "→ project=${PROJECT_ID} region=${REGION} service=${SERVICE}"
echo "→ provider=${LLM_PROVIDER} model=${LLM_MODEL}"

# --- 1. APIs ---------------------------------------------------------------
# artifactregistry is needed because --source pushes the built image there.
APIS="run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com"
[ "${USE_SECRET_MANAGER:-1}" = "1" ] && APIS="${APIS} secretmanager.googleapis.com"
[ "${LLM_PROVIDER}" = "vertex" ] && APIS="${APIS} aiplatform.googleapis.com"
echo "→ enabling APIs (first run takes a minute)"
# shellcheck disable=SC2086
gcloud services enable ${APIS} --project "${PROJECT_ID}" >/dev/null

# --- 2. runtime service account -------------------------------------------
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format 'value(projectNumber)')
RUNTIME_SA=${SERVICE_ACCOUNT:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}
echo "→ runtime service account: ${RUNTIME_SA}"

# --- 3. APP_SECRET ---------------------------------------------------------
# Players are anonymous but persistent: their identity is an HMAC-signed token.
# A fresh secret on every deploy would wipe every profile, so it is generated
# once and kept in Secret Manager.
if [ "${USE_SECRET_MANAGER:-1}" = "1" ]; then
  if ! gcloud secrets describe "${SECRET_NAME}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "→ creating secret ${SECRET_NAME}"
    APP_SECRET_VALUE=$(python3 -c "import secrets;print(secrets.token_hex(32))")
    printf '%s' "${APP_SECRET_VALUE}" |
      gcloud secrets create "${SECRET_NAME}" --data-file=- --project "${PROJECT_ID}" >/dev/null
  else
    echo "→ reusing secret ${SECRET_NAME}"
  fi
  if [ "${SKIP_IAM:-0}" != "1" ]; then
    # Without this the deploy itself fails: Cloud Run validates secret access.
    gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
      --member "serviceAccount:${RUNTIME_SA}" \
      --role roles/secretmanager.secretAccessor \
      --project "${PROJECT_ID}" --quiet >/dev/null
  fi
  SECRET_FLAGS=(--set-secrets "APP_SECRET=${SECRET_NAME}:latest")
else
  SECRET_FLAGS=(--set-env-vars "APP_SECRET=${APP_SECRET:?set APP_SECRET or USE_SECRET_MANAGER=1}")
fi

# --- 4. provider wiring ----------------------------------------------------
ENV_VARS="LLM_PROVIDER=${LLM_PROVIDER},LLM_MODEL=${LLM_MODEL},DB_PATH=/tmp/sway.db"
ENV_VARS="${ENV_VARS},LLM_CONCURRENCY=${LLM_CONCURRENCY:-3}"
case "${LLM_PROVIDER}" in
  vertex)
    ENV_VARS="${ENV_VARS},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
    ENV_VARS="${ENV_VARS},VERTEX_LOCATION=${VERTEX_LOCATION:-global}"
    # No API key anywhere: the runtime SA calls Vertex AI directly.
    if [ "${SKIP_IAM:-0}" != "1" ]; then
      echo "→ granting roles/aiplatform.user to the runtime service account"
      gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member "serviceAccount:${RUNTIME_SA}" --role roles/aiplatform.user \
        --condition=None --quiet >/dev/null
    fi
    ;;
  gemini)    ENV_VARS="${ENV_VARS},GEMINI_API_KEY=${GEMINI_API_KEY}" ;;
  anthropic) ENV_VARS="${ENV_VARS},ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" ;;
  openai)    ENV_VARS="${ENV_VARS},OPENAI_API_KEY=${OPENAI_API_KEY},OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1}" ;;
  mock)      echo "→ DEMO MODE: deploying without a model backend" ;;
esac

# --- 5. deploy -------------------------------------------------------------
# --max-instances=1 keeps every player on one SQLite file, so the leaderboard is
# shared. Raise it only after replacing app/store.py with a networked database.
echo "→ building and deploying (first run: 3-5 minutes)"
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${RUNTIME_SA}" \
  --allow-unauthenticated \
  --min-instances "${MIN_INSTANCES:-0}" \
  --max-instances "${MAX_INSTANCES:-1}" \
  --concurrency "${CONCURRENCY:-40}" \
  --cpu 1 --memory 512Mi \
  --timeout 300 \
  --set-env-vars "${ENV_VARS}" \
  "${SECRET_FLAGS[@]}" \
  --quiet

URL=$(gcloud run services describe "${SERVICE}" \
  --project "${PROJECT_ID}" --region "${REGION}" --format 'value(status.url)')

echo
echo "──────────────────────────────────────────────"
echo " live:    ${URL}"
echo " health:  ${URL}/api/health"
echo "──────────────────────────────────────────────"
echo
echo "Check the provider took effect:"
echo "  curl -s ${URL}/api/health | python3 -m json.tool"
echo
echo '`"demo_mode": true` means the credential did not arrive and the scripted'
echo "opponent is standing in. Logs:"
echo "  gcloud run services logs read ${SERVICE} --region ${REGION} --limit 50"
