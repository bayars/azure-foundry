# Example Prompts & Tracing Guide

---

## Contents

1. [Example inputs by entry point](#example-inputs-by-entry-point)
2. [Resume value reference](#resume-value-reference)
3. [How to trace in AI Foundry](#how-to-trace-in-ai-foundry)
4. [How to trace in Log Analytics](#how-to-trace-in-log-analytics)
5. [Optional: LangSmith tracing](#optional-langsmith-tracing)

---

## Example inputs by entry point

There are three ways to interact with the system. Each has a different input format.

```
┌──────────────────────────────────────────────────┐
│  1. AI Foundry portal / Foundry API              │  natural language
│     → agent (gpt-4o-mini) → OpenAPI tool         │
│       → POST /runs → GET /state → POST /resume   │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│  2. AAAS /sessions API                           │  structured turns
│     → sessions router → LangGraph graph directly │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│  3. LangGraph /runs API                          │  raw graph input
│     → LangGraph graph directly                   │
└──────────────────────────────────────────────────┘
```

---

### Entry point 1 — AI Foundry portal or Foundry API

Send natural language. The agent handles all graph calls automatically.

#### In the portal

1. https://ai.azure.com → hub `safabayar` → project `proj-default` → **Agents**
2. Open `langgraph-demo-agent` → **Test in playground**
3. Type your message and press Send

#### Via REST API

```bash
TOKEN=$(az account get-access-token --resource "https://ai.azure.com/" --query "accessToken" -o tsv)
BASE="https://safabayar.services.ai.azure.com/api/projects/proj-default"
API="?api-version=2025-05-15-preview"

# Create thread
THREAD_ID=$(curl -s -X POST "$BASE/threads$API" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Add message
curl -s -X POST "$BASE/threads/$THREAD_ID/messages$API" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"role": "user", "content": "My dashboard crashes on login"}'

# Run agent
RUN_ID=$(curl -s -X POST "$BASE/threads/$THREAD_ID/runs$API" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_id": "langgraph-demo-agent"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Poll until done
curl -s "$BASE/threads/$THREAD_ID/runs/$RUN_ID$API" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

#### support_agent examples

```
My application crashes every time I open the dashboard.
```
```
Hi, I'm Bob. I can't log in — it says "invalid credentials" even though
I just reset my password.
```
```
Our payment service has been down for 20 minutes. All checkout attempts
are failing with a 500 error. This is affecting hundreds of customers right now.
```
*(High/critical severity → agent will ask you to approve escalation)*

#### code_review examples

```
Please review this Python code:

def divide(a, b):
    return a / b
```
```
Review this TypeScript function:

async function fetchUser(id: string) {
  const res = await fetch(`/api/users/${id}`)
  return res.json()
}
```
```
Can you do a code review on:

import subprocess
def run_command(user_input):
    subprocess.run(user_input, shell=True)
```
*(Security issue → agent will produce a review; you respond accept/reject/re_review)*

---

### Entry point 2 — AAAS `/sessions` API

Direct protocol, no extra LLM layer. Used by `scripts/07-test-aaas.sh`.

Base URL: `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io`

#### support_agent full flow

```bash
# 1. Create session — specify graph and user metadata
curl -X POST .../sessions \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "support_agent",
    "metadata": { "user_name": "Alice" }
  }'
# → {"id": "<session_id>", "graph_id": "support_agent", "status": "created"}

# 2. Send first message
curl -X POST .../sessions/<session_id>/turns \
  -H "Content-Type: application/json" \
  -d '{
    "input": [{ "role": "user", "content": "My dashboard crashes on login" }]
  }'
# → {"id": "<turn_id>", "status": "in_progress"}

# 3. Poll until not in_progress
curl .../sessions/<session_id>/turns/<turn_id>
# → status: "completed" with interrupt_type: "clarification_needed"
# → output.messages[0].content = "Can you describe the error message?"

# 4. Send clarification (next turn — auto-resumes the interrupted run)
curl -X POST .../sessions/<session_id>/turns \
  -H "Content-Type: application/json" \
  -d '{
    "input": [{ "role": "user", "content": "It shows a white screen with error code 502" }]
  }'

# 5. Poll again
curl .../sessions/<session_id>/turns/<turn_id>
# → may hit escalation_approval if severity is high/critical:
# → output.messages[0].content = "Do you approve escalation? Reply true/false"

# 6. Send escalation decision
curl -X POST .../sessions/<session_id>/turns \
  -H "Content-Type: application/json" \
  -d '{"input": [{"role": "user", "content": "true"}]}'

# 7. Poll final state → status: "completed"

# 8. Clean up
curl -X DELETE .../sessions/<session_id>
```

#### code_review full flow

```bash
# 1. Create session
curl -X POST .../sessions \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "code_review",
    "metadata": { "language": "python" }
  }'

# 2. Send code snippet as message content
curl -X POST .../sessions/<session_id>/turns \
  -H "Content-Type: application/json" \
  -d '{
    "input": [{
      "role": "user",
      "content": "def divide(a, b):\n    return a / b"
    }]
  }'

