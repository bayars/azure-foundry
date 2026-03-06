# Azure AI Foundry Deployment Guide

End-to-end deployment of the LangGraph demo API to Azure Container Apps and registration as an agent in Azure AI Foundry.

---

## Architecture

```
Azure AI Foundry (safabayar hub / proj-default)
  └── Agent: langgraph-demo-agent  (gpt-4o-mini, tool: OpenAPI → langgraph_api)
        │
        │  direct HTTPS calls via OpenAPI tool (no client-side code needed)
        ▼
Azure Container Apps (cae-langgraph, eastus2)
  ├── langgraph-api   (external, port 8000)  ← FastAPI + LangGraph
  └── redis           (internal, port 6379)  ← future persistent checkpointer

Azure Container Registry: safademo.azurecr.io
  └── langgraph-api:latest
```

The LangGraph API uses **Azure OpenAI** (`gpt-4o-mini` deployed to `safabayar`) as the LLM backend.

The AI Foundry agent uses an **OpenAPI tool** to call the LangGraph API directly. No client-side tool execution code is needed — the agent autonomously starts runs, polls state, and resumes on interrupts.

---

## Azure Resources

| Resource | Name | Type | Location |
|---|---|---|---|
| Resource Group | `rg-bayarsafa-7080` | Resource Group | eastus2 |
| Container Registry | `safademo` | ACR Basic (admin enabled) | eastus2 |
| Container Apps Env | `cae-langgraph` | Container Apps Environment | eastus2 |
| Container App | `langgraph-api` | External ingress, port 8000, 1–3 replicas | eastus2 |
| Container App | `redis` | Internal ingress, port 6379 | eastus2 |
| AI Hub | `safabayar` | CognitiveServices / AIServices S0 | eastus2 |
| AI Project | `proj-default` | CognitiveServices/accounts/projects | eastus2 |
| Model Deployment | `gpt-4o-mini` | GlobalStandard, 10K TPM, v2024-07-18 | eastus2 |
| AI Agent | `langgraph-demo-agent` | AI Foundry Agent, kind: prompt, version 2 | — |

---

## Endpoints

| Service | URL |
|---|---|
| LangGraph API | `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io` |
| LangGraph OpenAPI spec | `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io/openapi.json` |
| AI Foundry Project API | `https://safabayar.services.ai.azure.com/api/projects/proj-default` |
| AI Foundry Agent | `https://safabayar.services.ai.azure.com/api/projects/proj-default/agents/langgraph-demo-agent` |
| AI Foundry Portal | https://ai.azure.com → hub `safabayar` → project `proj-default` → Agents |

---

## LangGraph API Reference

Base URL: `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io`

### Graphs

| Graph | Description |
|---|---|
| `support_agent` | Customer support with clarification loop + escalation interrupt |
| `code_review` | Code review with re-review loop + accept/reject/re_review interrupt |

### Endpoints

#### `GET /health`
```json
{"status": "ok", "graphs": ["support_agent", "code_review"]}
```

#### `POST /runs`
Start a new graph run. Returns immediately with `run_id`.

```json
// support_agent
{
  "graph_id": "support_agent",
  "input": {
    "user_name": "Alice",
    "message": "My login is broken"
  }
}

// code_review
{
  "graph_id": "code_review",
  "input": {
    "code_snippet": "def add(a, b):\n    return a + b",
    "language": "python"
  }
}
```

Response:
```json
{"run_id": "...", "thread_id": "...", "graph_id": "...", "status": "running"}
```

#### `GET /runs/{run_id}/state`
Poll for current run status. Check this after starting a run.

```json
{
  "run_id": "...",
  "thread_id": "...",
  "graph_id": "...",
  "status": "interrupted",
  "interrupt_payload": {
    "type": "escalation_approval",
    "severity": "high",
    "summary": "...",
    "user_name": "Alice"
  },
  "state_values": {...},
  "next_nodes": ["escalation_check"]
}
```

Status values: `running` | `interrupted` | `complete` | `error`

