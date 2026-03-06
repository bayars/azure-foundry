#!/usr/bin/env bash
# =============================================================================
# 07-test-aaas.sh — Smoke test the AAAS sessions protocol end-to-end
#
# What it does:
#   Tests the four AAAS protocol endpoints directly against the deployed
#   Container App (not via Foundry). Runs a full support_agent turn cycle:
#
#   1. POST /sessions         → create session
#   2. POST /sessions/{id}/turns → send user message
#   3. GET  /sessions/{id}/turns/{turn_id} → poll until not in_progress
#   4. (if interrupted) POST /sessions/{id}/turns → send resume value
#   5. GET  /sessions/{id}/turns/{turn_id} → poll final state
#   6. DELETE /sessions/{id} → clean up
#
# Polls up to MAX_POLLS times with POLL_INTERVAL seconds between each.
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/config.sh"

MAX_POLLS=20
POLL_INTERVAL=3
GRAPH="${1:-support_agent}"   # pass graph name as first arg, default: support_agent

log "Testing AAAS protocol on ${APP_URL}"
log "Graph: ${GRAPH}"
echo

# ── 1. Create session ─────────────────────────────────────────────────────────
log "POST /sessions..."
SESSION=$(curl -sf -X POST "${APP_URL}/sessions" \
  -H "Content-Type: application/json" \
  -d "{\"graph_id\": \"${GRAPH}\", \"metadata\": {\"user_name\": \"AaasTestUser\"}}")
SESSION_ID=$(echo "$SESSION" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  Session ID: ${SESSION_ID}"

# ── 2. Send first turn ────────────────────────────────────────────────────────
if [ "$GRAPH" = "support_agent" ]; then
  USER_MSG="My application crashes every time I try to export a large report."
else
  USER_MSG="def divide(a, b): return a / b"
  # For code_review the snippet goes via the metadata or directly as content
fi

log "POST /sessions/${SESSION_ID}/turns..."
TURN=$(curl -sf -X POST "${APP_URL}/sessions/${SESSION_ID}/turns" \
  -H "Content-Type: application/json" \
  -d "{\"input\": [{\"role\": \"user\", \"content\": \"${USER_MSG}\"}]}")
TURN_ID=$(echo "$TURN" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  Turn ID: ${TURN_ID}"

# ── 3. Poll until not in_progress ────────────────────────────────────────────
poll_turn() {
  local sid="$1" tid="$2"
  curl -sf "${APP_URL}/sessions/${sid}/turns/${tid}"
}

log "Polling turn state..."
POLLS=0
while [ $POLLS -lt $MAX_POLLS ]; do
  RESULT=$(poll_turn "$SESSION_ID" "$TURN_ID")
  STATUS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "  [poll $((POLLS+1))/${MAX_POLLS}] status=${STATUS}"

  if [ "$STATUS" != "in_progress" ]; then
    break
  fi
  sleep $POLL_INTERVAL
  POLLS=$((POLLS + 1))
done

echo
echo "Turn result:"
echo "$RESULT" | python3 -m json.tool
echo

# ── 4. Handle interrupt if present ───────────────────────────────────────────
INTERRUPT_TYPE=$(echo "$RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('interrupt_type', ''))
" 2>/dev/null || echo "")

if [ -n "$INTERRUPT_TYPE" ]; then
  log "Interrupt detected: ${INTERRUPT_TYPE}"

  # Choose an appropriate resume value based on interrupt type
  case "$INTERRUPT_TYPE" in
    escalation_approval) RESUME_MSG="true" ;;
    clarification_needed) RESUME_MSG="The export crashes with an out-of-memory error on reports over 10k rows." ;;
    review_decision) RESUME_MSG="accept" ;;
    *) RESUME_MSG="yes" ;;
  esac

  log "POST /sessions/${SESSION_ID}/turns (resume: '${RESUME_MSG}')..."
  TURN2=$(curl -sf -X POST "${APP_URL}/sessions/${SESSION_ID}/turns" \
    -H "Content-Type: application/json" \
    -d "{\"input\": [{\"role\": \"user\", \"content\": \"${RESUME_MSG}\"}]}")
  TURN_ID2=$(echo "$TURN2" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  echo "  Turn ID: ${TURN_ID2}"

  log "Polling resumed turn..."
  POLLS=0
  while [ $POLLS -lt $MAX_POLLS ]; do
    RESULT2=$(poll_turn "$SESSION_ID" "$TURN_ID2")
    STATUS2=$(echo "$RESULT2" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    echo "  [poll $((POLLS+1))/${MAX_POLLS}] status=${STATUS2}"

    if [ "$STATUS2" != "in_progress" ]; then
      break
    fi
    sleep $POLL_INTERVAL
    POLLS=$((POLLS + 1))
  done

  echo
  echo "Final turn result:"
  echo "$RESULT2" | python3 -m json.tool
  echo
fi

# ── 5. Delete session ─────────────────────────────────────────────────────────
log "DELETE /sessions/${SESSION_ID}..."
curl -sf -X DELETE "${APP_URL}/sessions/${SESSION_ID}" | python3 -m json.tool

echo
ok "AAAS smoke test complete."
