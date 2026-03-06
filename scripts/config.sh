#!/usr/bin/env bash
# =============================================================================
# config.sh — auto-discovers all Azure resource values from the active
#             az login session. No hardcoded IDs.
#
# Source this file at the top of every script:
#   source "$(dirname "$0")/config.sh"
#
# Override any value by setting it before sourcing:
#   RESOURCE_GROUP=my-rg source scripts/config.sh
#
# How discovery works:
#   Each variable is resolved in order:
#     1. Already set in environment (user override)
#     2. Queried from Azure CLI
#     3. Derived from other discovered values
#   If a required value cannot be found the script exits with a clear message.
# =============================================================================
set -euo pipefail

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[$(date +%H:%M:%S)] $*"; }
ok()   { echo "[$(date +%H:%M:%S)] ✓ $*"; }
fail() { echo "[$(date +%H:%M:%S)] ✗ $*" >&2; exit 1; }

# Run an az query and fail with a friendly message if empty
_az_or_fail() {
  local label="$1"; shift
  local result
  result=$(eval "$@" 2>/dev/null | tr -d '[:space:]')
  [[ -n "$result" ]] || fail "Could not discover ${label}. Check az login and resource group."
  echo "$result"
}

# Run an az query, return empty string if nothing found (non-fatal)
_az_optional() {
  eval "$@" 2>/dev/null | tr -d '[:space:]' || true
}

require_az_login() {
  az account show -o none 2>/dev/null || fail "Not logged in. Run: az login"
}

foundry_token() {
  az account get-access-token --resource "https://ai.azure.com/" --query "accessToken" -o tsv
}

# ── Verify login ──────────────────────────────────────────────────────────────
require_az_login

# ── Azure identity ────────────────────────────────────────────────────────────
export SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(
  _az_or_fail "subscription ID" \
    "az account show --query id -o tsv"
)}"

export TENANT_ID="${TENANT_ID:-$(
  _az_or_fail "tenant ID" \
    "az account show --query tenantId -o tsv"
)}"

# ── Resource group ────────────────────────────────────────────────────────────
# Auto-detect: find the resource group that contains a Container App
# or an AI Foundry hub (CognitiveServices/AIServices account).
# If multiple match, pick the first one alphabetically.
if [[ -z "${RESOURCE_GROUP:-}" ]]; then
  # Try: resource group containing a Container App
  RESOURCE_GROUP=$(
    az containerapp list --query "[0].resourceGroup" -o tsv 2>/dev/null | tr -d '[:space:]'
  )
fi
if [[ -z "${RESOURCE_GROUP:-}" ]]; then
  # Try: resource group containing an AI Foundry hub
  RESOURCE_GROUP=$(
    az cognitiveservices account list \
      --query "[?kind=='AIServices' || kind=='CognitiveServices'].resourceGroup | [0]" \
      -o tsv 2>/dev/null | tr -d '[:space:]'
  )
fi
[[ -n "${RESOURCE_GROUP:-}" ]] || fail "Could not discover RESOURCE_GROUP. Set it manually: export RESOURCE_GROUP=<name>"
export RESOURCE_GROUP

export LOCATION="${LOCATION:-$(
  _az_or_fail "location" \
    "az group show -n '$RESOURCE_GROUP' --query location -o tsv"
)}"

# ── Azure Container Registry ──────────────────────────────────────────────────
export ACR_NAME="${ACR_NAME:-$(
  _az_or_fail "ACR name" \
    "az acr list -g '$RESOURCE_GROUP' --query '[0].name' -o tsv"
)}"

export ACR_SERVER="${ACR_SERVER:-$(
  _az_or_fail "ACR login server" \
    "az acr show -n '$ACR_NAME' -g '$RESOURCE_GROUP' --query loginServer -o tsv"
)}"

export IMAGE_NAME="${IMAGE_NAME:-langgraph-api}"
export IMAGE_TAG="${IMAGE_TAG:-latest}"

# ── Azure Container Apps ──────────────────────────────────────────────────────
export CAE_NAME="${CAE_NAME:-$(
  _az_or_fail "Container Apps environment name" \
    "az containerapp env list -g '$RESOURCE_GROUP' --query '[0].name' -o tsv"
)}"

# Main app — the Container App that runs the LangGraph API (not redis)
export APP_NAME="${APP_NAME:-$(
  _az_or_fail "Container App name" \
    "az containerapp list -g '$RESOURCE_GROUP' \
       --query \"[?properties.configuration.ingress.external==\`true\`].name | [0]\" \
       -o tsv"
)}"

export REDIS_NAME="${REDIS_NAME:-$(
  _az_optional \
    "az containerapp list -g '$RESOURCE_GROUP' \
       --query \"[?properties.configuration.ingress.external==\`false\`].name | [0]\" \
       -o tsv"
)}"
export REDIS_NAME="${REDIS_NAME:-redis}"

export APP_URL="${APP_URL:-$(
  _az_or_fail "Container App URL" \
    "az containerapp show -n '$APP_NAME' -g '$RESOURCE_GROUP' \
       --query \"'https://' + properties.configuration.ingress.fqdn\" -o tsv"
)}"

# ── Azure AI Foundry ──────────────────────────────────────────────────────────
export AI_HUB="${AI_HUB:-$(
  _az_or_fail "AI Foundry hub name" \
    "az cognitiveservices account list -g '$RESOURCE_GROUP' \
       --query \"[?kind=='AIServices'].name | [0]\" -o tsv"
)}"

export AI_PROJECT="${AI_PROJECT:-$(
  _az_or_fail "AI Foundry project name" \
    "az resource list -g '$RESOURCE_GROUP' \
       --resource-type 'Microsoft.CognitiveServices/accounts/projects' \
       --query '[0].name' -o tsv" \
  | sed 's|.*/||'   # strip "hub/project" → "project"
)}"

export AI_ENDPOINT="${AI_ENDPOINT:-$(
  _az_or_fail "AI Foundry endpoint" \
    "az cognitiveservices account show -n '$AI_HUB' -g '$RESOURCE_GROUP' \
       --query properties.endpoint -o tsv"
)}"

export AI_FOUNDRY_PROJECT_API="${AI_FOUNDRY_PROJECT_API:-$(
  # Construct from hub name and project name
  echo "https://${AI_HUB}.services.ai.azure.com/api/projects/${AI_PROJECT}"
)}"

export FOUNDRY_API_VERSION="${FOUNDRY_API_VERSION:-2025-05-15-preview}"

export MODEL_DEPLOYMENT="${MODEL_DEPLOYMENT:-$(
  _az_or_fail "model deployment name" \
    "az cognitiveservices account deployment list -n '$AI_HUB' -g '$RESOURCE_GROUP' \
       --query '[0].name' -o tsv"
)}"

export AGENT_NAME="${AGENT_NAME:-langgraph-demo-agent}"

# ── Derived ───────────────────────────────────────────────────────────────────
export CA_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.App/containerapps/${APP_NAME}"