#### `GET /runs/{run_id}/stream`
SSE stream (for direct clients, not used by AI Foundry agent). Events:
- `token` — streaming LLM token
- `node_update` — node completed with state delta
- `interrupted` — graph paused, waiting for human input
- `complete` — graph finished
- `error` — graph failed
- `heartbeat` — keepalive (every 30s)

#### `POST /runs/{run_id}/resume`
Resume an interrupted run.

```json
{"resume_value": <value>}
```

| Interrupt type | `resume_value` |
|---|---|
| `support_agent` clarification | `"<answer text>"` (string) |
| `support_agent` escalation approval | `true` or `false` (boolean) |
| `code_review` decision | `"accept"`, `"reject"`, or `"re_review"` (string) |

#### `POST /runs/{run_id}/feedback`
Submit a feedback score (`0.0`–`1.0`) with optional comment and key.

---

## AI Foundry Agent Reference

**Agent ID:** `langgraph-demo-agent`
**Current version:** 2
**Model:** `gpt-4o-mini`
**Tool:** OpenAPI (`langgraph_api`) — anonymous auth
**API version:** `2025-05-15-preview`

### Authentication

```bash
TOKEN=$(az account get-access-token --resource "https://ai.azure.com/" --query "accessToken" -o tsv)
```

### Get agent

```bash
curl "https://safabayar.services.ai.azure.com/api/projects/proj-default/agents/langgraph-demo-agent?api-version=2025-05-15-preview" \
  -H "Authorization: Bearer $TOKEN"
```

### Use the agent — create thread, add message, run

```bash
# 1. Create thread
THREAD=$(curl -s -X POST \
  "https://safabayar.services.ai.azure.com/api/projects/proj-default/threads?api-version=2025-05-15-preview" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}')
THREAD_ID=$(echo $THREAD | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 2. Add a message
curl -s -X POST \
  "https://safabayar.services.ai.azure.com/api/projects/proj-default/threads/$THREAD_ID/messages?api-version=2025-05-15-preview" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"role": "user", "content": "Do a code review on: def add(a,b): return a+b"}'

# 3. Run the agent (it will call the LangGraph API autonomously)
curl -s -X POST \
  "https://safabayar.services.ai.azure.com/api/projects/proj-default/threads/$THREAD_ID/runs?api-version=2025-05-15-preview" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"agent_id": "langgraph-demo-agent"}'
```

### Python SDK

```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
import time

client = AIProjectClient(
    endpoint="https://safabayar.services.ai.azure.com/api/projects/proj-default",
    credential=DefaultAzureCredential(),
    api_version="2025-05-15-preview",
)

thread = client.agents.create_thread()
client.agents.create_message(thread.id, role="user", content="Review my Python code...")
run = client.agents.create_run(thread_id=thread.id, agent_id="langgraph-demo-agent")

# Poll until done (agent handles LangGraph calls automatically via OpenAPI tool)
while run.status in ("queued", "in_progress"):
    time.sleep(2)
    run = client.agents.get_run(thread_id=thread.id, run_id=run.id)

messages = client.agents.list_messages(thread_id=thread.id)
for msg in messages.data:
    print(f"{msg.role}: {msg.content[0].text.value}")
```

---

## OpenAPI Tool — Foundry GUI Setup

The agent's OpenAPI tool can be configured or recreated directly in the Azure AI Foundry portal (new UI).

**Steps:**

1. Go to https://ai.azure.com → hub `safabayar` → project `proj-default` → **Agents**
2. Open `langgraph-demo-agent` (or click **+ New agent**)
3. Set model: `gpt-4o-mini`
4. Set system instructions (see below)
5. In **Tools** → click **+ Add tool** → select **OpenAPI**
6. Choose one of:
   - **URL:** `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io/openapi.json`
   - **Upload file:** `docs/openapi-foundry.json` from this repo *(recommended — cleaned 3.0 spec, SSE endpoint removed)*
7. Authentication: **No authentication**
8. Click **Save**

**Why upload the file instead of the URL?**

The raw FastAPI spec (`/openapi.json`) is OpenAPI **3.1.0**. Some portal versions only accept **3.0.x**. The file `docs/openapi-foundry.json` is a cleaned 3.0.3 version with:
- SSE stream endpoint removed (not callable as a tool)
- `health` and `feedback` endpoints removed (not needed by agent)
- `nullable: true` instead of `anyOf/null` (3.0 syntax)
- Richer operation descriptions to guide the agent

