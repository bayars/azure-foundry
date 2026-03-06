#!/usr/bin/env bash
# =============================================================================
# 04-deploy-apps.sh — Deploy langgraph-api and redis to Container Apps
#
# What it does:
#   1. Reads ACR credentials and Azure OpenAI key from az CLI
#   2. Creates (or updates) the langgraph-api Container App
#      - External ingress on port 8000
#      - Azure OpenAI env vars injected (API key stored as a secret)
#      - 1–3 replicas, 1 CPU / 2 GB RAM
#   3. Creates (or updates) the redis Container App
#      - Internal ingress only (not reachable from internet)
#      - 1 replica, 0.25 CPU / 0.5 GB RAM
#
# The AZURE_OPENAI_API_KEY is stored as a Container App secret (not plain env var).
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/config.sh"

require_az_login

# ── Fetch credentials ─────────────────────────────────────────────────────────
log "Reading ACR credentials..."
ACR_USER=$(az acr credential show -n "$ACR_NAME" --query "username" -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR_NAME" --query "passwords[0].value" -o tsv)

log "Reading Azure OpenAI key..."
AOAI_KEY=$(az cognitiveservices account keys list \
  -n "$AI_HUB" -g "$RESOURCE_GROUP" --query "key1" -o tsv)

# ── Deploy langgraph-api ──────────────────────────────────────────────────────
log "Deploying ${APP_NAME}..."

if az containerapp show -n "$APP_NAME" -g "$RESOURCE_GROUP" -o none 2>/dev/null; then
  log "  → updating existing Container App..."
  az containerapp update \
    --name           "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --image          "${ACR_SERVER}/${IMAGE_NAME}:latest" \
    -o none
else
  log "  → creating new Container App..."
  az containerapp create \
    --name              "$APP_NAME" \
    --resource-group    "$RESOURCE_GROUP" \
    --environment       "$CAE_NAME" \
    --image             "${ACR_SERVER}/${IMAGE_NAME}:latest" \
    --registry-server   "$ACR_SERVER" \
    --registry-username "$ACR_USER" \
    --registry-password "$ACR_PASS" \
    --target-port       8000 \
    --ingress           external \
    --min-replicas      1 \
    --max-replicas      3 \
    --cpu               1.0 \
    --memory            2.0Gi \
    --env-vars \
      "AZURE_OPENAI_ENDPOINT=${AI_ENDPOINT}" \
      "AZURE_OPENAI_API_KEY=secretref:aoai-key" \
      "AZURE_OPENAI_DEPLOYMENT_NAME=${MODEL_DEPLOYMENT}" \
      "AZURE_OPENAI_API_VERSION=2024-12-01-preview" \
    --secrets "aoai-key=${AOAI_KEY}" \
    -o none
fi

APP_FQDN=$(az containerapp show -n "$APP_NAME" -g "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)
ok "langgraph-api deployed: https://${APP_FQDN}"

# Inject APP_URL so the running app can embed it in the OpenAPI spec's servers[] block.
# This enables Foundry tool registration to use the live /openapi.json directly.
log "Setting APP_URL env var on ${APP_NAME}..."
az containerapp update \
  --name           "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --set-env-vars   "APP_URL=https://${APP_FQDN}" \
  -o none
ok "APP_URL=https://${APP_FQDN}"

# ── Deploy redis ──────────────────────────────────────────────────────────────
log "Deploying ${REDIS_NAME}..."

if az containerapp show -n "$REDIS_NAME" -g "$RESOURCE_GROUP" -o none 2>/dev/null; then
  ok "redis already exists — skipping."
else
  az containerapp create \
    --name           "$REDIS_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --environment    "$CAE_NAME" \
    --image          "redis:7-alpine" \
    --target-port    6379 \
    --ingress        internal \
    --min-replicas   1 \
    --max-replicas   1 \
    --cpu            0.25 \
    --memory         0.5Gi \
    -o none
  ok "redis deployed (internal only)"
fi
