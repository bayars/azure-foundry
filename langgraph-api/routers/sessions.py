"""
AAAS Sessions Router
====================
Implements the Azure AI Agent Service (AAAS) wire protocol so this Container App
can be registered in Azure AI Foundry as a native `container_app` kind agent.

Foundry calls these four endpoints:
  POST   /sessions                          → create a session (maps to a LangGraph thread)
  POST   /sessions/{id}/turns               → send a user message; start or resume a run
  GET    /sessions/{id}/turns/{turn_id}     → poll for result (turn_id == run_id)
  DELETE /sessions/{id}                     → end the session

LangGraph interrupt handling:
  - When a run reaches an interrupt the turn completes with status="completed" and
    the interrupt payload rendered as the assistant's reply.
  - The next turn automatically resumes the interrupted run using the user's content
    as the resume_value.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage

from routers.runs import _graphs, _run_graph_background
from storage.run_store import run_store

log = logging.getLogger(__name__)
router = APIRouter()


# ── Session store ───────────────────────────────────────────────────────────


@dataclass
class SessionRecord:
    session_id: str
    graph_id: str
    thread_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # Set when a run is interrupted — cleared after the next turn resumes it
    pending_run_id: Optional[str] = None


_sessions: dict[str, SessionRecord] = {}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _interrupt_to_message(payload: dict[str, Any]) -> str:
    """Render an interrupt payload as a human-readable assistant message."""
    kind = payload.get("type", "")

    if kind == "escalation_approval":
        return (
            f"{payload.get('summary', '')}\n\n"
            "Do you approve escalation to the on-call engineering team? "
            "Reply **true** to approve or **false** to deny."
        )
    if kind == "clarification_needed":
        return payload.get("question", "Could you provide more details?")
    if kind == "review_decision":
        options = payload.get("options", ["accept", "reject", "re_review"])
        return (
            f"{payload.get('review', '')}\n\n"
            f"What would you like to do? Options: {', '.join(options)}"
        )

    # Fallback: serialise the raw payload
    return str(payload)


def _coerce_resume_value(content: str, interrupt_type: str) -> Any:
    """Convert a plain string from the user into the correct resume_value type."""
    if interrupt_type == "escalation_approval":
        return content.strip().lower() in ("true", "yes", "approve", "1")
    return content.strip()


def _last_assistant_content(run_id: str) -> str:
    """Extract the last assistant message content from a completed run's state."""
    record = run_store.get(run_id)
    if record is None:
        return ""
    graph = _graphs.get(record.graph_id)
    if graph is None:
        return ""
    try:
        import asyncio as _asyncio
        snapshot = _asyncio.get_event_loop().run_until_complete(
            graph.aget_state({"configurable": {"thread_id": record.thread_id}})
        )
        if snapshot and snapshot.values:
            messages = snapshot.values.get("messages", [])
            for msg in reversed(messages):
                content = getattr(msg, "content", "")
                role = getattr(msg, "type", "")
                if role != "human" and content:
                    return content
    except Exception as exc:
        log.warning("Could not fetch final state for run %s: %s", run_id, exc)
    return "Run completed."


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("")
async def create_session(body: dict) -> JSONResponse:
    """
    Create a new agent session.

    Body:
        graph_id  (str)  – "support_agent" or "code_review"
        metadata  (dict) – optional, e.g. {"user_name": "Alice"}
    """
    graph_id = body.get("graph_id", "support_agent")
    if graph_id not in _graphs:
        raise HTTPException(status_code=400, detail=f"Unknown graph_id: {graph_id}")

    session_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    _sessions[session_id] = SessionRecord(
        session_id=session_id,
        graph_id=graph_id,
        thread_id=thread_id,
        metadata=body.get("metadata", {}),
    )
    log.info("Session created: %s graph=%s", session_id, graph_id)
    return JSONResponse({"id": session_id, "graph_id": graph_id, "status": "created"})


