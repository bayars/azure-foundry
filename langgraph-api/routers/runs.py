from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from state.models import (
    FeedbackRequest,
    ResumeRunRequest,
    RunStateResponse,
    StartRunRequest,
    StartRunResponse,
)
from storage.run_store import run_store

log = logging.getLogger(__name__)

router = APIRouter()

# Graph registry — populated by main.py at startup
_graphs: dict[str, Any] = {}


def register_graphs(graphs: dict[str, Any]) -> None:
    _graphs.update(graphs)


# ── Background task ────────────────────────────────────────────────────────


async def _run_graph_background(
    run_id: str,
    graph_id: str,
    thread_id: str,
    initial_input: dict[str, Any],
) -> None:
    """Drive the graph forward, broadcasting SSE events until interrupt or end."""
    graph = _graphs.get(graph_id)
    if graph is None:
        run_store.update_status(run_id, "error")
        await run_store.broadcast(run_id, {"event": "error", "data": {"message": f"Unknown graph: {graph_id}"}})
        return

    config = {"configurable": {"thread_id": thread_id}}

    try:
        async for mode, chunk in graph.astream(
            initial_input, config, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                # chunk is a tuple: (message_chunk, metadata)
                msg_chunk, metadata = chunk
                content = getattr(msg_chunk, "content", "")
                if content:
                    await run_store.broadcast(
                        run_id,
                        {
                            "event": "token",
                            "data": {
                                "content": content,
                                "node": metadata.get("langgraph_node", ""),
                            },
                        },
                    )

            elif mode == "updates":
                # chunk is a dict: {node_name: state_delta}
                if "__interrupt__" in chunk:
                    interrupts = chunk["__interrupt__"]
                    payload = interrupts[0].value if interrupts else {}
                    run_store.update_status(run_id, "interrupted", payload)
                    await run_store.broadcast(
                        run_id,
                        {
                            "event": "interrupted",
                            "data": {
                                "run_id": run_id,
                                "interrupt_payload": payload,
                            },
                        },
                    )
                    # Stop streaming — wait for /resume
                    return
                else:
                    node_name = next(iter(chunk), "unknown")
                    await run_store.broadcast(
                        run_id,
                        {
                            "event": "node_update",
                            "data": {
                                "node": node_name,
                                "delta": _serialise(chunk.get(node_name, {})),
                            },
                        },
                    )

        # Graph completed normally
        run_store.update_status(run_id, "complete")
        await run_store.broadcast(
            run_id,
            {"event": "complete", "data": {"run_id": run_id}},
        )

    except Exception as exc:
        log.exception("Graph %s run %s failed", graph_id, run_id)
        run_store.update_status(run_id, "error")
        await run_store.broadcast(
            run_id,
            {"event": "error", "data": {"message": str(exc)}},
        )


def _serialise(obj: Any) -> Any:
    """Best-effort JSON-safe serialisation of state deltas."""
    if isinstance(obj, dict):
        return {k: _serialise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialise(i) for i in obj]
    if hasattr(obj, "content"):  # BaseMessage
        return {"role": getattr(obj, "type", "unknown"), "content": obj.content}
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post("", response_model=StartRunResponse)
async def start_run(request: StartRunRequest) -> StartRunResponse:
    """Start a new graph run and return the run_id immediately."""
    if request.graph_id not in _graphs:
        raise HTTPException(status_code=400, detail=f"Unknown graph_id: {request.graph_id}")

    record = run_store.create(request.graph_id)

    # Coerce plain string message into HumanMessage for the graph
    raw_input = dict(request.input)
    if "message" in raw_input and isinstance(raw_input["message"], str):
        raw_input["messages"] = [HumanMessage(content=raw_input.pop("message"))]

    asyncio.create_task(
        _run_graph_background(
            run_id=record.run_id,
            graph_id=record.graph_id,
            thread_id=record.thread_id,
            initial_input=raw_input,
        )
    )

    return StartRunResponse(
        run_id=record.run_id,
        thread_id=record.thread_id,
        graph_id=record.graph_id,
    )


@router.get("/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    """SSE stream for a run. Stays open until 'complete' or 'error' event."""
    record = run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    q = run_store.subscribe(run_id)

    async def event_generator():
        try:
            while True:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("event") in ("complete", "error"):
                    break
                # After an interrupt the background task exits; drain remaining
                if event.get("event") == "interrupted":
                    # Yield the interrupt event and close — client will poll /state
                    break
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
        finally:
            run_store.unsubscribe(run_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, body: ResumeRunRequest) -> dict:
    """Resume a paused (interrupted) run with the provided value."""
    record = run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if record.status != "interrupted":
        raise HTTPException(
            status_code=409,
            detail=f"Run is not interrupted (current status: {record.status})",
        )

    run_store.update_status(run_id, "running")

    config = {"configurable": {"thread_id": record.thread_id}}

    asyncio.create_task(
        _run_graph_background(
            run_id=run_id,
            graph_id=record.graph_id,
            thread_id=record.thread_id,
            initial_input=Command(resume=body.resume_value),
        )
    )

    return {"run_id": run_id, "status": "running"}


@router.get("/{run_id}/state", response_model=RunStateResponse)
async def get_run_state(run_id: str) -> RunStateResponse:
    """Return the latest graph state snapshot and run status."""
    record = run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    graph = _graphs.get(record.graph_id)
    config = {"configurable": {"thread_id": record.thread_id}}

    state_values: dict = {}
    next_nodes: list[str] = []

    if graph:
        try:
            snapshot = await graph.aget_state(config)
            if snapshot:
                raw = snapshot.values or {}
                state_values = _serialise(raw)
                next_nodes = list(snapshot.next) if snapshot.next else []
        except Exception as exc:
            log.warning("Could not fetch state for run %s: %s", run_id, exc)

    return RunStateResponse(
        run_id=run_id,
        thread_id=record.thread_id,
        graph_id=record.graph_id,
        status=record.status,
        interrupt_payload=record.interrupt_payload,
        state_values=state_values,
        next_nodes=next_nodes,
    )


@router.post("/{run_id}/feedback")
async def submit_feedback(run_id: str, body: FeedbackRequest) -> dict:
    """
    Placeholder for LangSmith run feedback.
    Wire up langsmith.Client().create_feedback() when LANGCHAIN_API_KEY is set.
    """
    record = run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    log.info(
        "Feedback for run %s: key=%s score=%.2f comment=%s",
        run_id,
        body.key,
        body.score,
        body.comment,
    )
    return {"run_id": run_id, "status": "feedback_recorded"}
