#!/usr/bin/env bash
# =============================================================================
# 03-create-infra.sh — Create the Azure Container Apps environment
#
# What it does:
#   Creates the Container Apps environment (cae-langgraph) in the resource
#   group. Also auto-provisions a Log Analytics workspace for container logs.
#
# Run once. Skip if the environment already exists.
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/config.sh"

require_az_login

# Check if environment already exists
if az containerapp env show -n "$CAE_NAME" -g "$RESOURCE_GROUP" -o none 2>/dev/null; then
  ok "Environment ${CAE_NAME} already exists — skipping."
  exit 0
fi

log "Creating Container Apps environment: ${CAE_NAME} in ${LOCATION}..."
az containerapp env create \
  --name           "$CAE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location       "$LOCATION"

ok "Environment created: ${CAE_NAME}"
