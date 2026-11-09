"""Application service: one function to run a campaign end to end."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from .agents.specialists import build_agents
from .config import Settings
from .experiments import assign_variant, variant_config
from .graph.builder import build_graph
from .graph.state import initial_state
from .llm.client import LLMClient, build_llm
from .observability import METRICS, RunTrace, configure_logging, log_event, store_trace
from .schemas import AgentResult, CampaignPlan, CampaignRequest, CampaignResponse
from .tools.catalog import CatalogTools
from .tools.repository import Repository

logger = logging.getLogger("ecom_agents.runner")


def run_campaign(
    request: CampaignRequest | dict[str, Any],
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    repository: Repository | None = None,
) -> CampaignResponse:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    req = request if isinstance(request, CampaignRequest) else CampaignRequest.model_validate(request)
    trace_id = uuid.uuid4().hex[:16]

    repo = repository or Repository(settings.db_path, settings.seed_path)
    tools = CatalogTools(repo)
    client = llm or build_llm(settings)
    agents = build_agents(client, tools, settings)

    variant = assign_variant(req.user_id, req.experiment_key, settings.ab_split, settings.ab_enabled)
    config = variant_config(variant)
    trace = RunTrace(trace_id=trace_id, variant=variant)
    graph = build_graph(settings, client, tools, repo, agents, trace)

    run_config = {"configurable": {"thread_id": trace_id}}
    started = time.perf_counter()
    METRICS.inc("campaign_runs_total")
    log_event(logger, "campaign_started", trace_id=trace_id, user_id=req.user_id, variant=variant, mode=settings.llm_provider)

    state = initial_state(req.model_dump(mode="json"), trace_id, variant, config)
    final_state = graph.invoke(state, config=run_config)

    if settings.require_approval:
        snapshot = graph.get_state(run_config)
        if snapshot.next:
            trace.event("approval: 自动放行（演示模式，生产环境应接入人工审批）")
            final_state = graph.invoke(None, config=run_config)

    plan = CampaignPlan.model_validate(final_state.get("plan") or {})
    results = [AgentResult.model_validate(item) for item in final_state.get("agent_results", [])]
    for item in results:
        if item.degraded:
            METRICS.inc("campaign_degraded_total")
    trace.events.extend(final_state.get("events", []))
    report = trace.finish()
    store_trace(report, settings.trace_store_size)
    METRICS.observe("campaign", (time.perf_counter() - started) * 1000)

    return CampaignResponse(
        trace_id=trace_id,
        variant=variant,
        plan=plan,
        agent_results=results,
        trace=report,
        mode="openai" if settings.uses_real_llm else "mock",
    )
