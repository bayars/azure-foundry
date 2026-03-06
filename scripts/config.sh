#!/usr/bin/env bash
# =============================================================================
# config.sh — shared configuration for all deployment scripts
#
# Source this file at the top of every script:
#   source "$(dirname "$0")/config.sh"
# =============================================================================

# ── Azure identity ────────────────────────────────────────────────────────────
export SUBSCRIPTION_ID="5ec3a6f9-978c-4e02-9d96-135dbc85269e"
export TENANT_ID="b2a97c18-26c3-4214-bc2c-50dda9403081"
export RESOURCE_GROUP="rg-bayarsafa-7080"
export LOCATION="eastus2"

# ── Azure Container Registry ──────────────────────────────────────────────────
export ACR_NAME="safademo"
export ACR_SERVER="safademo.azurecr.io"
export IMAGE_NAME="langgraph-api"
export IMAGE_TAG="${IMAGE_TAG:-latest}"                 # override with: IMAGE_TAG=20260305 ./02-build-push.sh

# ── Azure Container Apps ──────────────────────────────────────────────────────
export CAE_NAME="cae-langgraph"
export APP_NAME="langgraph-api"
export REDIS_NAME="redis"
export APP_URL="https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io"

# ── Azure AI Foundry ──────────────────────────────────────────────────────────
export AI_HUB="safabayar"
export AI_PROJECT="proj-default"
export AI_ENDPOINT="https://safabayar.cognitiveservices.azure.com/"
export AI_FOUNDRY_PROJECT_API="https://safabayar.services.ai.azure.com/api/projects/proj-default"
export FOUNDRY_API_VERSION="2025-05-15-preview"
export MODEL_DEPLOYMENT="gpt-4o-mini"
export AGENT_NAME="langgraph-demo-agent"

# ── Container App resource ID (used for container_app agent registration) ─────
export CA_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.App/containerapps/${APP_NAME}"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[$(date +%H:%M:%S)] $*"; }
ok()   { echo "[$(date +%H:%M:%S)] ✓ $*"; }
fail() { echo "[$(date +%H:%M:%S)] ✗ $*" >&2; exit 1; }

require_az_login() {
  az account show -o none 2>/dev/null || fail "Not logged in. Run: az login"
}

foundry_token() {
  az account get-access-token --resource "https://ai.azure.com/" --query "accessToken" -o tsv
}
