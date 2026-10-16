"""LangGraph workflow assembly."""

from langgraph.graph import END, START, StateGraph

from customer_agent.nodes import build_nodes
from customer_agent.state import SupportState


def build_graph(database):
    nodes = build_nodes(database)
    graph = StateGraph(SupportState)
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", lambda s: "answer" if s.get("needs_clarification") else "authorize_order",
                                {"answer": "answer", "authorize_order": "authorize_order"})
    graph.add_conditional_edges("authorize_order", lambda s: "answer" if s.get("order_access") in {"denied", "missing_order"} else "retrieve_memory",
                                {"answer": "answer", "retrieve_memory": "retrieve_memory"})
    graph.add_edge("retrieve_memory", "retrieve_policy")
    graph.add_edge("retrieve_policy", "answer")
    graph.add_edge("answer", END)
    return graph.compile()
