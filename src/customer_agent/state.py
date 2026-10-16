"""Typed state passed between the customer-support LangGraph nodes."""

from typing import Any, TypedDict

from langgraph.graph import MessagesState


class SupportState(MessagesState):
    user_id: str
    latest_query: str
    intent: str
    order_id: str | None
    order_access: str
    order_context: dict[str, Any]
    policy_evidence: list[dict[str, Any]]
    memory_context: str
    answer: str
    analysis: dict[str, Any]
    clarification_question: str
    answer_sources: list[dict[str, Any]]
    needs_clarification: bool
