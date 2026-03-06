# Deployment & Operations Scripts

Each script covers one specific step of the deployment or testing process.
They are numbered in execution order but are independent — run only what you need.

---

## Prerequisites

- Azure CLI logged in: `az login`
- Docker running locally
- Python 3.10+ in PATH
- Working directory: the **repo root** (`langgraph-demo/`), not `scripts/`

```bash
cd /path/to/langgraph-demo
az login
```

---

## Scripts Overview

```
scripts/
├── config.sh                  shared variables sourced by all shell scripts
├── 01-deploy-model.sh         deploy gpt-4o-mini to AI Foundry hub
├── 02-build-push.sh           build Docker image and push to ACR
├── 03-create-infra.sh         create Container Apps environment
├── 04-deploy-apps.sh          deploy langgraph-api and redis Container Apps
├── 05-register-agent.py       create / update the AI Foundry agent
├── 06-update-image.sh         rebuild + redeploy after code changes
├── 07-test-aaas.sh            smoke test the AAAS /sessions protocol directly
└── 08-test-foundry-agent.py   end-to-end test via the Foundry threads/runs API
```

---

## Full first-time deployment

Run these in order for a fresh environment:

```bash
./scripts/01-deploy-model.sh
./scripts/02-build-push.sh
./scripts/03-create-infra.sh
./scripts/04-deploy-apps.sh
python3 scripts/05-register-agent.py
```

Verify:
```bash
./scripts/07-test-aaas.sh
python3 scripts/08-test-foundry-agent.py
```

---

## Script details

---

### `config.sh` — shared configuration

All shell scripts source this file at startup. It defines every variable used
across the deployment (subscription ID, resource names, URLs, etc.) and
provides helper functions (`log`, `ok`, `fail`, `require_az_login`, `foundry_token`).

**Key variables:**

| Variable | Value |
|---|---|
| `SUBSCRIPTION_ID` | `5ec3a6f9-978c-4e02-9d96-135dbc85269e` |
| `RESOURCE_GROUP` | `rg-bayarsafa-7080` |
| `LOCATION` | `eastus2` |
| `ACR_SERVER` | `safademo.azurecr.io` |
| `CAE_NAME` | `cae-langgraph` |
| `APP_NAME` | `langgraph-api` |
| `AI_HUB` | `safabayar` |
| `AI_PROJECT` | `proj-default` |
| `MODEL_DEPLOYMENT` | `gpt-4o-mini` |
| `AGENT_NAME` | `langgraph-demo-agent` |
| `APP_URL` | `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io` |

---

### `01-deploy-model.sh` — deploy gpt-4o-mini

**Flow:**
```
az cognitiveservices account deployment create
  --deployment-name gpt-4o-mini
  --model-name      gpt-4o-mini
  --model-version   2024-07-18
  --sku-name        GlobalStandard
  --sku-capacity    10              ← 10,000 tokens per minute
```

**Why:** The LangGraph API uses `AzureChatOpenAI` which requires a model deployment
in the AI hub. The Foundry agent orchestrator also uses this deployment.

**Run once.** Re-running is idempotent.

---

### `02-build-push.sh` — build and push Docker image

**Flow:**
```
az acr update --admin-enabled true    ← required for Container Apps pull
az acr login --name safademo
docker build -t safademo.azurecr.io/langgraph-api:latest
             -t safademo.azurecr.io/langgraph-api:YYYYMMDD
             ./langgraph-api
docker push safademo.azurecr.io/langgraph-api:latest
docker push safademo.azurecr.io/langgraph-api:YYYYMMDD
```

**Why ACR Tasks are not used:** `az acr build` (ACR Tasks) is disabled on this
subscription tier. The image must be built locally and pushed directly.

**Always build locally — never use `az acr build`.**

---

### `03-create-infra.sh` — create Container Apps environment

**Flow:**
```
az containerapp env create
  --name  cae-langgraph
  --location eastus2
```

Auto-provisions a Log Analytics workspace for container logs.

**Idempotent:** skips if the environment already exists.

---

### `04-deploy-apps.sh` — deploy Container Apps

**Flow:**

