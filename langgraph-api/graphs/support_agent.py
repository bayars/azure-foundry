"""
Support Agent Graph
===================
Demonstrates:
  • Context-gathering loop (clarification)
  • Human-in-the-loop interrupt (escalation approval)
  • Conditional edges with loop guard

Node flow:
  START → greet_and_collect → analyze_issue
            ↑        [unclear, attempts < 3]↓
            └───── request_clarification ←──┘
          [clear]
            ↓
        assess_severity → escalation_check
                            │ [low/medium]
                       notify_no_escalation → END
                            │ [high/critical] → INTERRUPT (escalation_approval)
                                │ [approved=True] → create_ticket → END
                                │ [approved=False] → notify_no_escalation → END
"""

from __future__ import annotations

import uuid
from typing import Literal

import os

from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from state.models import SupportAgentState

llm = AzureChatOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    temperature=0,
)


# ── Nodes ──────────────────────────────────────────────────────────────────


def greet_and_collect(state: SupportAgentState) -> dict:
    """Extract user_name and initial issue_description from the first message."""
    messages = state.get("messages", [])
    user_name = state.get("user_name", "User")

    # Build a prompt to extract the issue description
    system = SystemMessage(
        content=(
            "You are a helpful support agent. "
            "Greet the user warmly and acknowledge their issue in 1-2 sentences. "
            "Do NOT ask follow-up questions yet."
        )
    )
    user_msg = HumanMessage(
        content=f"User '{user_name}' says: {messages[-1].content if messages else 'I need help.'}"
    )
    response = llm.invoke([system, user_msg])

    # Use the last human message as the issue description
    issue_description = messages[-1].content if messages else ""

    return {
        "messages": [response],
        "issue_description": issue_description,
        "clarification_attempts": 0,
        "needs_clarification": False,
        "severity": "unknown",
    }


def analyze_issue(state: SupportAgentState) -> dict:
    """LLM determines whether the issue description is clear enough to act on."""
    issue = state.get("issue_description", "")
    attempts = state.get("clarification_attempts", 0)

    system = SystemMessage(
        content=(
            "You are a support triage analyst. "
            "Given an issue description, decide if it contains enough detail "
            "(specific error, steps to reproduce, affected component) to proceed. "
            "Reply with JSON only: "
            '{"needs_clarification": true/false, "reason": "..."}'
        )
    )
    user_msg = HumanMessage(content=f"Issue: {issue}")
    response = llm.invoke([system, user_msg])

    # Parse LLM response — be lenient
    import json, re
    text = response.content
    needs = True  # default: ask for clarification
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group())
            needs = bool(data.get("needs_clarification", True))
    except Exception:
        pass

    # Loop guard: after 3 attempts, force proceed
    if attempts >= 3:
        needs = False

    return {"needs_clarification": needs, "messages": [response]}


def request_clarification(state: SupportAgentState) -> dict:
    """
    Interrupt the graph and ask the human for more detail.
    The resumed value becomes the new issue_description.
    """
    issue = state.get("issue_description", "")
    attempts = state.get("clarification_attempts", 0)

    system = SystemMessage(
        content=(
            "You are a support agent. Formulate one clear, specific question "
            "to gather the missing information needed to resolve this issue. "
            "Be concise — one sentence only."
        )
    )
    user_msg = HumanMessage(content=f"Current description: {issue}")
    q_response = llm.invoke([system, user_msg])
    question = q_response.content.strip()

    # Pause here — resume_value will be the user's clarification text
    user_clarification: str = interrupt(
        {
            "type": "clarification_needed",
            "question": question,
            "attempt": attempts + 1,
        }
    )

    return {
        "messages": [q_response, HumanMessage(content=user_clarification)],
        "issue_description": user_clarification,
        "clarification_attempts": attempts + 1,
    }


def assess_severity(state: SupportAgentState) -> dict:
    """LLM assigns a severity level to the clarified issue."""
    issue = state.get("issue_description", "")
    system = SystemMessage(
        content=(
            "You are a support triage specialist. "
            "Rate the severity of this issue as exactly one of: low, medium, high, critical. "
            "Reply with JSON only: "
            '{"severity": "low|medium|high|critical", "reason": "..."}'
        )
    )
    user_msg = HumanMessage(content=f"Issue: {issue}")
    response = llm.invoke([system, user_msg])

    import json, re
    severity = "medium"
    try:
        m = re.search(r"\{.*\}", response.content, re.DOTALL)
        if m:
            data = json.loads(m.group())
            severity = data.get("severity", "medium")
    except Exception:
        pass

    return {"severity": severity, "messages": [response]}


