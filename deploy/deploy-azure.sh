#!/usr/bin/env bash
#
# Deploy Nexus Arcade to Azure Container Apps.
#
#   ./deploy/deploy-azure.sh
#
# ACR builds the image server-side, so no local Docker is needed - it is the
# same shape as the Cloud Build path in deploy-cloudrun.sh.
#
# Idempotent: run it again to redeploy. Everything it creates is reused.
#
# Configure with environment variables:
#   SUBSCRIPTION      Azure subscription id     (default: az's current)
#   RESOURCE_GROUP    resource group            (default: llm-games-rg)
#   LOCATION          Azure region              (default: westus)
#   APP               container app name        (default: nexus-arcade)
#   ENVIRONMENT       Container Apps env        (default: games-env)
#   REGISTRY          ACR name, globally unique (default: derived, see below)
#   IMAGE             a prebuilt image to deploy instead of building one. Set
#                     this where ACR Tasks is unavailable (Azure for Students
#                     disables it); .github/workflows/build-images.yml pushes
#                     images to GHCR for exactly this purpose.
#   REGISTRY_USERNAME / REGISTRY_PASSWORD
#                     credentials for IMAGE's registry, if it is private
#   LLM_PROVIDER      azure | gemini | anthropic | openai | mock (default: azure)
#   LLM_MODEL         model id                  (default: gpt-4.1-mini)
#   AOAI_NAME         Azure OpenAI resource     (default: derived)
#   AOAI_CAPACITY     deployment TPM, thousands (default: 50)
#   TAG_OWNER         Owner tag         (default: the signed-in account's email)
#   TAG_COST_CENTER   Cost Center tag   (default: student)
#   TAG_IAC           IaC managed tag   (default: this repo)
#   AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY
#                     point at an Azure OpenAI resource you already have, and
#                     the script will not create or touch one
#   GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY  (non-azure providers)
#
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v az >/dev/null 2>&1; then
  echo "az not found. Install the Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Not logged in. Run: az login" >&2
  exit 1
fi

SUBSCRIPTION=${SUBSCRIPTION:-$(az account show --query id -o tsv)}
az account set --subscription "${SUBSCRIPTION}"

RESOURCE_GROUP=${RESOURCE_GROUP:-llm-games-rg}
LOCATION=${LOCATION:-westus}
APP=${APP:-nexus-arcade}
ENVIRONMENT=${ENVIRONMENT:-games-env}
LLM_PROVIDER=${LLM_PROVIDER:-azure}
LLM_MODEL=${LLM_MODEL:-gpt-4.1-mini}

# ACR and Azure OpenAI names are DNS labels, so they must be globally unique and
# alphanumeric. Derive a stable suffix from the subscription id so re-running
# lands on the same names instead of littering the group with new resources.
#
# The group, environment, registry and Azure OpenAI resource are deliberately
# shared with SWAY - one Container Apps environment costs less than two, and
# each app still owns its own secrets, image repository and revisions, so
# redeploying one cannot disturb the other.
SUFFIX=$(printf '%s' "${SUBSCRIPTION}" | tr -d '-' | cut -c1-10)
REGISTRY=${REGISTRY:-llmgames${SUFFIX}}
AOAI_NAME=${AOAI_NAME:-llm-games-aoai-${SUFFIX}}
AOAI_CAPACITY=${AOAI_CAPACITY:-50}
SECRET_NAME=app-secret

# Some tenants deny any resource group that arrives untagged - Northeastern's
# `nuroot-tagging-initiative` is one, and it rejects the create outright rather
# than warning. These three satisfy it; every value is overridable, and Owner
# defaults to whoever is signed in rather than being hardcoded.
TAG_OWNER=${TAG_OWNER:-$(az account show --query user.name -o tsv)}
TAG_COST_CENTER=${TAG_COST_CENTER:-student}
TAG_IAC=${TAG_IAC:-gitachi143/gametest}
TAGS=(--tags "Owner=${TAG_OWNER}" "Cost Center=${TAG_COST_CENTER}" "IaC managed=${TAG_IAC}")

# Validate the provider's credential before creating anything, so a missing key
# costs nothing to discover.
case "${LLM_PROVIDER}" in
  azure|mock) ;;
  gemini)    : "${GEMINI_API_KEY:?set GEMINI_API_KEY (https://aistudio.google.com/apikey)}" ;;
  anthropic) : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}" ;;
  openai)    : "${OPENAI_API_KEY:?set OPENAI_API_KEY}" ;;
  *) echo "Unknown LLM_PROVIDER: ${LLM_PROVIDER} (azure|gemini|anthropic|openai|mock)" >&2; exit 1 ;;
esac

