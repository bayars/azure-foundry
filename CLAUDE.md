# CLAUDE.md — LangGraph Demo Project

## Project Overview

LangGraph demo FastAPI service with two graphs (`support_agent`, `code_review`) deployed to Azure Container Apps and registered as an OpenAPI-tool agent in Azure AI Foundry.

## Azure Resources

| Resource | Name | Notes |
|---|---|---|
| Subscription | `5ec3a6f9-978c-4e02-9d96-135dbc85269e` | "Azure subscription 1" |
| Resource Group | `rg-bayarsafa-7080` | eastus2 |
| ACR | `safademo` / `safademo.azurecr.io` | Admin enabled; ACR Tasks **disabled** — use local docker build/push |
| Container Apps Env | `cae-langgraph` | eastus2 |
| Container App | `langgraph-api` | External ingress, port 8000, 1–3 replicas |
| Container App | `redis` | Internal ingress only, port 6379 |
| AI Hub | `safabayar` | AIServices S0 |
| AI Project | `proj-default` | Default project |
| Model | `gpt-4o-mini` | GlobalStandard, 10K TPM, version 2024-07-18 |
| AI Agent | `langgraph-demo-agent` | kind: prompt, **version 2**, tool: OpenAPI |

## Key Endpoints

- **LangGraph API:** `https://langgraph-api.ambitiousglacier-23b2e299.eastus2.azurecontainerapps.io`
- **OpenAPI spec:** `.../openapi.json` (raw 3.1) or `docs/openapi-foundry.json` (cleaned 3.0)
- **AI Foundry Project API:** `https://safabayar.services.ai.azure.com/api/projects/proj-default`
- **AI Foundry Agent:** `https://safabayar.services.ai.azure.com/api/projects/proj-default/agents/langgraph-demo-agent`

## LLM Backend

Azure OpenAI (not Ollama). Both graphs use `AzureChatOpenAI`:
- `AZURE_OPENAI_ENDPOINT` = `https://safabayar.cognitiveservices.azure.com/`
- `AZURE_OPENAI_DEPLOYMENT_NAME` = `gpt-4o-mini`
- `AZURE_OPENAI_API_KEY` = Container App secret `aoai-key`

## Agent Tool — OpenAPI (version 2, current)

The agent uses an **OpenAPI tool** (not a function tool). This means:
- Visible and configurable in the AI Foundry portal GUI (Tools → Add tool → OpenAPI)
- AI Foundry calls the LangGraph API **directly** — no client-side tool execution needed
- Exposes 3 operations: `start_run`, `get_run_state`, `resume_run`
- Auth: anonymous

Version 1 used a `function` tool (`invoke_langgraph`) — invisible in GUI, required client polling code. Superseded.

## Important Constraints

- **ACR Tasks blocked:** Always `docker build` + `docker push` locally. Never `az acr build`.
- **State is in-memory** (`MemorySaver`) — lost on restart. Redis deployed but not wired in yet.
- **Single replica only** until Redis checkpointer is integrated.
- **Agent kind is `prompt`** — `container_app` kind requires AAAS wire protocol which this API doesn't implement.
- **Use `docs/openapi-foundry.json`** for tool registration (not the raw `/openapi.json`) — raw spec is 3.1, portal needs 3.0.

## Image Update Workflow

```bash
az acr login --name safademo
docker build -t safademo.azurecr.io/langgraph-api:latest ./langgraph-api
docker push safademo.azurecr.io/langgraph-api:latest
az containerapp update --name langgraph-api --resource-group rg-bayarsafa-7080 \
  --image safademo.azurecr.io/langgraph-api:latest
```

## Agent Version Update Workflow

Use Python (not curl/shell) to avoid JSON quoting issues. POST to `.../versions` endpoint:

```python
# See docs/azure-deployment.md § Step 8 for full script
# Key: POST to .../agents/langgraph-demo-agent/versions (not PUT on the agent itself)
```

## Auth Token for AI Foundry API

```bash
TOKEN=$(az account get-access-token --resource "https://ai.azure.com/" --query "accessToken" -o tsv)
```

## Key Files

| File | Purpose |
|---|---|
| `docs/azure-deployment.md` | Full deployment guide |
| `docs/openapi-foundry.json` | Cleaned OpenAPI 3.0.3 spec for AI Foundry tool |
| `langgraph-api/graphs/support_agent.py` | Support graph (AzureChatOpenAI) |
| `langgraph-api/graphs/code_review.py` | Code review graph (AzureChatOpenAI) |
| `docker-compose.yml` | Local dev only (Ollama-based, not used in Azure) |