**System instructions to paste:**

```
You are a helpful assistant that orchestrates LangGraph workflows.
To run a workflow:
1) Call start_run with graph_id ('support_agent' or 'code_review') and input.
2) Poll get_run_state until status is not 'running'.
3) If status='interrupted', show the interrupt_payload to the user and call resume_run with their answer.
4) Repeat step 2-3 until status='complete'.
```

### Function tool vs OpenAPI tool

| | Function tool (v1, deprecated) | OpenAPI tool (v2, current) |
|---|---|---|
| Visible in GUI | No | Yes |
| Execution location | Your client code | AI Foundry calls API directly |
| Client polling loop needed | Yes | No — agent handles it |
| Operations | 1 (`invoke_langgraph`) | 3 (`start_run`, `get_run_state`, `resume_run`) |
| Auth | N/A | Anonymous |

---

## Deployment Steps

### 1. Code change — Replace Ollama with Azure OpenAI

Both `graphs/support_agent.py` and `graphs/code_review.py`:

```python
# Before
from langchain_ollama import ChatOllama
llm = ChatOllama(model="llama3.1:8b", base_url=os.getenv("OLLAMA_BASE_URL"), temperature=0)

# After
from langchain_openai import AzureChatOpenAI
llm = AzureChatOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    temperature=0,
)
```

`requirements.txt`: `langchain-ollama` → `langchain-openai`

### 2. Deploy gpt-4o-mini model to AI Foundry hub

```bash
az cognitiveservices account deployment create \
  -n safabayar -g rg-bayarsafa-7080 \
  --deployment-name gpt-4o-mini \
  --model-name gpt-4o-mini \
  --model-version "2024-07-18" \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name GlobalStandard
```

### 3. Build & push image to ACR

ACR Tasks are disabled on this subscription. Build locally:

```bash
az acr update -n safademo --admin-enabled true
az acr login --name safademo

docker build \
  -t safademo.azurecr.io/langgraph-api:latest \
  -t safademo.azurecr.io/langgraph-api:$(date +%Y%m%d) \
  ./langgraph-api

docker push safademo.azurecr.io/langgraph-api:latest
docker push safademo.azurecr.io/langgraph-api:$(date +%Y%m%d)
```

### 4. Create Container Apps environment

```bash
az containerapp env create \
  --name cae-langgraph \
  --resource-group rg-bayarsafa-7080 \
  --location eastus2
```

### 5. Deploy langgraph-api Container App

```bash
ACR_USER=$(az acr credential show -n safademo --query "username" -o tsv)
ACR_PASS=$(az acr credential show -n safademo --query "passwords[0].value" -o tsv)
AOAI_KEY=$(az cognitiveservices account keys list -n safabayar -g rg-bayarsafa-7080 --query "key1" -o tsv)

az containerapp create \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --environment cae-langgraph \
  --image safademo.azurecr.io/langgraph-api:latest \
  --registry-server safademo.azurecr.io \
  --registry-username "$ACR_USER" \
  --registry-password "$ACR_PASS" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 --max-replicas 3 \
  --cpu 1.0 --memory 2.0Gi \
  --env-vars \
    "AZURE_OPENAI_ENDPOINT=https://safabayar.cognitiveservices.azure.com/" \
    "AZURE_OPENAI_API_KEY=secretref:aoai-key" \
    "AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o-mini" \
    "AZURE_OPENAI_API_VERSION=2024-12-01-preview" \
  --secrets "aoai-key=$AOAI_KEY"
```

### 6. Deploy Redis (internal)

```bash
az containerapp create \
  --name redis \
  --resource-group rg-bayarsafa-7080 \
  --environment cae-langgraph \
  --image redis:7-alpine \
  --target-port 6379 \
  --ingress internal \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.25 --memory 0.5Gi
```

### 7. Create AI Foundry Agent (v1 — function tool, superseded)

Initial agent created with a custom `function` tool. Not visible in GUI. Superseded by v2.

```python
# See git history or docs for the v1 payload
# kind: prompt, tool type: function, name: invoke_langgraph
```