# 3. Poll → status: "completed"
#    interrupt_type: "review_decision"
#    output contains the full review text

# 4. Respond with decision
curl -X POST .../sessions/<session_id>/turns \
  -H "Content-Type: application/json" \
  -d '{"input": [{"role": "user", "content": "re_review"}]}'
# Valid values: "accept" | "reject" | "re_review"

# 5. Poll again after re_review → another review + interrupt
# 6. Final decision
curl -X POST .../sessions/<session_id>/turns \
  -H "Content-Type: application/json" \
  -d '{"input": [{"role": "user", "content": "accept"}]}'
```

---

### Entry point 3 — LangGraph `/runs` API

Lowest level. Useful for direct testing and custom clients.

Base URL: `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io`

#### support_agent

```bash
# Start run
curl -X POST .../runs \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "support_agent",
    "input": {
      "user_name": "Alice",
      "message": "My dashboard crashes on login"
    }
  }'
# → {"run_id": "abc123", "thread_id": "xyz789", "status": "running"}

# Poll state
curl .../runs/abc123/state
# → {"status": "interrupted", "interrupt_payload": {"type": "clarification_needed", ...}}

# Resume with clarification text
curl -X POST .../runs/abc123/resume \
  -H "Content-Type: application/json" \
  -d '{"resume_value": "Shows a 502 error after 30 seconds"}'

# Poll again → may hit escalation_approval
curl .../runs/abc123/state
# → {"status": "interrupted", "interrupt_payload": {"type": "escalation_approval", ...}}

# Approve escalation
curl -X POST .../runs/abc123/resume \
  -H "Content-Type: application/json" \
  -d '{"resume_value": true}'

# Poll final → status: "complete"
curl .../runs/abc123/state
```

#### code_review

```bash
# Start run
curl -X POST .../runs \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "code_review",
    "input": {
      "code_snippet": "def divide(a, b):\n    return a / b",
      "language": "python"
    }
  }'

# Poll → status: "interrupted", interrupt_payload.type: "review_decision"
# The review text is in state_values.review_output

# Accept
curl -X POST .../runs/<run_id>/resume \
  -H "Content-Type: application/json" \
  -d '{"resume_value": "accept"}'

# OR request re-review (up to 3 times)
curl -X POST .../runs/<run_id>/resume \
  -H "Content-Type: application/json" \
  -d '{"resume_value": "re_review"}'
```

---

## Resume value reference

| Graph | Interrupt type | What triggers it | Valid `resume_value` |
|---|---|---|---|
| `support_agent` | `clarification_needed` | Issue description is unclear | Any string — the user's answer |
| `support_agent` | `escalation_approval` | Severity is `high` or `critical` | `true` (approve) or `false` (deny) |
| `code_review` | `review_decision` | Review is generated | `"accept"`, `"reject"`, `"re_review"` |

**For the `/sessions` API:** `resume_value` is sent as the plain `content` of the next turn message. The sessions router converts it automatically:
- `"true"` / `"yes"` / `"approve"` → Python `True` for escalation_approval
- anything else → passed through as a string

**For the `/runs` API:** Send the exact type — `true`/`false` as JSON boolean for escalation, string for others.

---

## How to trace in AI Foundry

### Portal — Tracing view

After running the agent through the Foundry playground or API:

1. Go to https://ai.azure.com
2. Select hub **`safabayar`**
3. Select project **`proj-default`**
4. Left sidebar → **Tracing**

What you see per agent run:

```
Thread: <thread_id>
└── Run: <run_id>
    ├── [model call] gpt-4o-mini        ← agent deciding what to do
    │     input:  user message
    │     output: tool_call: start_run
    │     tokens: 312 prompt / 48 completion
    │     latency: 1.2s
    │
    ├── [tool call] langgraph_api.start_run
    │     input:  {"graph_id": "support_agent", "input": {...}}
    │     output: {"run_id": "...", "status": "running"}
    │
    ├── [tool call] langgraph_api.get_run_state   ← polling
    │     output: {"status": "in_progress"}
    │
    ├── [tool call] langgraph_api.get_run_state
    │     output: {"status": "interrupted", "interrupt_payload": {...}}
    │
    ├── [model call] gpt-4o-mini        ← agent presenting interrupt to user
    │     output: "Do you approve escalation? ..."
    │
    ├── [tool call] langgraph_api.resume_run
    │     input:  {"resume_value": true}
    │     output: {"status": "running"}
    │
    ├── [tool call] langgraph_api.get_run_state
    │     output: {"status": "complete"}
    │
    └── [model call] gpt-4o-mini        ← agent composing final reply
          output: "Ticket TKT-A1B2C3D4 created..."