```
1. Read ACR credentials:
   az acr credential show → ACR_USER, ACR_PASS

2. Read Azure OpenAI key:
   az cognitiveservices account keys list → AOAI_KEY

3. Create or update langgraph-api:
   az containerapp create/update
     --image  safademo.azurecr.io/langgraph-api:latest
     --ingress external, port 8000
     --min-replicas 1, --max-replicas 3
     --env-vars:
       AZURE_OPENAI_ENDPOINT
       AZURE_OPENAI_API_KEY        ← stored as a secret, not plain env var
       AZURE_OPENAI_DEPLOYMENT_NAME
       AZURE_OPENAI_API_VERSION

4. Create redis (if not exists):
   az containerapp create
     --image redis:7-alpine
     --ingress internal            ← not reachable from internet
     --min-replicas 1
```

**Secret handling:** `AZURE_OPENAI_API_KEY` is stored as a Container App secret
(`aoai-key`) and referenced with `secretref:aoai-key` — it never appears as a
plain environment variable in the portal or API responses.

**Idempotent:** updates if the app exists, creates if not.

---

### `05-register-agent.py` — register the AI Foundry agent

**Usage:**
```bash
python3 scripts/05-register-agent.py                    # default: prompt+openapi
python3 scripts/05-register-agent.py --kind container_app  # attempt AAAS native
python3 scripts/05-register-agent.py --recreate         # delete + recreate
```

**Flow (default — prompt + openapi):**
```
1. Get Azure AI Foundry access token
   az account get-access-token --resource https://ai.azure.com/

2. If agent exists and kind matches → POST .../agents/{name}/versions
   If agent exists and kind differs  → DELETE agent, POST .../agents
   If agent does not exist           → POST .../agents

3. Definition: kind=prompt, model=gpt-4o-mini
   Tool: openapi, spec from docs/openapi-foundry.json, auth=anonymous

4. Print agent ID, version, portal URL
```

**Flow (--kind container_app — AAAS native):**
```
1. Get token
2. If agent exists with different kind → delete it
3. POST .../agents with definition:
   {
     "kind": "container_app",
     "container_app_resource_id": "/subscriptions/.../containerapps/langgraph-api",
     "container_protocol_versions": [{"protocol": "AzureAIAgentService", "version": "1.0"}]
   }
4. If Foundry returns 500 → fall back to prompt+openapi
```

**Why `container_app` kind falls back:**
Foundry validates the AAAS protocol handshake against the Container App
server-side when registering. The exact wire-level validation spec is not
publicly documented. The AAAS `/sessions/*` endpoints are implemented in
`routers/sessions.py` and work correctly (verified by `07-test-aaas.sh`),
but the Foundry registration endpoint returns a 500 during validation.
This requires Foundry-side investigation (support ticket or undocumented
manifest endpoint). The prompt+openapi fallback is fully functional.

**Agent kind comparison:**

| Kind | Extra LLM | GUI visible | Foundry threads | Status |
|---|---|---|---|---|
| `prompt` + OpenAPI tool | Yes (gpt-4o-mini orchestrator) | Yes | No | **Working** |
| `container_app` | No | Yes (native) | Yes | Blocked (Foundry 500) |

---

### `06-update-image.sh` — rebuild after code changes

**Flow:**
```
1. Build image tagged with YYYYMMDD-HHMM (explicit, not :latest)
2. Push both :latest and the dated tag
3. az containerapp update --image <dated-tag>  ← forces new revision
4. Wait 10s and hit /health
```

**Why not just push :latest and re-run 04?**
Container Apps caches the image at revision creation time. Pointing a revision
to `:latest` when `:latest` is already the current tag does **not** trigger a
new pull. An explicit dated tag guarantees a new revision and fresh pull.

```bash
SUFFIX=my-feature ./scripts/06-update-image.sh
```

---

### `07-test-aaas.sh` — smoke test the AAAS sessions protocol

**Usage:**
```bash
./scripts/07-test-aaas.sh                     # test support_agent (default)
./scripts/07-test-aaas.sh code_review         # test code_review graph
```

