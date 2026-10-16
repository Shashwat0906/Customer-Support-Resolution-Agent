"""LangGraph definition of the resolution workflow.

    intake -> locate_order -> load_order_facts -> (investigate || retrieve_policy) -> resolve -> decide
                         \\-> ask_customer -> await_customer (interrupt) -> intake
    decide -> propose -> await_approval (interrupt) -> execute_refund -> verify_refund -> respond -> update_ticket
    decide -> respond | ask_customer | escalate

In single-agent mode, ``single_agent`` replaces the investigate/retrieve_policy/resolve trio.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.workflows import nodes
from app.workflows.checkpointer import get_checkpointer
from app.workflows.state import ResolutionState

SEQUENTIAL_ROUTES = (
    "intake",
    "locate_order",
    "ask_customer",
    "await_customer",
    "load_order_facts",
    "single_agent",
    "decide",
    "propose",
    "await_approval",
    "execute_refund",
    "verify_refund",
    "escalate",
    "respond",
    "update_ticket",
)


def _route(state: ResolutionState) -> str | list[str]:
    if state.route == "fanout":
        return ["investigate", "retrieve_policy"]
    if state.route in (None, "end"):
        return END
    return state.route


def build_graph() -> StateGraph:
    g = StateGraph(ResolutionState)
    for name in (
        "intake",
        "locate_order",
        "ask_customer",
        "load_order_facts",
        "investigate",
        "retrieve_policy",
        "resolve",
        "single_agent",
        "decide",
        "propose",
        "execute_refund",
        "verify_refund",
        "escalate",
        "respond",
        "update_ticket",
    ):
        g.add_node(name, getattr(nodes, name))
    g.add_node("await_customer", nodes.await_customer)
    g.add_node("await_approval", nodes.await_approval)

    g.add_edge(START, "intake")
    targets = [*SEQUENTIAL_ROUTES, "investigate", "retrieve_policy", END]
    for name in SEQUENTIAL_ROUTES:
        g.add_conditional_edges(name, _route, targets)
    # Concurrent branches join before the resolution agent runs.
    g.add_edge(["investigate", "retrieve_policy"], "resolve")
    g.add_conditional_edges("resolve", _route, targets)
    return g


@lru_cache
def get_graph() -> CompiledStateGraph:
    return build_graph().compile(checkpointer=get_checkpointer())
