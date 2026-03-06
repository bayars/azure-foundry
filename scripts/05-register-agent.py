#!/usr/bin/env python3
"""
05-register-agent.py — Create or update the AI Foundry agent

What it does:
  Registers (or re-registers) the langgraph-demo-agent in AI Foundry project
  proj-default under hub safabayar.

  The agent is registered as kind=prompt with an OpenAPI tool pointing at the
  deployed langgraph-api Container App. This is the stable, GUI-visible mode.

  container_app kind (AAAS native, no extra LLM layer):
    Attempted automatically. Falls back to prompt+openapi if the Foundry
    service returns a 500 (Foundry validates the wire protocol handshake
    server-side and the exact spec is not publicly documented).

  See docs/azure-deployment.md for the full explanation of agent kinds.

Usage:
  python3 scripts/05-register-agent.py
  python3 scripts/05-register-agent.py --kind container_app   # attempt AAAS native
  python3 scripts/05-register-agent.py --recreate             # delete + recreate
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────
import os

SUBSCRIPTION_ID   = os.environ.get("SUBSCRIPTION_ID", "")
RESOURCE_GROUP    = os.environ.get("RESOURCE_GROUP", "")
AI_HUB            = os.environ.get("AI_HUB", "")
AI_PROJECT        = os.environ.get("AI_PROJECT", "")
MODEL_DEPLOYMENT  = os.environ.get("MODEL_DEPLOYMENT", "gpt-4o-mini")
AGENT_NAME        = os.environ.get("AGENT_NAME", "langgraph-demo-agent")
APP_NAME          = os.environ.get("APP_NAME", "langgraph-api")
APP_URL           = os.environ.get("APP_URL", "")
API_VERSION       = os.environ.get("FOUNDRY_API_VERSION", "2025-05-15-preview")
BASE_URL          = f"https://{AI_HUB}.services.ai.azure.com/api/projects/{AI_PROJECT}"
CA_RESOURCE_ID    = os.environ.get(
    "CA_RESOURCE_ID",
    f"/subscriptions/{SUBSCRIPTION_ID}"
    f"/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.App/containerapps/{APP_NAME}",
)
OPENAPI_SPEC_PATH = os.environ.get("OPENAPI_SPEC_PATH", "docs/openapi-foundry.json")


def _require_env():
    missing = [v for v in ("AI_HUB", "AI_PROJECT", "APP_URL") if not os.environ.get(v)]
    if missing:
        print(
            f"ERROR: Missing required env vars: {', '.join(missing)}\n"
            "Run:  source scripts/config.sh",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_token() -> str:
    return subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", "https://ai.azure.com/",
         "--query", "accessToken", "-o", "tsv"],
        text=True
    ).strip()


def api(method: str, path: str, token: str, body: dict | None = None):
    url = f"{BASE_URL}/{path}?api-version={API_VERSION}"
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def agent_exists(token: str) -> bool:
    status, _ = api("GET", f"agents/{AGENT_NAME}", token)
    return status == 200


def delete_agent(token: str):
    status, body = api("DELETE", f"agents/{AGENT_NAME}", token)
    if status == 200:
        print(f"  Deleted existing agent: {AGENT_NAME}")
    else:
        print(f"  Delete returned {status}: {body}")


def create_version(token: str, definition: dict) -> tuple[int, dict]:
    payload = {
        "name": AGENT_NAME,
        "description": "LangGraph demo — support_agent and code_review graphs",
        "definition": definition,
    }
    # First attempt: create the agent
    status, body = api("POST", "agents", token, payload)
    if status == 200:
        return status, body
    # If agent already exists (no versions), post to versions endpoint
    status, body = api("POST", f"agents/{AGENT_NAME}/versions", token, payload)
    return status, body


# ── Definitions ───────────────────────────────────────────────────────────────

def container_app_definition() -> dict:
    """
    AAAS native — no extra LLM layer.
    Foundry routes turns directly to our /sessions/* endpoints.
    Requires the Container App to pass Foundry's internal protocol validation.
    """
    return {
        "kind": "container_app",
        "container_app_resource_id": CA_RESOURCE_ID,
        "container_protocol_versions": [
            {"protocol": "AzureAIAgentService", "version": "1.0"}
        ],
    }


def prompt_openapi_definition() -> dict:
    """
    prompt + OpenAPI tool — GUI visible, stable fallback.
    Foundry agent (gpt-4o-mini) orchestrates calls to the LangGraph API
    via the OpenAPI tool. One extra LLM layer but fully functional.
    """
    with open(OPENAPI_SPEC_PATH) as f:
        spec = json.load(f)

    return {
        "kind": "prompt",
        "model": MODEL_DEPLOYMENT,
        "instructions": (
            "You orchestrate LangGraph workflows. "
            "1) Call start_run with graph_id ('support_agent' or 'code_review') and input. "
            "2) Poll get_run_state until status is not 'running'. "
            "3) If status='interrupted', present the interrupt_payload to the user "
            "and call resume_run with their answer. "
            "4) Repeat until status='complete'."
        ),
        "tools": [{
            "type": "openapi",
            "name": "langgraph_api",
            "openapi": {
                "name": "langgraph_api",
                "description": "LangGraph Demo API — start, monitor and resume graph runs",
                "spec": spec,
                "auth": {"type": "anonymous"},
            },
        }],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    _require_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["container_app", "prompt"], default="prompt",
                        help="Agent kind to register (default: prompt)")
    parser.add_argument("--recreate", action="store_true",
                        help="Delete existing agent before creating")
    args = parser.parse_args()

    print(f"[05-register-agent] Getting token...")
    token = get_token()

    if args.recreate and agent_exists(token):
        print(f"[05-register-agent] Deleting existing agent {AGENT_NAME}...")
        delete_agent(token)

    # ── Attempt container_app kind ────────────────────────────────────────────
    if args.kind == "container_app":
        print(f"[05-register-agent] Attempting container_app kind registration...")
        definition = container_app_definition()
        if agent_exists(token):
            # Kind cannot change — must delete first
            print(f"  Agent exists. Deleting to change kind...")
            delete_agent(token)
        status, body = create_version(token, definition)
        if status == 200:
            latest = body.get("versions", {}).get("latest", {})
            print(f"  Created — version: {latest.get('version')}, kind: container_app")
            print(f"  Agent ID: {AGENT_NAME}")
            return
        else:
            print(f"  container_app registration failed ({status}): {body.get('error', {}).get('message', '')}")
            print(f"  NOTE: Foundry validates the AAAS protocol handshake server-side.")
            print(f"        The /sessions/* endpoints are implemented in the Container App.")
            print(f"        This may require Foundry-side allow-listing or a specific")
            print(f"        /.well-known endpoint. Falling back to prompt+openapi...")
            print()

    # ── Fallback / default: prompt + openapi ──────────────────────────────────
    print(f"[05-register-agent] Registering as prompt+openapi kind...")
    definition = prompt_openapi_definition()

    if agent_exists(token):
        # Check if kind matches — if not, delete and recreate
        _, existing = api("GET", f"agents/{AGENT_NAME}", token)
        existing_kind = (existing.get("versions", {})
                                 .get("latest", {})
                                 .get("definition", {})
                                 .get("kind", ""))
        if existing_kind != "prompt":
            print(f"  Kind mismatch (existing: {existing_kind}). Deleting...")
            delete_agent(token)
            status, body = create_version(token, definition)
        else:
            # Add new version
            payload = {
                "name": AGENT_NAME,
                "description": "LangGraph demo — support_agent and code_review graphs",
                "definition": definition,
            }
            status, body = api("POST", f"agents/{AGENT_NAME}/versions", token, payload)
    else:
        status, body = create_version(token, definition)

    if status == 200:
        version = (body.get("versions", {}).get("latest", {}).get("version")
                   or body.get("version", "?"))
        kind = (body.get("versions", {}).get("latest", {}).get("definition", {}).get("kind")
                or body.get("definition", {}).get("kind", "?"))
        print(f"  Registered — version: {version}, kind: {kind}")
        print(f"  Agent ID:   {AGENT_NAME}")
        print(f"  Portal:     https://ai.azure.com → {AI_HUB} → {AI_PROJECT} → Agents")
        print(f"  REST API:   {BASE_URL}/agents/{AGENT_NAME}")
    else:
        print(f"  ERROR ({status}): {body}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
