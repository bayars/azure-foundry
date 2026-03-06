from __future__ import annotations

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


# ──────────────────────────────────────────────
# Graph States (TypedDicts consumed by LangGraph)
# ──────────────────────────────────────────────

class SupportAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_name: str
    issue_description: str
    severity: str                    # "low" | "medium" | "high" | "critical"
    clarification_attempts: int
    needs_clarification: bool
    escalation_approved: Optional[bool]
    ticket_id: Optional[str]


class CodeReviewState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    code_snippet: str
    language: str
    review_output: str
    user_decision: str               # "accept" | "reject" | "re_review"
    re_review_count: int


# ──────────────────────────────────────────────
# API Request / Response Pydantic Models
# ──────────────────────────────────────────────

class StartRunRequest(BaseModel):
    graph_id: str = Field(..., description="'support_agent' or 'code_review'")
    input: dict[str, Any] = Field(..., description="Initial state fields for the graph")


class StartRunResponse(BaseModel):
    run_id: str
    thread_id: str
    graph_id: str
    status: str = "running"


class ResumeRunRequest(BaseModel):
    resume_value: Any = Field(..., description="Value to resume the interrupted graph with")


class RunStateResponse(BaseModel):
    run_id: str
    thread_id: str
    graph_id: str
    status: str
    interrupt_payload: Optional[dict[str, Any]] = None
    state_values: Optional[dict[str, Any]] = None
    next_nodes: list[str] = []


class FeedbackRequest(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    comment: Optional[str] = None
    key: str = "user_feedback"