def escalation_check(state: SupportAgentState) -> dict:
    """
    For high/critical issues, interrupt and ask a human to approve escalation.
    The resumed value (bool) becomes escalation_approved.
    """
    severity = state.get("severity", "medium")
    issue = state.get("issue_description", "")
    user_name = state.get("user_name", "User")

    if severity in ("high", "critical"):
        summary = (
            f"User '{user_name}' has a {severity.upper()} severity issue:\n{issue}\n\n"
            "Do you approve escalation to the on-call engineering team?"
        )
        approved: bool = interrupt(
            {
                "type": "escalation_approval",
                "severity": severity,
                "summary": summary,
                "user_name": user_name,
            }
        )
        return {"escalation_approved": approved}

    # Low/medium: no interrupt needed
    return {"escalation_approved": False}


def create_ticket(state: SupportAgentState) -> dict:
    """Create a support ticket (simulated)."""
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    user_name = state.get("user_name", "User")
    severity = state.get("severity", "medium")
    issue = state.get("issue_description", "")

    msg = HumanMessage(
        content=(
            f"Ticket {ticket_id} created for {user_name}. "
            f"Severity: {severity}. "
            f"Issue: {issue[:120]}..."
        )
    )
    return {"ticket_id": ticket_id, "messages": [msg]}


def notify_no_escalation(state: SupportAgentState) -> dict:
    """Inform the user that no escalation was approved."""
    user_name = state.get("user_name", "User")
    severity = state.get("severity", "medium")
    approved = state.get("escalation_approved", False)

    if approved is False and severity in ("high", "critical"):
        content = (
            f"Hi {user_name}, escalation for your {severity} issue was not approved at this time. "
            "Your case has been logged and will be reviewed in the next business day."
        )
    else:
        content = (
            f"Hi {user_name}, your issue has been reviewed. "
            f"Severity: {severity}. Our team will follow up within the standard SLA window."
        )
    return {"messages": [HumanMessage(content=content)]}


# ── Conditional edge functions ─────────────────────────────────────────────


def needs_clarification_router(
    state: SupportAgentState,
) -> Literal["request_clarification", "assess_severity"]:
    if state.get("needs_clarification", False):
        return "request_clarification"
    return "assess_severity"


def escalation_router(
    state: SupportAgentState,
) -> Literal["create_ticket", "notify_no_escalation"]:
    severity = state.get("severity", "medium")
    approved = state.get("escalation_approved")

    if severity in ("high", "critical") and approved is True:
        return "create_ticket"
    return "notify_no_escalation"


# ── Graph builder ──────────────────────────────────────────────────────────


def build_support_agent_graph(checkpointer: MemorySaver) -> StateGraph:
    builder = StateGraph(SupportAgentState)

    builder.add_node("greet_and_collect", greet_and_collect)
    builder.add_node("analyze_issue", analyze_issue)
    builder.add_node("request_clarification", request_clarification)
    builder.add_node("assess_severity", assess_severity)
    builder.add_node("escalation_check", escalation_check)
    builder.add_node("create_ticket", create_ticket)
    builder.add_node("notify_no_escalation", notify_no_escalation)

    builder.add_edge(START, "greet_and_collect")
    builder.add_edge("greet_and_collect", "analyze_issue")
    builder.add_conditional_edges(
        "analyze_issue",
        needs_clarification_router,
        {
            "request_clarification": "request_clarification",
            "assess_severity": "assess_severity",
        },
    )
    # Loop: after clarification, re-analyze
    builder.add_edge("request_clarification", "analyze_issue")
    builder.add_edge("assess_severity", "escalation_check")
    builder.add_conditional_edges(
        "escalation_check",
        escalation_router,
        {
            "create_ticket": "create_ticket",
            "notify_no_escalation": "notify_no_escalation",
        },
    )
    builder.add_edge("create_ticket", END)
    builder.add_edge("notify_no_escalation", END)

    return builder.compile(checkpointer=checkpointer)
