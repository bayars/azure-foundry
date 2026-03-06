"""
Code Review Graph
=================
Demonstrates:
  • Re-review loop with counter guard
  • Single interrupt with multiple outcome paths (accept / reject / re_review)

Node flow:
  START → collect_context → generate_review → await_user_decision
                                  ↑                    │ [re_review, count ≤ 3]
                                  └────────────────────┘
                                            │ [accept] → finalize_review → END
                                            │ [reject] → notify_rejected  → END
"""

from __future__ import annotations

from typing import Literal

import os

from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from state.models import CodeReviewState

llm = AzureChatOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    temperature=0,
)

MAX_RE_REVIEWS = 3


# ── Nodes ──────────────────────────────────────────────────────────────────


def collect_context(state: CodeReviewState) -> dict:
    """Acknowledge the code snippet and language to review."""
    snippet = state.get("code_snippet", "")
    language = state.get("language", "unknown")

    msg = HumanMessage(
        content=f"Starting code review for {language} snippet ({len(snippet)} chars)."
    )
    return {
        "messages": [msg],
        "re_review_count": state.get("re_review_count", 0),
        "user_decision": "",
    }


def generate_review(state: CodeReviewState) -> dict:
    """LLM performs a detailed code review."""
    snippet = state.get("code_snippet", "")
    language = state.get("language", "python")
    re_review_count = state.get("re_review_count", 0)

    extra = ""
    if re_review_count > 0:
        extra = f"\n\nThis is re-review #{re_review_count}. Focus on any remaining issues."

    system = SystemMessage(
        content=(
            f"You are an expert {language} code reviewer. "
            "Review the following code for: correctness, edge cases, security, "
            "performance, and readability. "
            "Structure your review with clear sections and actionable suggestions."
            + extra
        )
    )
    user_msg = HumanMessage(content=f"```{language}\n{snippet}\n```")
    response = llm.invoke([system, user_msg])

    return {"review_output": response.content, "messages": [response]}


def await_user_decision(state: CodeReviewState) -> dict:
    """
    Interrupt and present the review to a human.
    They may accept, reject, or request a re-review.
    """
    review = state.get("review_output", "")
    re_review_count = state.get("re_review_count", 0)

    options = ["accept", "reject", "re_review"]
    if re_review_count >= MAX_RE_REVIEWS:
        options = ["accept", "reject"]  # no more re-reviews

    decision: str = interrupt(
        {
            "type": "review_decision",
            "review": review,
            "re_review_count": re_review_count,
            "options": options,
            "prompt": "What would you like to do with this review?",
        }
    )

    return {"user_decision": decision.strip().lower()}


def finalize_review(state: CodeReviewState) -> dict:
    """User accepted — confirm and record the accepted review."""
    snippet = state.get("code_snippet", "")
    language = state.get("language", "python")
    re_review_count = state.get("re_review_count", 0)

    msg = HumanMessage(
        content=(
            f"Review accepted for {language} snippet. "
            f"Re-reviews performed: {re_review_count}. "
            "Review has been finalized and recorded."
        )
    )
    return {"messages": [msg]}


def notify_rejected(state: CodeReviewState) -> dict:
    """User rejected the review — no action taken."""
    language = state.get("language", "python")
    msg = HumanMessage(
        content=(
            f"Review rejected for {language} snippet. "
            "No changes will be enforced. The review has been discarded."
        )
    )
    return {"messages": [msg]}


# ── Conditional edge functions ─────────────────────────────────────────────


def decision_router(
    state: CodeReviewState,
) -> Literal["generate_review", "finalize_review", "notify_rejected"]:
    decision = state.get("user_decision", "").lower()
    re_review_count = state.get("re_review_count", 0)

    if decision == "re_review" and re_review_count < MAX_RE_REVIEWS:
        return "generate_review"
    if decision == "accept":
        return "finalize_review"
    # reject, or re_review exhausted
    return "notify_rejected"


def increment_re_review(state: CodeReviewState) -> dict:
    """Increment the re-review counter before looping back."""
    return {"re_review_count": state.get("re_review_count", 0) + 1}


# ── Graph builder ──────────────────────────────────────────────────────────


def build_code_review_graph(checkpointer: MemorySaver) -> StateGraph:
    builder = StateGraph(CodeReviewState)

    builder.add_node("collect_context", collect_context)
    builder.add_node("generate_review", generate_review)
    builder.add_node("await_user_decision", await_user_decision)
    builder.add_node("increment_re_review", increment_re_review)
    builder.add_node("finalize_review", finalize_review)
    builder.add_node("notify_rejected", notify_rejected)

    builder.add_edge(START, "collect_context")
    builder.add_edge("collect_context", "generate_review")
    builder.add_edge("generate_review", "await_user_decision")
    builder.add_conditional_edges(
        "await_user_decision",
        decision_router,
        {
            "generate_review": "increment_re_review",
            "finalize_review": "finalize_review",
            "notify_rejected": "notify_rejected",
        },
    )
    builder.add_edge("increment_re_review", "generate_review")
    builder.add_edge("finalize_review", END)
    builder.add_edge("notify_rejected", END)

    return builder.compile(checkpointer=checkpointer)
