from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import MemorySaver

from graphs.code_review import build_code_review_graph
from graphs.support_agent import build_support_agent_graph
from routers.runs import register_graphs, router as runs_router
from routers.sessions import router as sessions_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# When deployed to Azure Container Apps, APP_URL is injected as an env var.
# Setting it here embeds the correct server URL in the generated /openapi.json
# so Foundry tool registration can use the live spec directly.
_app_url = os.environ.get("APP_URL", "")
_servers = [{"url": _app_url}] if _app_url else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise shared checkpointer and pre-build both graphs."""
    log.info("Initialising LangGraph graphs…")
    checkpointer = MemorySaver()

    graphs = {
        "support_agent": build_support_agent_graph(checkpointer),
        "code_review": build_code_review_graph(checkpointer),
    }
    register_graphs(graphs)

    log.info("Graphs ready: %s", list(graphs.keys()))
    yield
    log.info("Shutdown.")


app = FastAPI(
    title="LangGraph Demo API",
    description="SSE-streaming LangGraph runs with human-in-the-loop interrupts",
    version="1.0.0",
    lifespan=lifespan,
    servers=_servers,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs_router, prefix="/runs", tags=["runs"])
app.include_router(sessions_router, prefix="/sessions", tags=["sessions (AAAS)"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "graphs": ["support_agent", "code_review"]}


@app.get("/graphs")
async def list_graphs() -> dict:
    """Return the available graph IDs. Used by Foundry tool callers to know valid graph_id values."""
    return {"graphs": ["support_agent", "code_review"]}
