# n8n Setup Guide

## Prerequisites

1. Stack is running: `docker compose up --build`
2. n8n is healthy at http://localhost:5678
3. LangGraph API is healthy at http://localhost:8000/health

---

## Importing Workflows

1. Open http://localhost:5678 in your browser.
2. On first launch, create an owner account (any email/password works locally).
3. Go to **Workflows** → **Add workflow** → **Import from file**.
4. Import both files from `n8n/workflows/`:
   - `support_agent_workflow.json`
   - `code_review_workflow.json`

---

## Activating Webhooks

Each workflow has a **Webhook** trigger node. To get webhook URLs:

1. Open a workflow.
2. Click the **Webhook** node.
3. Copy the **Test URL** (for manual testing) or **Production URL** (when workflow is activated).
4. Click **Activate** (toggle in the top-right) to enable the production URL.

Default webhook paths (after activation):
- Support Agent: `POST http://localhost:5678/webhook/start-support`
- Code Review:   `POST http://localhost:5678/webhook/code-review`

---

## Environment Variable in n8n

The workflows reference `$env.LANGGRAPH_API_URL` which is set to
`http://langgraph-api:8000` via `docker-compose.yml`.

If you run n8n outside Docker, set this as an n8n credential or replace the
expression with the literal URL `http://localhost:8000`.

---

## Test Commands

### Support Agent

```bash
# Trigger via n8n webhook
curl -X POST http://localhost:5678/webhook/start-support \
  -H 'Content-Type: application/json' \
  -d '{"message": "My login is broken", "user_name": "Alice"}'
```

n8n will:
1. Start a LangGraph run
2. Poll `/state` every 2 seconds
3. Pause at each `interrupted` status and show a **form** for human input
4. Resume the graph with the human's answer
5. Return the final state when complete

### Code Review

```bash
curl -X POST http://localhost:5678/webhook/code-review \
  -H 'Content-Type: application/json' \
  -d '{
    "code_snippet": "def divide(a, b):\n    return a / b",
    "language": "python"
  }'
```

n8n will show a **dropdown form** with options: accept / reject / re_review.

---

## Direct API Testing (without n8n)

```bash
# 1. Start a support agent run
RUN_ID=$(curl -s -X POST http://localhost:8000/runs \
  -H 'Content-Type: application/json' \
  -d '{"graph_id":"support_agent","input":{"message":"My app crashes on login","user_name":"Bob"}}' \
  | jq -r .run_id)

echo "Run ID: $RUN_ID"

# 2. Stream events (open in separate terminal)
curl -N http://localhost:8000/runs/$RUN_ID/stream

# 3. Poll state
curl http://localhost:8000/runs/$RUN_ID/state | jq .

# 4. Resume after clarification interrupt
curl -X POST http://localhost:8000/runs/$RUN_ID/resume \
  -H 'Content-Type: application/json' \
  -d '{"resume_value": "Error 500 on /dashboard after 2FA, happens on Chrome and Firefox"}'

# 5. Resume after escalation interrupt (true = approve, false = reject)
curl -X POST http://localhost:8000/runs/$RUN_ID/resume \
  -H 'Content-Type: application/json' \
  -d '{"resume_value": true}'

# --- Code Review ---
RUN_ID=$(curl -s -X POST http://localhost:8000/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "graph_id": "code_review",
    "input": {
      "code_snippet": "def divide(a, b):\n    return a / b",
      "language": "python"
    }
  }' | jq -r .run_id)

# Resume with decision
curl -X POST http://localhost:8000/runs/$RUN_ID/resume \
  -H 'Content-Type: application/json' \
  -d '{"resume_value": "accept"}'
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| n8n can't reach LangGraph API | Check `LANGGRAPH_API_URL=http://langgraph-api:8000` in compose env |
| Workflow stuck polling | Increase `Wait 2s` node or check graph logs: `docker logs langgraph-api` |
| Form node not rendering | Ensure n8n version supports `n8n-nodes-base.form` (n8n ≥ 1.x) |
| `ANTHROPIC_API_KEY` missing | Copy `.env.example` → `.env` and fill in your key |
