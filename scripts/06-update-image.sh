#!/usr/bin/env bash
# =============================================================================
# 06-update-image.sh — Rebuild, push, and redeploy after code changes
#
# What it does:
#   1. Builds a new Docker image tagged with today's date + a suffix
#   2. Pushes it to ACR
#   3. Updates the langgraph-api Container App to use the new tag
#      (using an explicit tag forces a new revision; :latest does not)
#
# Use this whenever you change code in langgraph-api/.
# Always use an explicit tag — updating to :latest silently reuses the cached
# image if the revision already points to :latest.
#
# Usage:
#   ./06-update-image.sh
#   SUFFIX=hotfix ./06-update-image.sh
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/config.sh"

require_az_login

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUFFIX="${SUFFIX:-$(date +%Y%m%d-%H%M)}"
FULL_IMAGE="${ACR_SERVER}/${IMAGE_NAME}"
NEW_TAG="${FULL_IMAGE}:${SUFFIX}"

log "Building ${NEW_TAG}..."
az acr login --name "$ACR_NAME"
docker build -t "${FULL_IMAGE}:latest" -t "$NEW_TAG" "${REPO_ROOT}/langgraph-api"

log "Pushing ${NEW_TAG}..."
docker push "${FULL_IMAGE}:latest"
docker push "$NEW_TAG"

log "Updating Container App to ${NEW_TAG}..."
REVISION=$(az containerapp update \
  --name           "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image          "$NEW_TAG" \
  --query "properties.latestRevisionName" -o tsv)

ok "Deployed revision: ${REVISION}"
ok "Image: ${NEW_TAG}"

log "Waiting for health check..."
sleep 10
STATUS=$(curl -sf "${APP_URL}/health" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "unreachable")
ok "Health: ${STATUS}"