@router.post("/{session_id}/turns")
async def create_turn(session_id: str, body: dict) -> JSONResponse:
    """
    Send a user message to the agent.

    Body:
        input  (list)  – [{"role": "user", "content": "..."}]

    If the session has a pending interrupt, the user's content is used as the
    resume_value and the interrupted run is resumed instead of starting a new one.
    """
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Extract user content from the AAAS turn input format
    input_messages = body.get("input", [])
    user_content = ""
    for m in input_messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user_content = m.get("content", "")
            break

    if session.pending_run_id:
        # Resume the interrupted run
        pending = run_store.get(session.pending_run_id)
        if pending and pending.status == "interrupted":
            interrupt_type = (pending.interrupt_payload or {}).get("type", "")
            resume_value = _coerce_resume_value(user_content, interrupt_type)

            run_store.update_status(session.pending_run_id, "running")
            from langgraph.types import Command
            asyncio.create_task(
                _run_graph_background(
                    run_id=session.pending_run_id,
                    graph_id=pending.graph_id,
                    thread_id=pending.thread_id,
                    initial_input=Command(resume=resume_value),
                )
            )
            turn_id = session.pending_run_id
            session.pending_run_id = None
            log.info("Session %s resuming run %s", session_id, turn_id)
            return JSONResponse({"id": turn_id, "session_id": session_id, "status": "in_progress"})

    # Start a new run
    run_record = run_store.create(session.graph_id)
    # Override the thread_id so LangGraph uses the session's persistent thread
    run_record.thread_id = session.thread_id

    # Build graph input from user content + session metadata
    graph_input: dict[str, Any] = {**session.metadata}

    if "message" not in graph_input and "messages" not in graph_input:
        graph_input["messages"] = [HumanMessage(content=user_content)]
    elif "message" in graph_input:
        graph_input["messages"] = [HumanMessage(content=graph_input.pop("message"))]

    # For code_review, the snippet may be in the user message directly
    if session.graph_id == "code_review" and "code_snippet" not in graph_input:
        graph_input["code_snippet"] = user_content
        graph_input.setdefault("language", "unknown")

    asyncio.create_task(
        _run_graph_background(
            run_id=run_record.run_id,
            graph_id=run_record.graph_id,
            thread_id=run_record.thread_id,
            initial_input=graph_input,
        )
    )

    log.info("Session %s started run %s", session_id, run_record.run_id)
    return JSONResponse({
        "id": run_record.run_id,
        "session_id": session_id,
        "status": "in_progress",
    })


@router.get("/{session_id}/turns/{turn_id}")
async def get_turn(session_id: str, turn_id: str) -> JSONResponse:
    """
    Poll for the result of a turn (turn_id == run_id).

    Foundry calls this until status is "completed" or "failed".

    LangGraph status → AAAS status mapping:
        running     → in_progress
        interrupted → completed  (interrupt payload rendered as assistant reply)
        complete    → completed
        error       → failed
    """
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    record = run_store.get(turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Turn not found")

    if record.status == "running":
        return JSONResponse({"id": turn_id, "session_id": session_id, "status": "in_progress"})

    if record.status == "interrupted":
        payload = record.interrupt_payload or {}
        message = _interrupt_to_message(payload)
        # Mark session as having a pending interrupt to handle on the next turn
        session.pending_run_id = turn_id
        return JSONResponse({
            "id": turn_id,
            "session_id": session_id,
            "status": "completed",
            "interrupt_type": payload.get("type", "unknown"),
            "output": {
                "messages": [{"role": "assistant", "content": message}]
            },
        })

    if record.status == "complete":
        # Extract the last assistant message from the graph state
        content = _last_assistant_content(turn_id)
        return JSONResponse({
            "id": turn_id,
            "session_id": session_id,
            "status": "completed",
            "output": {
                "messages": [{"role": "assistant", "content": content}]
            },
        })

    # error
    return JSONResponse({
        "id": turn_id,
        "session_id": session_id,
        "status": "failed",
        "error": "Graph run encountered an error.",
    })


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> JSONResponse:
    """End a session and clean up its record."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    del _sessions[session_id]
    log.info("Session deleted: %s", session_id)
    return JSONResponse({"deleted": True})