# Student and MSDN subscriptions are commonly pinned to a handful of regions by
# an "Allowed resource deployment regions" policy. The denial it produces names
# no regions at all, and it only fires once the first regional resource is
# attempted - so check the policy up front and say which regions are legal.
ALLOWED=$(az policy assignment list --disable-scope-strict-match \
  --query "[].parameters.listOfAllowedLocations.value[]" -o tsv 2>/dev/null | sort -u || true)
if [ -n "${ALLOWED}" ] && ! printf '%s\n' "${ALLOWED}" | grep -qx "${LOCATION}"; then
  echo "LOCATION=${LOCATION} is blocked by policy on this subscription." >&2
  echo "Allowed regions:" >&2
  printf '  %s\n' ${ALLOWED} >&2
  exit 1
fi

echo "→ subscription=${SUBSCRIPTION}"
echo "→ group=${RESOURCE_GROUP} location=${LOCATION} app=${APP}"
echo "→ provider=${LLM_PROVIDER} model=${LLM_MODEL}"
echo "→ tags: Owner=${TAG_OWNER} Cost Center=${TAG_COST_CENTER} IaC managed=${TAG_IAC}"

# --- 1. resource providers ------------------------------------------------
# A fresh subscription has these unregistered, and the failure it produces
# otherwise names an API rather than the fix.
echo "→ registering resource providers (first run takes a minute)"
for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.OperationalInsights Microsoft.CognitiveServices; do
  state=$(az provider show --namespace "${ns}" --query registrationState -o tsv 2>/dev/null || echo NotRegistered)
  if [ "${state}" != "Registered" ]; then
    echo "   ${ns}: ${state} → registering"
    az provider register --namespace "${ns}" --wait
  fi
done

# The containerapp commands moved into the CLI core, but older installs still
# need the extension.
if ! az containerapp --help >/dev/null 2>&1; then
  echo "→ adding the containerapp CLI extension"
  az extension add --name containerapp --upgrade --only-show-errors
fi

# --- 2. resource group ----------------------------------------------------
if ! az group show --name "${RESOURCE_GROUP}" >/dev/null 2>&1; then
  echo "→ creating resource group ${RESOURCE_GROUP}"
  az group create --name "${RESOURCE_GROUP}" --location "${LOCATION}" \
    "${TAGS[@]}" --only-show-errors >/dev/null
else
  echo "→ reusing resource group ${RESOURCE_GROUP}"
fi

# --- 3. the model backend -------------------------------------------------
AOAI_ENDPOINT=${AZURE_OPENAI_ENDPOINT:-}
AOAI_KEY=${AZURE_OPENAI_API_KEY:-}
DEPLOYMENT=${AZURE_OPENAI_DEPLOYMENT:-${LLM_MODEL}}

if [ "${LLM_PROVIDER}" = "azure" ] && [ -z "${AOAI_ENDPOINT}" ]; then
  if ! az cognitiveservices account show --name "${AOAI_NAME}" \
        --resource-group "${RESOURCE_GROUP}" >/dev/null 2>&1; then
    echo "→ creating Azure OpenAI resource ${AOAI_NAME}"
    az cognitiveservices account create \
      --name "${AOAI_NAME}" --resource-group "${RESOURCE_GROUP}" \
      --location "${LOCATION}" --kind OpenAI --sku S0 \
      --custom-domain "${AOAI_NAME}" --yes "${TAGS[@]}" --only-show-errors >/dev/null
  else
    echo "→ reusing Azure OpenAI resource ${AOAI_NAME}"
  fi

  if ! az cognitiveservices account deployment show \
        --name "${AOAI_NAME}" --resource-group "${RESOURCE_GROUP}" \
        --deployment-name "${DEPLOYMENT}" >/dev/null 2>&1; then
    # Ask the resource which version it will actually serve rather than pinning
    # one here: available versions differ by region and rotate.
    VERSION=$(az cognitiveservices account list-models \
      --name "${AOAI_NAME}" --resource-group "${RESOURCE_GROUP}" \
      --query "sort_by([?name=='${LLM_MODEL}'], &version) | [-1].version" -o tsv 2>/dev/null || true)
    if [ -z "${VERSION}" ] || [ "${VERSION}" = "None" ]; then
      echo "   ${LLM_MODEL} is not offered in ${LOCATION}. Available chat models:" >&2
      az cognitiveservices account list-models \
        --name "${AOAI_NAME}" --resource-group "${RESOURCE_GROUP}" \
        --query "[?format=='OpenAI'].name" -o tsv 2>/dev/null | sort -u >&2 || true
      echo "   Pick one with LLM_MODEL=..., or try another LOCATION." >&2
      exit 1
    fi
    echo "→ deploying model ${LLM_MODEL} (version ${VERSION}, ${AOAI_CAPACITY}k TPM)"
    az cognitiveservices account deployment create \
      --name "${AOAI_NAME}" --resource-group "${RESOURCE_GROUP}" \
      --deployment-name "${DEPLOYMENT}" \
      --model-name "${LLM_MODEL}" --model-version "${VERSION}" --model-format OpenAI \
      --sku-name GlobalStandard --sku-capacity "${AOAI_CAPACITY}" --only-show-errors >/dev/null
  else
    echo "→ reusing model deployment ${DEPLOYMENT}"
  fi

  AOAI_ENDPOINT=$(az cognitiveservices account show --name "${AOAI_NAME}" \
    --resource-group "${RESOURCE_GROUP}" --query properties.endpoint -o tsv)
  AOAI_KEY=$(az cognitiveservices account keys list --name "${AOAI_NAME}" \
    --resource-group "${RESOURCE_GROUP}" --query key1 -o tsv)
