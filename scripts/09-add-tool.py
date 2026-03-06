#!/usr/bin/env python3
"""
09-add-tool.py — Add, list, or remove tools on the AI Foundry agent

Supported tool types:
  openapi        — OpenAPI tool pointing at the LangGraph API (GUI-visible)
  code_interpreter — Azure AI code execution sandbox
  bing_grounding — Bing web search grounding
  file_search    — Vector-store file retrieval

Reads config from environment (source scripts/config.sh first).

Usage:
  source scripts/config.sh
  python3 scripts/09-add-tool.py --list
  python3 scripts/09-add-tool.py --add openapi
  python3 scripts/09-add-tool.py --add openapi --spec path/to/custom-spec.json
  python3 scripts/09-add-tool.py --add code_interpreter
  python3 scripts/09-add-tool.py --add bing_grounding --bing-connection <connection-id>
  python3 scripts/09-add-tool.py --add file_search --vector-store <store-id>
  python3 scripts/09-add-tool.py --remove openapi
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

# ── Config from environment ───────────────────────────────────────────────────
AI_HUB           = os.environ.get("AI_HUB", "")
AI_PROJECT       = os.environ.get("AI_PROJECT", "")
MODEL_DEPLOYMENT = os.environ.get("MODEL_DEPLOYMENT", "gpt-4o-mini")
AGENT_NAME       = os.environ.get("AGENT_NAME", "langgraph-demo-agent")
APP_URL          = os.environ.get("APP_URL", "")
API_VERSION      = os.environ.get("FOUNDRY_API_VERSION", "2025-05-15-preview")
BASE_URL         = f"https://{AI_HUB}.services.ai.azure.com/api/projects/{AI_PROJECT}"
OPENAPI_SPEC_PATH = os.environ.get("OPENAPI_SPEC_PATH", "docs/openapi-foundry.json")

TOOL_TYPES = ("openapi", "code_interpreter", "bing_grounding", "file_search")


def _require_env():
    missing = [v for v in ("AI_HUB", "AI_PROJECT") if not os.environ.get(v)]
    if missing:
        print(
            f"ERROR: Missing required env vars: {', '.join(missing)}\n"
            "Run:  source scripts/config.sh",
            file=sys.stderr,
        )
        sys.exit(1)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def get_token() -> str:
    return subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", "https://ai.azure.com/",
         "--query", "accessToken", "-o", "tsv"],
        text=True,
    ).strip()


def api(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"{BASE_URL}/{path}?api-version={API_VERSION}"
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ── Agent helpers ─────────────────────────────────────────────────────────────

def get_agent(token: str) -> dict:
    status, body = api("GET", f"agents/{AGENT_NAME}", token)
    if status != 200:
        print(f"ERROR: Agent '{AGENT_NAME}' not found ({status}).", file=sys.stderr)
        print("Run scripts/05-register-agent.py first.", file=sys.stderr)
        sys.exit(1)
    return body


def current_definition(agent: dict) -> dict:
    """Extract the current definition from the agent response."""
    return (
        agent.get("versions", {}).get("latest", {}).get("definition")
        or agent.get("definition")
        or {}
    )


def current_tools(defn: dict) -> list:
    return defn.get("tools") or []


def post_new_version(token: str, defn: dict) -> tuple[int, dict]:
    payload = {
        "name": AGENT_NAME,
        "description": "LangGraph demo — support_agent and code_review graphs",
        "definition": defn,
    }
    return api("POST", f"agents/{AGENT_NAME}/versions", token, payload)


# ── Tool builders ─────────────────────────────────────────────────────────────

def build_openapi_tool(spec_path: str) -> dict:
    if not APP_URL:
        print("ERROR: APP_URL not set. Run: source scripts/config.sh", file=sys.stderr)
        sys.exit(1)

    if spec_path == OPENAPI_SPEC_PATH and APP_URL:
        # Try to fetch the live spec from the deployed app first.
        # The app embeds APP_URL in servers[] when that env var is set,
        # so the live spec is always up to date.
        live_url = f"{APP_URL.rstrip('/')}/openapi.json"
        try:
            req = urllib.request.Request(live_url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                spec = json.loads(resp.read())
            print(f"  Using live spec from {live_url}")
        except Exception:
            print(f"  Live spec unavailable, falling back to {spec_path}")
            with open(spec_path) as f:
                spec = json.load(f)
    else:
        with open(spec_path) as f:
            spec = json.load(f)

    # Ensure servers block points at the deployed app
    spec["servers"] = [{"url": APP_URL}]

    return {
        "type": "openapi",
        "name": "langgraph_api",
        "openapi": {
            "name": "langgraph_api",
            "description": "LangGraph Demo API — start, monitor and resume graph runs",
            "spec": spec,
            "auth": {"type": "anonymous"},
        },
    }


def build_code_interpreter_tool() -> dict:
    return {"type": "code_interpreter"}


def build_bing_grounding_tool(connection_id: str) -> dict:
    if not connection_id:
        print("ERROR: --bing-connection is required for bing_grounding tool.", file=sys.stderr)
        sys.exit(1)
    return {
        "type": "bing_grounding",
        "bing_grounding": {"connection_id": connection_id},
    }


def build_file_search_tool(vector_store_id: str) -> dict:
    if not vector_store_id:
        print("ERROR: --vector-store is required for file_search tool.", file=sys.stderr)
        sys.exit(1)
    return {
        "type": "file_search",
        "file_search": {"vector_store_ids": [vector_store_id]},
    }


# ── Actions ───────────────────────────────────────────────────────────────────

def list_tools(token: str):
    agent = get_agent(token)
    defn = current_definition(agent)
    tools = current_tools(defn)
    kind = defn.get("kind", "?")
    version = (
        agent.get("versions", {}).get("latest", {}).get("version")
        or agent.get("version", "?")
    )
    print(f"Agent:   {AGENT_NAME}")
    print(f"Kind:    {kind}")
    print(f"Version: {version}")
    print(f"Model:   {defn.get('model', '—')}")
    print()
    if not tools:
        print("Tools:   (none)")
    else:
        print(f"Tools ({len(tools)}):")
        for t in tools:
            ttype = t.get("type", "?")
            name = t.get("name", "")
            print(f"  - {ttype}" + (f" ({name})" if name else ""))


def add_tool(token: str, tool_type: str, args: argparse.Namespace):
    agent = get_agent(token)
    defn = current_definition(agent)
    tools = current_tools(defn)

    # Check for duplicate
    existing_types = [t.get("type") for t in tools]
    if tool_type in existing_types:
        print(f"Tool '{tool_type}' already present. Remove it first with --remove {tool_type}")
        sys.exit(1)

    if tool_type == "openapi":
        new_tool = build_openapi_tool(args.spec)
    elif tool_type == "code_interpreter":
        new_tool = build_code_interpreter_tool()
    elif tool_type == "bing_grounding":
        new_tool = build_bing_grounding_tool(args.bing_connection or "")
    elif tool_type == "file_search":
        new_tool = build_file_search_tool(args.vector_store or "")
    else:
        print(f"ERROR: Unknown tool type '{tool_type}'", file=sys.stderr)
        sys.exit(1)

    defn["tools"] = tools + [new_tool]

    print(f"Adding '{tool_type}' tool to agent '{AGENT_NAME}'...")
    status, body = post_new_version(token, defn)
    if status == 200:
        version = (
            body.get("versions", {}).get("latest", {}).get("version")
            or body.get("version", "?")
        )
        print(f"  Done — new version: {version}")
        print(f"  Portal: https://ai.azure.com → {AI_HUB} → {AI_PROJECT} → Agents")
    else:
        err = body.get("error", {}).get("message", str(body))
        print(f"  ERROR ({status}): {err}", file=sys.stderr)
        sys.exit(1)


def remove_tool(token: str, tool_type: str):
    agent = get_agent(token)
    defn = current_definition(agent)
    tools = current_tools(defn)

    before = len(tools)
    defn["tools"] = [t for t in tools if t.get("type") != tool_type]
    if len(defn["tools"]) == before:
        print(f"Tool '{tool_type}' not found on agent '{AGENT_NAME}'.")
        sys.exit(1)

    print(f"Removing '{tool_type}' tool from agent '{AGENT_NAME}'...")
    status, body = post_new_version(token, defn)
    if status == 200:
        version = (
            body.get("versions", {}).get("latest", {}).get("version")
            or body.get("version", "?")
        )
        print(f"  Done — new version: {version}")
    else:
        err = body.get("error", {}).get("message", str(body))
        print(f"  ERROR ({status}): {err}", file=sys.stderr)
        sys.exit(1)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    _require_env()

    parser = argparse.ArgumentParser(
        description="Manage tools on the AI Foundry agent"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true",
                       help="List current tools on the agent")
    group.add_argument("--add", choices=TOOL_TYPES, metavar="TOOL_TYPE",
                       help=f"Add a tool: {', '.join(TOOL_TYPES)}")
    group.add_argument("--remove", choices=TOOL_TYPES, metavar="TOOL_TYPE",
                       help="Remove a tool by type")

    parser.add_argument("--spec", default=OPENAPI_SPEC_PATH,
                        help="Path to OpenAPI spec JSON (for --add openapi)")
    parser.add_argument("--bing-connection", metavar="CONNECTION_ID",
                        help="Azure AI connection ID for Bing (for --add bing_grounding)")
    parser.add_argument("--vector-store", metavar="STORE_ID",
                        help="Vector store ID for file search (for --add file_search)")

    args = parser.parse_args()

    print("[09-add-tool] Getting access token...")
    token = get_token()

    if args.list:
        list_tools(token)
    elif args.add:
        add_tool(token, args.add, args)
    elif args.remove:
        remove_tool(token, args.remove)


if __name__ == "__main__":
    main()