```

### Portal — Thread inspector

1. Left sidebar → **Threads** (or via Agents → select agent → Threads)
2. Click any thread to see the full message history
3. Expand a run to see each step, tool call, and model output side by side

### Foundry API — list runs and steps programmatically

```bash
TOKEN=$(az account get-access-token --resource "https://ai.azure.com/" --query "accessToken" -o tsv)
BASE="https://safabayar.services.ai.azure.com/api/projects/proj-default"
API="?api-version=2025-05-15-preview"

# List all threads
curl -s "$BASE/threads$API" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# List runs for a thread
curl -s "$BASE/threads/<thread_id>/runs$API" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# List steps for a run (each tool call and model call is a step)
curl -s "$BASE/threads/<thread_id>/runs/<run_id>/steps$API" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# List messages in a thread
curl -s "$BASE/threads/<thread_id>/messages$API" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Run steps response shows each `tool_calls` with input/output:

```json
{
  "data": [
    {
      "type": "tool_calls",
      "step_details": {
        "tool_calls": [
          {
            "type": "function",
            "function": {
              "name": "start_run",
              "arguments": "{\"graph_id\": \"support_agent\", \"input\": {...}}",
              "output": "{\"run_id\": \"...\", \"status\": \"running\"}"
            }
          }
        ]
      }
    }
  ]
}
```

---

## How to trace in Log Analytics

The Container Apps environment automatically sends all container stdout/stderr
to the Log Analytics workspace **`workspace-rgbayarsafa7080IjMs`**.

### Portal — Log Analytics

1. Azure Portal → Log Analytics workspaces → `workspace-rgbayarsafa7080IjMs`
2. Left sidebar → **Logs**
3. Run KQL queries (examples below)

### az CLI queries

```bash
WORKSPACE="1cb1a358-d319-46a0-aaf5-e3afb73e2394"
az monitor log-analytics query --workspace "$WORKSPACE" \
  --analytics-query "<KQL query here>" -o table
```

### Useful KQL queries

**All container logs (last 30 min):**
```kql
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(30m)
| where ContainerName_s == "langgraph-api"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

**All HTTP requests with status codes:**
```kql
ContainerAppConsoleLogs_CL
| where Log_s matches regex @"(GET|POST|DELETE|PUT) /"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

**Session lifecycle — track a session end-to-end:**
```kql
ContainerAppConsoleLogs_CL
| where Log_s contains "<session_id>"
| project TimeGenerated, Log_s
| order by TimeGenerated asc
```

**LLM calls to Azure OpenAI (every model invocation):**
```kql
ContainerAppConsoleLogs_CL
| where Log_s contains "chat/completions"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

**Errors only:**
```kql
ContainerAppConsoleLogs_CL
| where Log_s contains "ERROR" or Log_s contains "Exception"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

**Graph interrupts (human-in-the-loop events):**
```kql
ContainerAppConsoleLogs_CL
| where Log_s contains "interrupted"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

**Run throughput — count runs per hour:**
```kql
ContainerAppConsoleLogs_CL
| where Log_s contains "POST /runs"
| summarize count() by bin(TimeGenerated, 1h)
| render timechart
```

### Container log stream (real-time, az CLI)

```bash
# Stream live logs
az containerapp logs show \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --follow

# Last 50 lines
az containerapp logs show \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --tail 50
```

---

## Optional: LangSmith tracing

LangSmith provides deep LangGraph-native tracing — every graph node, edge
decision, LLM call, and state snapshot is captured and visualized as a graph.

### Enable

1. Get a LangSmith API key from https://smith.langchain.com
2. Add env vars to the Container App:

```bash
az containerapp update \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --set-env-vars \
    "LANGCHAIN_TRACING_V2=true" \
    "LANGCHAIN_PROJECT=langgraph-demo" \
    "LANGCHAIN_API_KEY=secretref:langsmith-key" \
  --replace-env-vars false
```

3. Add the secret:
```bash
az containerapp secret set \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --secrets "langsmith-key=<your-langsmith-key>"
```

### What LangSmith shows (vs Log Analytics)

| | Log Analytics | LangSmith |
|---|---|---|
| HTTP requests | Yes | No |
| LLM calls (raw) | Yes (httpx log lines) | Yes (structured, with token counts) |
| Graph node execution | No | Yes — each node as a span |
| State at each node | No | Yes — full state snapshot |
| Edge decisions | No | Yes |
| Interrupt events | Partial (log lines) | Yes — dedicated span |
| Latency per node | No | Yes |
| Replay / debug runs | No | Yes |

LangSmith is the recommended tool for debugging **graph logic**.
Log Analytics is for **infrastructure and HTTP-level** monitoring.
AI Foundry Tracing is for **agent orchestration** (tool calls, model calls, thread history).
