#!/usr/bin/env bash
# =============================================================================
# 01-deploy-model.sh — Deploy gpt-4o-mini to the AI Foundry hub
#
# What it does:
#   Deploys the gpt-4o-mini model to the safabayar AI Foundry hub using
#   GlobalStandard SKU with 10K TPM capacity. This model is used by both
#   the LangGraph API (as AzureChatOpenAI) and by the Foundry agent orchestrator.
#
# Run once. Idempotent — re-running updates the deployment if it already exists.
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/config.sh"

require_az_login

log "Deploying model ${MODEL_DEPLOYMENT} to hub ${AI_HUB}..."

az cognitiveservices account deployment create \
  --name              "$AI_HUB" \
  --resource-group    "$RESOURCE_GROUP" \
  --deployment-name   "$MODEL_DEPLOYMENT" \
  --model-name        "gpt-4o-mini" \
  --model-version     "2024-07-18" \
  --model-format      "OpenAI" \
  --sku-capacity      10 \
  --sku-name          "GlobalStandard"

ok "Model deployed: ${MODEL_DEPLOYMENT}"
ok "Endpoint: ${AI_ENDPOINT}"
