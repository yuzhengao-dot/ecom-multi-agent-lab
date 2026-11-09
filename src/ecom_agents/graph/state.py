"""LangGraph state schema.

Parallel branches only ever write to keys carrying an ``operator.add`` reducer
(``agent_results`` / ``events`` / ``errors``). Everything else is written by a
single node per superstep, which keeps the graph deterministic under fan-out.
"""

import operator
from typing import Annotated, Any, TypedDict


class GraphState(TypedDict):
    request: dict[str, Any]
    trace_id: str
    variant: str
    variant_config: dict[str, Any]

    stages: list[list[str]]
    stage_index: int
    pending_agents: list[str]
    retry_counts: dict[str, int]
    revisions: int
    next_action: str

    agent_results: Annotated[list[dict[str, Any]], operator.add]
    events: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]

    plan: dict[str, Any]
    compliance: dict[str, Any]


def initial_state(request: dict[str, Any], trace_id: str, variant: str, variant_config: dict[str, Any]) -> GraphState:
    return {
        "request": request,
        "trace_id": trace_id,
        "variant": variant,
        "variant_config": variant_config,
        "stages": [],
        "stage_index": 0,
        "pending_agents": [],
        "retry_counts": {},
        "revisions": 0,
        "next_action": "end",
        "agent_results": [],
        "events": [],
        "errors": [],
        "plan": {},
        "compliance": {},
    }


def result_index(state: GraphState) -> dict[str, dict[str, Any]]:
    """Latest result per agent (a retried agent may appear more than once)."""
    index: dict[str, dict[str, Any]] = {}
    for item in state.get("agent_results", []):
        if isinstance(item, dict) and item.get("agent"):
            index[str(item["agent"])] = item
    return index


def build_context(state: GraphState) -> dict[str, Any]:
    """Project graph state into the read-only context handed to an agent."""
    index = result_index(state)
    return {
        "request": state.get("request", {}),
        "variant": state.get("variant_config", {}),
        "variant_name": state.get("variant", "A"),
        "trace_id": state.get("trace_id", ""),
        "agent_data": {name: item.get("data", {}) for name, item in index.items()},
        "agent_results": index,
        "plan": state.get("plan", {}),
    }
