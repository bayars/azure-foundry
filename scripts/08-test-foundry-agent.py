#!/usr/bin/env python3
"""
08-test-foundry-agent.py — Test the AI Foundry agent end-to-end via REST API

What it does:
  Drives a full conversation through the AI Foundry agent (langgraph-demo-agent)
  using the Foundry threads/runs API — the same path Foundry uses internally.

  Flow:
    1. Create a Foundry thread
    2. Add a user message
    3. Create a run (agent processes the message, calls LangGraph via OpenAPI tool)
    4. Poll the run until complete
    5. Print all messages in the thread

  The agent (gpt-4o-mini) orchestrates the LangGraph API calls automatically:
  start_run → poll get_run_state → resume_run on interrupt → repeat until complete.

Usage:
  python3 scripts/08-test-foundry-agent.py
  python3 scripts/08-test-foundry-agent.py --graph code_review
  python3 scripts/08-test-foundry-agent.py --message "Review: def add(a,b): return a+b" --graph code_review
"""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────
AI_HUB       = "safabayar"
AI_PROJECT   = "proj-default"
AGENT_NAME   = "langgraph-demo-agent"
API_VERSION  = "2025-05-15-preview"
BASE_URL     = f"https://{AI_HUB}.services.ai.azure.com/api/projects/{AI_PROJECT}"
POLL_INTERVAL = 3
MAX_POLLS     = 30


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_token() -> str:
    return subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", "https://ai.azure.com/",
         "--query", "accessToken", "-o", "tsv"],
        text=True
    ).strip()


def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = f"{BASE_URL}/{path}?api-version={API_VERSION}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            err = json.loads(body_bytes)
        except Exception:
            err = {"raw": body_bytes.decode()}
        raise RuntimeError(f"HTTP {e.code}: {err}") from e


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", default="support_agent",
                        choices=["support_agent", "code_review"])
    parser.add_argument("--message", default=None,
                        help="User message (defaults to a graph-appropriate example)")
    args = parser.parse_args()

    if args.message:
        user_message = args.message
    elif args.graph == "support_agent":
        user_message = (
            "Hi, I'm Alice. My application crashes every time I try to export "
            "a large report. It worked fine last week."
        )
    else:
        user_message = (
            "Please review this Python code:\n"
            "```python\n"
            "def divide(a, b):\n"
            "    return a / b\n"
            "```"
        )

    print(f"[foundry-test] Getting access token...")
    token = get_token()

    # 1. Create thread
    print(f"[foundry-test] Creating thread...")
    thread = api("POST", "threads", token, {})
    thread_id = thread["id"]
    print(f"  Thread ID: {thread_id}")

    # 2. Add user message
    print(f"[foundry-test] Adding user message...")
    api("POST", f"threads/{thread_id}/messages", token,
        {"role": "user", "content": user_message})
    print(f"  Message: {user_message[:80]}{'...' if len(user_message) > 80 else ''}")

    # 3. Create run
    print(f"[foundry-test] Starting agent run (agent={AGENT_NAME})...")
    run = api("POST", f"threads/{thread_id}/runs", token,
              {"agent_id": AGENT_NAME})
    run_id = run["id"]
    print(f"  Run ID: {run_id}")

    # 4. Poll until done
    print(f"[foundry-test] Polling run status...")
    polls = 0
    while polls < MAX_POLLS:
        time.sleep(POLL_INTERVAL)
        run = api("GET", f"threads/{thread_id}/runs/{run_id}", token)
        status = run.get("status", "unknown")
        print(f"  [{polls+1}/{MAX_POLLS}] status={status}")

        if status in ("completed", "failed", "cancelled", "expired"):
            break
        polls += 1

    print()
    if run.get("status") != "completed":
        print(f"Run ended with status: {run.get('status')}")
        if run.get("last_error"):
            print(f"Error: {run['last_error']}")
        return

    # 5. Print all messages
    print("[foundry-test] Thread messages:")
    messages = api("GET", f"threads/{thread_id}/messages", token)
    for msg in reversed(messages.get("data", [])):
        role = msg.get("role", "?").upper()
        for block in msg.get("content", []):
            if block.get("type") == "text":
                text = block["text"]["value"]
                print(f"\n  [{role}]")
                print(f"  {text}")

    print(f"\n[foundry-test] Done. Thread: {thread_id}")


if __name__ == "__main__":
    main()
