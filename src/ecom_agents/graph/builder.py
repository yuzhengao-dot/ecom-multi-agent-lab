"""Assemble the LangGraph state machine.

    START -> load_context -> plan --> [stage 0 agents in parallel] --> evaluate
                                  ... (one superstep per stage) ...
             evaluate -> synthesize -> compliance -> END
                                   ^        |
                                   +--------+  (one revision loop at most)

``Send`` is used for dynamic fan-out, so the supervisor decides parallelism at
runtime instead of the graph hard-coding a fixed pipeline.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ..agents.base import BaseAgent
from ..config import Settings
from ..llm.client import LLMClient
from ..observability import RunTrace
from ..tools.catalog import CatalogTools
from ..tools.repository import Repository
from .nodes import NodeSet
from .state import GraphState
from .supervisor import ROUTABLE_AGENTS


def build_graph(
    settings: Settings,
    llm: LLMClient,
    tools: CatalogTools,
    repository: Repository,
    agents: dict[str, BaseAgent],
    trace: RunTrace,
) -> Any:
    nodes = NodeSet(settings, llm, tools, repository, agents, trace)
    builder = StateGraph(GraphState)

    builder.add_node("load_context", nodes.load_context)
    builder.add_node("plan", nodes.plan)
    for name in ROUTABLE_AGENTS:
        builder.add_node(f"agent_{name}", nodes.agent_node(name))
    builder.add_node("evaluate", nodes.evaluate)
    builder.add_node("synthesize", nodes.synthesize)
    builder.add_node("compliance", nodes.compliance)

    path_map: dict[str, Any] = {f"agent_{name}": f"agent_{name}" for name in ROUTABLE_AGENTS}
    path_map["synthesize"] = "synthesize"

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "plan")
    builder.add_conditional_edges("plan", nodes.dispatch_initial, path_map)
    for name in ROUTABLE_AGENTS:
        builder.add_edge(f"agent_{name}", "evaluate")
    builder.add_conditional_edges("evaluate", nodes.dispatch_next, path_map)
    builder.add_edge("synthesize", "compliance")
    builder.add_conditional_edges(
        "compliance",
        nodes.after_compliance,
        {"synthesize": "synthesize", "end": END},
    )

    compile_kwargs: dict[str, Any] = {}
    if settings.require_approval:
        compile_kwargs["interrupt_before"] = ["compliance"]
    return builder.compile(checkpointer=MemorySaver(), **compile_kwargs)
