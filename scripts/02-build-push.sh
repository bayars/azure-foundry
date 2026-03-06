#!/usr/bin/env bash
# =============================================================================
# 02-build-push.sh — Build and push the langgraph-api Docker image to ACR
#
# What it does:
#   1. Logs in to ACR
#   2. Builds the image from ./langgraph-api/Dockerfile
#   3. Tags as both :latest and :YYYYMMDD
#   4. Pushes both tags to safademo.azurecr.io
#
# NOTE: az acr build (ACR Tasks) is DISABLED on this subscription.
#       Docker must be running locally.
#
# Override the tag:
#   IMAGE_TAG=20260305 ./02-build-push.sh
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/config.sh"

require_az_login

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATE_TAG="$(date +%Y%m%d)"
FULL_IMAGE="${ACR_SERVER}/${IMAGE_NAME}"

log "Enabling ACR admin (required for Container Apps pull)..."
az acr update -n "$ACR_NAME" --admin-enabled true -o none

log "Logging in to ACR ${ACR_SERVER}..."
az acr login --name "$ACR_NAME"

log "Building image from ${REPO_ROOT}/langgraph-api..."
docker build \
  -t "${FULL_IMAGE}:latest" \
  -t "${FULL_IMAGE}:${DATE_TAG}" \
  "${REPO_ROOT}/langgraph-api"

log "Pushing ${FULL_IMAGE}:latest ..."
docker push "${FULL_IMAGE}:latest"

log "Pushing ${FULL_IMAGE}:${DATE_TAG} ..."
docker push "${FULL_IMAGE}:${DATE_TAG}"

ok "Image pushed: ${FULL_IMAGE}:latest"
ok "Image pushed: ${FULL_IMAGE}:${DATE_TAG}"
