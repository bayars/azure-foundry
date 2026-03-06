# LangGraph Demo — Azure AI Foundry

A LangGraph FastAPI service with two agentic graphs deployed to Azure Container Apps and registered as a native agent in Azure AI Foundry via the AAAS protocol.

---

## Contents

1. [What this project is](#what-this-project-is)
2. [Local development](#local-development)
3. [Architecture](#architecture)
4. [AI Foundry concepts](#ai-foundry-concepts)
5. [Agent tool types explained](#agent-tool-types-explained)
6. [Foundry integration options](#foundry-integration-options)
7. [AAAS protocol — Option C implementation](#aaas-protocol--option-c-implementation)
8. [LangGraph API reference](#langgraph-api-reference)
9. [AAAS sessions API reference](#aaas-sessions-api-reference)
10. [Azure deployment](#azure-deployment)

---

## What this project is

Two LangGraph graphs exposed as a FastAPI service:

| Graph | What it does |
|---|---|
| `support_agent` | Customer support — clarification loop + escalation human-in-the-loop interrupt |
| `code_review` | Code review — re-review loop + accept/reject/re_review interrupt |

Both graphs use **Azure OpenAI `gpt-4o-mini`** as the LLM backend (Ollama used locally only).

The service is registered in **Azure AI Foundry** as a native `container_app` kind agent via the AAAS (Azure AI Agent Service) protocol — meaning Foundry manages threads and tracing while LangGraph handles all execution logic.

---

## Local development

```bash
cp .env.example .env
# Set OLLAMA_BASE_URL to your local Ollama instance

docker compose up
```

| Service | URL |
|---|---|
| LangGraph API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  AZURE AI FOUNDRY                   │
│                                                     │
│  Agent registry   Thread storage   Traces/evals     │
│                                                     │
│  langgraph-demo-agent (container_app kind)          │
│         │                                           │
└─────────┼───────────────────────────────────────────┘
          │  AAAS protocol (sessions/turns)
          │  POST /sessions/{id}/turns
          │  GET  /sessions/{id}/turns/{turn_id}
          ▼
┌─────────────────────────────────────────────────────┐
│          AZURE CONTAINER APPS (cae-langgraph)       │
│                                                     │
│  langgraph-api  (external, port 8000)               │
│  ├── /runs/*          → LangGraph run management    │
│  └── /sessions/*      → AAAS protocol endpoints     │
│                                                     │
│  redis  (internal, port 6379)                       │
│  └── future persistent checkpointer                 │
└─────────────────────────────────────────────────────┘
          │
          │  Azure OpenAI (gpt-4o-mini)
          ▼
┌─────────────────────────────────────────────────────┐
│  AI Hub: safabayar   Model: gpt-4o-mini             │
│  https://safabayar.cognitiveservices.azure.com/     │
└─────────────────────────────────────────────────────┘
```

---

## AI Foundry concepts

### What Foundry provides

Foundry is a **platform layer** that sits in front of your agents. It does not run your agent code — your Container App does that.

| Foundry feature | What it does |
|---|---|
| **Agent Registry** | Catalog of all agents — name, URL, version, metadata |
| **Thread management** | Owns conversation history, routes messages to the right agent |
| **Multi-agent orchestration** | Agent A can call Agent B as a sub-agent |
| **Portal playground** | Test any registered agent from the UI without writing code |
| **Tracing & evaluation** | Every turn logged — latency, token cost, quality scores |
| **Unified auth** | All agents secured via Azure AD, no individual API keys per agent |
| **Versioning** | Roll back an agent to a previous version at any time |

### Foundry does not care what is inside your agent

Whether your agent contains:
- A GPT-4o wrapper
- A LangGraph state machine with an LLM
- A LangGraph graph with **no LLM** (pure rules/deterministic)
- A legacy REST API

...Foundry treats them identically. It sends a message, expects a response. The internals are invisible to Foundry.

### Where the workflow lives

```
┌──────────────────────────────────┐
│         AZURE AI FOUNDRY         │
│                                  │
│  Stores:                         │
│  - Agent registration (URL)      │
│  - Conversation threads          │
│  - Traces                        │
└──────────────┬───────────────────┘
               │  sends user message
               ▼
┌──────────────────────────────────┐
│       YOUR CONTAINER APP         │
│                                  │
│  Runs:                           │
│  - LangGraph graphs              │  ← workflow lives here
│  - LLM calls                     │
│  - State machine logic           │
│  - Business rules                │
└──────────────────────────────────┘
```

The workflow is **not** in Foundry. Foundry is the front door and bus. Your container is the brain.

### Multi-agent orchestration

Once multiple agents are registered, Foundry routes between them:

```
User: "I have a bug causing customer complaints"
               │
               ▼
      FOUNDRY ORCHESTRATOR
               │
        ┌──────┴──────┐
        ▼             ▼
  code_review    support_agent      ← both your LangGraph containers
     agent          agent
        │             │
        └──────┬───────┘
               ▼
        combined response
```

The orchestration — which agent runs, what gets passed between them — is defined either in Foundry's workflow system or in a dedicated orchestrator agent.

---

## Agent tool types explained

### Why function tools don't exist in the GUI

A `function` tool in the Agents API is **not server-side execution**. It is a contract between the LLM and your client application:

```
Agent LLM → decides to call invoke_langgraph
    → returns tool_call JSON to YOUR code
        → YOUR code calls the LangGraph API
            → YOUR code sends result back to agent
```

The portal has no way to configure this because **your application is the executor**, not Azure. The GUI only shows tools that Azure executes entirely on its side:

| Tool | Who executes |
|---|---|
| Web search | Azure (Bing) |
| Code interpreter | Azure (sandboxed container) |
| Azure AI Search | Azure (your index) |
| **Function** | **Your application code** |

To use a `function` tool, your client must run a polling loop:

```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
import httpx, json, time

client = AIProjectClient(
    endpoint="https://safabayar.services.ai.azure.com/api/projects/proj-default",
    credential=DefaultAzureCredential(),
    api_version="2025-05-15-preview",
)

LANGGRAPH_URL = "https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io"

def handle_tool_call(name: str, arguments: dict) -> str:
    if name == "invoke_langgraph":
        r = httpx.post(f"{LANGGRAPH_URL}/runs", json={
            "graph_id": arguments["graph_name"],
            "input": arguments["input"],
        })
        return json.dumps(r.json())
    return "{}"

thread = client.agents.create_thread()
client.agents.create_message(thread.id, role="user",
    content="Review my Python code: def add(a,b): return a+b")

run = client.agents.create_run(thread_id=thread.id, agent_id="langgraph-demo-agent")

while run.status in ("queued", "in_progress", "requires_action"):
    time.sleep(1)
    run = client.agents.get_run(thread_id=thread.id, run_id=run.id)

    if run.status == "requires_action":
        tool_outputs = []
        for tc in run.required_action.submit_tool_outputs.tool_calls:
            result = handle_tool_call(tc.function.name, json.loads(tc.function.arguments))
            tool_outputs.append({"tool_call_id": tc.id, "output": result})
        run = client.agents.submit_tool_outputs_to_run(
            thread_id=thread.id, run_id=run.id, tool_outputs=tool_outputs
        )
```

### OpenAPI tool — GUI visible, no client loop

The `openapi` tool type is visible in the Foundry portal GUI and lets Foundry call the API directly. No client-side execution loop needed. The agent handles `start_run → poll state → resume on interrupt` autonomously.

| | Function tool | OpenAPI tool | AAAS (container_app) |
|---|---|---|---|
| GUI visible | No | Yes | Yes (as a full agent) |
| Execution | Client code | Foundry → your API | Foundry → your API |
| Extra LLM layer | Yes (orchestrator) | Yes (orchestrator) | **No** |
| Operations exposed | 1 combined | 3 separate | Protocol-native |
| Thread management | Client | Client | **Foundry** |
| Tracing | None | None | **Full Foundry traces** |

---

## Foundry integration options

### Option A — Foundry agent wraps LangGraph via OpenAPI tool (current v2)

```
Foundry agent (gpt-4o-mini) → decides to call OpenAPI tool → LangGraph API (gpt-4o-mini)
```

- Two LLM layers — redundant for a single-purpose agent
- No native Foundry thread ownership or tracing
- Good if you need Foundry to choose between multiple tools

### Option B — Connection only, call LangGraph directly

Register the URL as a Foundry connection. Visible in Management → Connections. No Foundry features (no threads, tracing, portal playground). Call the LangGraph API directly.

### Option C — container_app kind agent (recommended)

```
Foundry (manages threads + traces) → AAAS protocol → LangGraph API (brain)
```

- Single LLM layer — LangGraph does all the work
- Foundry owns threads: history stored and visible in portal
- Full tracing per turn
- Portal playground works natively
- Foundry can orchestrate this alongside other agents
- Requires implementing 4 AAAS protocol endpoints (see below)

---

## AAAS protocol — Option C implementation

### What the protocol is

AAAS is a small REST contract. Foundry calls these endpoints on your Container App:

```
POST   /sessions                          → Foundry creates a conversation session
POST   /sessions/{id}/turns               → Foundry sends a user message; your agent runs
GET    /sessions/{id}/turns/{turn_id}     → Foundry polls for the result (async)
DELETE /sessions/{id}                     → Foundry ends the session
```

That is the entire protocol. Your LangGraph API maps them like this:

```
POST /sessions              → store session with graph_id + new thread_id
POST /sessions/{id}/turns   → start (or resume) a LangGraph run; return run_id as turn_id
GET  /sessions/{id}/turns/{turn_id}  → check run status; map to AAAS turn status
DELETE /sessions/{id}       → clean up session record
```

### How LangGraph interrupts map to AAAS turns

LangGraph human-in-the-loop interrupts become a two-turn exchange in AAAS:

```
Turn 1:  user sends message
         → LangGraph runs, hits interrupt (e.g. escalation approval needed)
         → turn completes with status="completed", output = the interrupt question

Turn 2:  user sends answer (true/false or text)
         → session detects pending interrupt, resumes the run
         → turn completes with status="completed", output = final result
```

### Register as container_app kind

After implementing the protocol endpoints:

```python
import json, subprocess, urllib.request

CA_RESOURCE_ID = (
    "/subscriptions/5ec3a6f9-978c-4e02-9d96-135dbc85269e"
    "/resourceGroups/rg-bayarsafa-7080"
    "/providers/Microsoft.App/containerapps/langgraph-api"
)

payload = {
    "name": "langgraph-demo-agent",
    "description": "LangGraph demo — support_agent and code_review graphs",
    "definition": {
        "kind": "container_app",
        "container_app_resource_id": CA_RESOURCE_ID,
        "container_protocol_versions": [
            {"protocol": "AzureAIAgentService", "version": "1.0"}
        ],
    }
}

token = subprocess.check_output(
    ["az", "account", "get-access-token", "--resource", "https://ai.azure.com/",
     "--query", "accessToken", "-o", "tsv"], text=True
).strip()

body = json.dumps(payload).encode()
req = urllib.request.Request(
    "https://safabayar.services.ai.azure.com/api/projects/proj-default"
    "/agents/langgraph-demo-agent/versions?api-version=2025-05-15-preview",
    data=body, method="POST",
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
)
with urllib.request.urlopen(req) as resp:
    print(json.dumps(json.loads(resp.read()), indent=2))
```

### Add via GUI (after implementing the protocol)

1. https://ai.azure.com → hub `safabayar` → project `proj-default` → **Agents**
2. Open `langgraph-demo-agent` → **Edit**
3. Change type to **Container App**
4. Select the `langgraph-api` Container App from the dropdown
5. Save

---

## LangGraph API reference

Base URL: `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io`

### `GET /health`

```json
{"status": "ok", "graphs": ["support_agent", "code_review"]}
```

### `POST /runs` — start a run

**support_agent input:**
```json
{
  "graph_id": "support_agent",
  "input": {
    "user_name": "Alice",
    "message": "My login is broken"
  }
}
```

**code_review input:**
```json
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

### `GET /runs/{run_id}/state` — poll status

```json
{
  "run_id": "...",
  "status": "interrupted",
  "interrupt_payload": {
    "type": "escalation_approval",
    "severity": "high",
    "summary": "User Alice has a HIGH severity issue..."
  }
}
```

Status values: `running` | `interrupted` | `complete` | `error`

### `POST /runs/{run_id}/resume` — resume after interrupt

```json
{"resume_value": true}
```

| Interrupt type | resume_value |
|---|---|
| `clarification_needed` | `"<answer text>"` |
| `escalation_approval` | `true` or `false` |
| `review_decision` | `"accept"`, `"reject"`, or `"re_review"` |

### `GET /runs/{run_id}/stream` — SSE stream

Events: `token`, `node_update`, `interrupted`, `complete`, `error`, `heartbeat`

### `POST /runs/{run_id}/feedback`

```json
{"score": 0.9, "comment": "Very helpful", "key": "user_feedback"}
```

---

## AAAS sessions API reference

Base URL: `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io`

These endpoints implement the Azure AI Agent Service protocol for Foundry integration.

### `POST /sessions` — create session

```json
{
  "graph_id": "support_agent",
  "metadata": {"user_name": "Alice"}
}
```

Response:
```json
{"id": "<session_id>", "graph_id": "support_agent", "status": "created"}
```

### `POST /sessions/{session_id}/turns` — send a message

```json
{
  "input": [{"role": "user", "content": "My login is broken"}]
}
```

If the session has a pending interrupt (from a previous turn), the content is used as the `resume_value` automatically.

Response:
```json
{"id": "<turn_id>", "session_id": "<session_id>", "status": "in_progress"}
```

### `GET /sessions/{session_id}/turns/{turn_id}` — poll turn result

Response when in progress:
```json
{"id": "<turn_id>", "status": "in_progress"}
```

Response when interrupted (turn completes, interrupt becomes the agent's reply):
```json
{
  "id": "<turn_id>",
  "status": "completed",
  "output": {
    "messages": [
      {
        "role": "assistant",
        "content": "Do you approve escalation to the on-call team? (true/false)"
      }
    ]
  },
  "interrupt_type": "escalation_approval"
}
```

Response when complete:
```json
{
  "id": "<turn_id>",
  "status": "completed",
  "output": {
    "messages": [
      {"role": "assistant", "content": "Ticket TKT-A1B2C3D4 created. Severity: high."}
    ]
  }
}
```

Response on error:
```json
{"id": "<turn_id>", "status": "failed", "error": "..."}
```

### `DELETE /sessions/{session_id}` — end session

```json
{"deleted": true}
```

---

## Azure deployment

### Resources

| Resource | Name | Location |
|---|---|---|
| Resource Group | `rg-bayarsafa-7080` | eastus2 |
| ACR | `safademo.azurecr.io` | eastus2 |
| Container Apps Env | `cae-langgraph` | eastus2 |
| Container App | `langgraph-api` | eastus2 |
| Container App | `redis` (internal) | eastus2 |
| AI Hub | `safabayar` | eastus2 |
| AI Project | `proj-default` | — |
| Model | `gpt-4o-mini` GlobalStandard 10K TPM | eastus2 |
| AI Agent | `langgraph-demo-agent` kind: container_app | — |

### Key endpoints

| Service | URL |
|---|---|
| LangGraph API | `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io` |
| AI Foundry Portal | https://ai.azure.com → `safabayar` → `proj-default` → Agents |
| AI Foundry Agent | `https://safabayar.services.ai.azure.com/api/projects/proj-default/agents/langgraph-demo-agent` |

### Image update

```bash
az acr login --name safademo
docker build -t safademo.azurecr.io/langgraph-api:latest ./langgraph-api
docker push safademo.azurecr.io/langgraph-api:latest
az containerapp update \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --image safademo.azurecr.io/langgraph-api:latest
```

> **Note:** `az acr build` (ACR Tasks) is disabled on this subscription. Always build locally.

### Full deployment guide

See [`docs/azure-deployment.md`](docs/azure-deployment.md) for the complete step-by-step guide including all `az` commands.

### Known limitations

**State is in-memory.** `MemorySaver` loses all run state on container restart. Redis is deployed internally but not yet wired in. To enable persistence replace `MemorySaver` with `AsyncRedisSaver` and set:
```
REDIS_URL=redis://redis.internal.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io:6379
```

**Single replica only** until Redis checkpointer is integrated — multiple replicas cannot share in-memory state.