### 8. Update agent to OpenAPI tool (v2 — current)

Uses Python to avoid shell JSON-quoting issues. `docs/openapi-foundry.json` must exist.

```python
import json, subprocess, urllib.request

spec = json.load(open("docs/openapi-foundry.json"))

payload = {
    "name": "langgraph-demo-agent",
    "description": "LangGraph demo agent - support_agent and code_review graphs",
    "definition": {
        "kind": "prompt",
        "model": "gpt-4o-mini",
        "instructions": (
            "You are a helpful assistant that orchestrates LangGraph workflows. "
            "1) Call start_run with graph_id and input. "
            "2) Poll get_run_state until status is not 'running'. "
            "3) If status='interrupted', show interrupt_payload and call resume_run with the user's answer. "
            "4) Repeat until status='complete'."
        ),
        "tools": [{
            "type": "openapi",
            "name": "langgraph_api",
            "openapi": {
                "name": "langgraph_api",
                "description": "LangGraph Demo API — start, monitor and resume graph runs",
                "spec": spec,
                "auth": {"type": "anonymous"}
            }
        }]
    }
}

token = subprocess.check_output(
    ["az", "account", "get-access-token", "--resource", "https://ai.azure.com/",
     "--query", "accessToken", "-o", "tsv"], text=True
).strip()

body = json.dumps(payload).encode()
req = urllib.request.Request(
    "https://safabayar.services.ai.azure.com/api/projects/proj-default/agents"
    "/langgraph-demo-agent/versions?api-version=2025-05-15-preview",
    data=body, method="POST",
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
)
with urllib.request.urlopen(req) as resp:
    print(json.loads(resp.read()))
```

### 9. Image update workflow (for future deploys)

```bash
az acr login --name safademo
docker build -t safademo.azurecr.io/langgraph-api:latest ./langgraph-api
docker push safademo.azurecr.io/langgraph-api:latest
az containerapp update \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --image safademo.azurecr.io/langgraph-api:latest
```

---

## Files in this repo

| File | Purpose |
|---|---|
| `langgraph-api/Dockerfile` | Container image definition |
| `langgraph-api/main.py` | FastAPI app entrypoint, graph registration |
| `langgraph-api/graphs/support_agent.py` | Support agent LangGraph graph |
| `langgraph-api/graphs/code_review.py` | Code review LangGraph graph |
| `langgraph-api/graphs/state/models.py` | Pydantic state models + API request/response schemas |
| `langgraph-api/routers/runs.py` | REST endpoints: start, stream, resume, state, feedback |
| `langgraph-api/requirements.txt` | Python dependencies |
| `docker-compose.yml` | Local dev only (uses Ollama) |
| `docs/azure-deployment.md` | This file |
| `docs/openapi-foundry.json` | Cleaned OpenAPI 3.0.3 spec for AI Foundry tool registration |
| `CLAUDE.md` | Quick reference for Claude Code sessions |

---

## Known Limitations & Notes

### ACR Tasks disabled
`az acr build` is blocked on this subscription tier. Always use local `docker build` + `docker push`.

### Agent kind: `prompt` not `container_app`
The `container_app` kind requires the Container App to implement the Azure AI Agent Service (AAAS) wire protocol handshake. The LangGraph API does not implement this. The agent uses `kind: prompt` with an OpenAPI tool instead, which works equivalently for this use case.

### OpenAPI tool vs function tool
Custom `function` tools are not visible or configurable in the AI Foundry portal GUI because they require client-side execution. The `openapi` tool type is GUI-visible and lets the agent call the API server-side without any client code.

### State persistence
The LangGraph checkpointer uses in-memory `MemorySaver`. State is lost on container restart. Redis is deployed internally at `redis.internal.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io:6379` but not yet wired in.

To enable persistence, replace `MemorySaver` with `AsyncRedisSaver` (`langgraph-checkpoint-redis`) and set env var:
```
REDIS_URL=redis://redis.internal.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io:6379
```

### Scaling
Multiple replicas cannot share in-memory state. Keep `--min-replicas 1 --max-replicas 1` until the Redis checkpointer is integrated.