fi

if [ "${LLM_PROVIDER}" = "azure" ] && [ -z "${AOAI_KEY}" ]; then
  echo "AZURE_OPENAI_ENDPOINT was set but AZURE_OPENAI_API_KEY was not." >&2
  exit 1
fi

# --- 4. the image ---------------------------------------------------------
# Preferred path is a server-side ACR build, so nothing needs Docker locally.
# Where ACR Tasks is disallowed - Azure for Students blocks it outright with
# `TasksOperationsNotAllowed` - pass IMAGE= a registry reference built
# elsewhere, and this step is skipped along with the registry it would need.
if [ -n "${IMAGE:-}" ]; then
  echo "→ using prebuilt image ${IMAGE}"
  REG_SERVER=${REGISTRY_SERVER:-${IMAGE%%/*}}
  REG_USER=${REGISTRY_USERNAME:-}
  REG_PASS=${REGISTRY_PASSWORD:-}
else
  if ! az acr show --name "${REGISTRY}" >/dev/null 2>&1; then
    echo "→ creating container registry ${REGISTRY}"
    az acr create --name "${REGISTRY}" --resource-group "${RESOURCE_GROUP}" \
      --sku Basic --admin-enabled true "${TAGS[@]}" --only-show-errors >/dev/null
  else
    echo "→ reusing container registry ${REGISTRY}"
  fi

  TAG="$(date -u +%Y%m%d%H%M%S)"
  IMAGE="${REGISTRY}.azurecr.io/${APP}:${TAG}"
  echo "→ building ${IMAGE} in ACR (first run: 3-5 minutes)"
  if ! az acr build --registry "${REGISTRY}" --image "${APP}:${TAG}" --image "${APP}:latest" \
      --file Dockerfile . --only-show-errors >/dev/null; then
    echo >&2
    echo "ACR build failed. If it said TasksOperationsNotAllowed, this" >&2
    echo "subscription cannot build server-side. Build elsewhere and pass it in:" >&2
    echo "  gh workflow run build-images.yml --ref \$(git branch --show-current)" >&2
    echo "  IMAGE=ghcr.io/<owner>/${APP}:latest REGISTRY_USERNAME=<user> \\" >&2
    echo "    REGISTRY_PASSWORD=\$(gh auth token) $0" >&2
    exit 1
  fi

  # Admin credentials rather than a managed identity: one fewer role assignment
  # to propagate before the first pull can succeed.
  REG_SERVER="${REGISTRY}.azurecr.io"
  REG_USER=$(az acr credential show --name "${REGISTRY}" --query username -o tsv)
  REG_PASS=$(az acr credential show --name "${REGISTRY}" --query 'passwords[0].value' -o tsv)
fi

# A public image needs no credentials, so only pass them when we have them.
REG_ARGS=()
if [ -n "${REG_USER}" ]; then
  REG_ARGS=(--registry-server "${REG_SERVER}"
            --registry-username "${REG_USER}" --registry-password "${REG_PASS}")
fi

# --- 5. Container Apps environment ----------------------------------------
if ! az containerapp env show --name "${ENVIRONMENT}" \
      --resource-group "${RESOURCE_GROUP}" >/dev/null 2>&1; then
  echo "→ creating Container Apps environment ${ENVIRONMENT} (2-3 minutes)"
  az containerapp env create --name "${ENVIRONMENT}" \
    --resource-group "${RESOURCE_GROUP}" --location "${LOCATION}" \
    "${TAGS[@]}" --only-show-errors >/dev/null
else
  echo "→ reusing Container Apps environment ${ENVIRONMENT}"
fi

# --- 6. APP_SECRET --------------------------------------------------------
# Player records are keyed to HMAC-signed tokens, so a fresh APP_SECRET on every
# deploy would log everyone out. Generate once, then reuse what the app holds.
APP_EXISTS=0
az containerapp show --name "${APP}" --resource-group "${RESOURCE_GROUP}" >/dev/null 2>&1 && APP_EXISTS=1

HELD=""
if [ "${APP_EXISTS}" = "1" ]; then
  HELD=$(az containerapp secret list --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
    --query "[?name=='${SECRET_NAME}'].name" -o tsv 2>/dev/null || true)
fi

if [ -n "${HELD}" ]; then
  echo "→ reusing the existing ${SECRET_NAME}"
  APP_SECRET_VALUE=""
else
  echo "→ generating ${SECRET_NAME}"
  APP_SECRET_VALUE=$(python3 -c "import secrets;print(secrets.token_hex(32))")
fi

# --- 7. provider wiring ---------------------------------------------------
# --max-replicas 1 keeps every player on one SQLite file, so the leaderboard is
# shared and streaks survive. Raise it only after swapping server/store.py for a
# networked database (see README -> "One caveat worth knowing").
ENV_VARS=("LLM_PROVIDER=${LLM_PROVIDER}" "LLM_MODEL=${LLM_MODEL}" "DB_PATH=/tmp/arcade.db")
SECRETS=()
[ -n "${APP_SECRET_VALUE}" ] && SECRETS+=("${SECRET_NAME}=${APP_SECRET_VALUE}")
ENV_VARS+=("APP_SECRET=secretref:${SECRET_NAME}")

case "${LLM_PROVIDER}" in
  azure)
    SECRETS+=("azure-openai-key=${AOAI_KEY}")
    ENV_VARS+=("AZURE_OPENAI_ENDPOINT=${AOAI_ENDPOINT}"
               "AZURE_OPENAI_DEPLOYMENT=${DEPLOYMENT}"
               "AZURE_OPENAI_API_KEY=secretref:azure-openai-key")
    ;;
  gemini)
    SECRETS+=("gemini-key=${GEMINI_API_KEY}")
    ENV_VARS+=("GEMINI_API_KEY=secretref:gemini-key")
    ;;
  anthropic)
    SECRETS+=("anthropic-key=${ANTHROPIC_API_KEY}")
    ENV_VARS+=("ANTHROPIC_API_KEY=secretref:anthropic-key")
    ;;
  openai)
    SECRETS+=("openai-key=${OPENAI_API_KEY}")
    ENV_VARS+=("OPENAI_API_KEY=secretref:openai-key"
               "OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1}")
    ;;
  mock) echo "→ DEMO MODE: deploying without a model backend" ;;
esac

# --- 8. deploy ------------------------------------------------------------
if [ "${APP_EXISTS}" = "0" ]; then
  echo "→ creating container app ${APP}"
  az containerapp create --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
    --environment "${ENVIRONMENT}" \
    --image "${IMAGE}" \
    ${REG_ARGS[@]+"${REG_ARGS[@]}"} \
    --target-port 8080 --ingress external \
    --min-replicas "${MIN_REPLICAS:-0}" --max-replicas "${MAX_REPLICAS:-1}" \
    --cpu 0.5 --memory 1.0Gi \
    ${SECRETS[@]+--secrets "${SECRETS[@]}"} \
    --env-vars "${ENV_VARS[@]}" \
    "${TAGS[@]}" \
    --only-show-errors >/dev/null
else
  echo "→ updating container app ${APP}"
  # Secrets are set first: an env var referencing one that does not exist yet
  # is rejected at revision-creation time.
  if [ ${#SECRETS[@]} -gt 0 ]; then
    az containerapp secret set --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
      --secrets "${SECRETS[@]}" --only-show-errors >/dev/null
  fi
  if [ -n "${REG_USER}" ]; then
    az containerapp registry set --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
      --server "${REG_SERVER}" \
      --username "${REG_USER}" --password "${REG_PASS}" --only-show-errors >/dev/null
  fi
  az containerapp update --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
    --image "${IMAGE}" \
    --min-replicas "${MIN_REPLICAS:-0}" --max-replicas "${MAX_REPLICAS:-1}" \
    --cpu 0.5 --memory 1.0Gi \
    --set-env-vars "${ENV_VARS[@]}" \
    --only-show-errors >/dev/null
fi

URL="https://$(az containerapp show --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
  --query properties.configuration.ingress.fqdn -o tsv)"

echo
echo "───────────────────────────────────────────────"
echo " live:    ${URL}"
echo " health:  ${URL}/api/health"
echo "───────────────────────────────────────────────"
echo
echo "Check the provider took effect:"
echo "  curl -s ${URL}/api/health | python3 -m json.tool"
echo
echo '`"demo_mode": true` means the key did not arrive and the scripted opponent'
echo "is standing in. Logs:"
echo "  az containerapp logs show -n ${APP} -g ${RESOURCE_GROUP} --tail 50"