**Flow:**
```
POST /sessions                 → {"id": "<session_id>", "status": "created"}

POST /sessions/{id}/turns      → {"id": "<turn_id>", "status": "in_progress"}
  input: [{"role": "user", "content": "..."}]

GET /sessions/{id}/turns/{tid} → poll every 3s up to 20 times
  in_progress → keep polling
  completed   → check for interrupt_type
  failed      → print error

if interrupt_type present:
  POST /sessions/{id}/turns  → resume with appropriate value
    escalation_approval  → "true"
    clarification_needed → clarification text
    review_decision      → "accept"
  GET /sessions/{id}/turns/{tid} → poll again

DELETE /sessions/{id}          → {"deleted": true}
```

**What this tests:**
- AAAS session lifecycle (create → turn → interrupt → resume → complete → delete)
- LangGraph graph execution via the sessions router
- Correct mapping of LangGraph interrupt types to AAAS turn responses
- Does NOT go through Foundry — calls the Container App directly

---

### `08-test-foundry-agent.py` — end-to-end Foundry agent test

**Usage:**
```bash
python3 scripts/08-test-foundry-agent.py
python3 scripts/08-test-foundry-agent.py --graph code_review
python3 scripts/08-test-foundry-agent.py --message "My login is broken"
```

**Flow:**
```
1. Get AI Foundry token
   az account get-access-token --resource https://ai.azure.com/

2. POST /threads                      → create Foundry thread
3. POST /threads/{id}/messages        → add user message
4. POST /threads/{id}/runs            → start agent run
   {"agent_id": "langgraph-demo-agent"}

5. Poll GET /threads/{id}/runs/{run_id} every 3s
   queued / in_progress → keep polling
   completed → done
   failed    → print error

   While in_progress the agent (gpt-4o-mini):
   - Calls start_run via OpenAPI tool → POST /runs
   - Polls get_run_state via OpenAPI tool → GET /runs/{id}/state
   - Calls resume_run on interrupt → POST /runs/{id}/resume
   - Repeats until LangGraph run is complete

6. GET /threads/{id}/messages         → print full conversation
```

**What this tests:**
- Full path: Foundry → OpenAPI tool → LangGraph API → gpt-4o-mini graph execution
- Foundry thread and run management
- Agent orchestration of multi-step LangGraph workflows

---

## Operational runbook

### Deploy code change

```bash
# Make your changes to langgraph-api/
./scripts/06-update-image.sh

# Verify
./scripts/07-test-aaas.sh
```

### Re-register agent after definition change

```bash
python3 scripts/05-register-agent.py --recreate
python3 scripts/08-test-foundry-agent.py
```

### Check Container App logs

```bash
az containerapp logs show \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --tail 50
```

### Roll back to a previous image

```bash
az containerapp update \
  --name langgraph-api \
  --resource-group rg-bayarsafa-7080 \
  --image safademo.azurecr.io/langgraph-api:<previous-tag>
```

### List all agent versions

```bash
TOKEN=$(az account get-access-token --resource "https://ai.azure.com/" --query "accessToken" -o tsv)
curl -s "https://safabayar.services.ai.azure.com/api/projects/proj-default/agents/langgraph-demo-agent?api-version=2025-05-15-preview" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## Known issues

### `container_app` kind returns HTTP 500 from Foundry

The `--kind container_app` option in `05-register-agent.py` currently returns
a 500 from the Foundry registration endpoint. The AAAS sessions protocol is
correctly implemented in `routers/sessions.py` (verified by `07-test-aaas.sh`).
The issue is that Foundry performs a server-side protocol validation during
registration whose exact requirements are not publicly documented.

**Workaround:** The script falls back to `prompt+openapi` automatically.

**To resolve:** File an Azure support ticket referencing request ID
`f694209bdecd88d2bbc12079d22b312c` and ask for the AAAS Container App
registration requirements for the `proj-default` project.

### State lost on container restart

The LangGraph `MemorySaver` checkpointer is in-memory. All run state
(including interrupted runs awaiting human input) is lost if the container
restarts. Redis is deployed but not yet wired in.

**To fix:** Replace `MemorySaver` with `AsyncRedisSaver` and set:
```bash
REDIS_URL=redis://redis.internal.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io:6379
```
