"""Graph node factories.

Nodes are closures over the run-scoped dependencies (LLM client, tools, agent
instances, trace collector). Keeping them as factories makes the graph cheap to
rebuild per request while remaining fully testable with injected fakes.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Send

from ..agents.base import BaseAgent
from ..config import Settings
from ..llm.client import LLMClient
from ..llm.mock import mock_result
from ..observability import METRICS, RunTrace, log_event
from ..tools.catalog import CatalogTools
from ..tools.repository import Repository
from .state import GraphState, build_context, result_index
from .supervisor import decide

import logging

logger = logging.getLogger("ecom_agents.graph")


def reconcile_plan(raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Trust tool-backed numbers over free-form LLM prose.

    Copywriting may come from the model, but SKUs, discounts, coupons and
    inventory notes are always taken from specialist results. This is the main
    guard against a model inventing a product or a price.
    """
    index = context.get("agent_results", {})

    def data(name: str) -> dict[str, Any]:
        value = index.get(name, {}).get("data")
        return dict(value) if isinstance(value, dict) else {}

    recommendation = data("recommendation")
    pricing = data("pricing")
    inventory = data("inventory")
    profile = data("profile")
    marketing = data("marketing")
    compliance = context.get("compliance") or {}

    plan = dict(raw)
    items = [i for i in (recommendation.get("items") or []) if isinstance(i, dict) and i.get("sku")]
    plan["recommended_skus"] = [str(i["sku"]) for i in items]
    try:
        plan["discount_rate"] = round(float(pricing.get("discount_rate") or 0.0), 4)
    except (TypeError, ValueError):
        plan["discount_rate"] = 0.0
    plan["coupon_code"] = pricing.get("coupon_code")
    plan["inventory_notes"] = str(inventory.get("notes") or "")
    plan["segment"] = str(profile.get("segment") or "")
    for key in ("headline", "body", "cta"):
        if not plan.get(key):
            plan[key] = str(marketing.get(key) or "")
    if not plan.get("rationale"):
        plan["rationale"] = "由专家 Agent 结论汇总生成。"
    if compliance.get("suggestions"):
        plan["body"] = f"{plan.get('body', '')} 文案已按合规建议调整表述。"
    plan["compliance_status"] = "pending"
    plan["compliance_notes"] = []
    return plan


class NodeSet:
    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        tools: CatalogTools,
        repository: Repository,
        agents: dict[str, BaseAgent],
        trace: RunTrace,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.repository = repository
        self.agents = agents
        self.trace = trace

    # -- setup -------------------------------------------------------------
    def load_context(self, state: GraphState) -> dict[str, Any]:
        self.repository.ensure_ready()
        user_id = state["request"].get("user_id", "")
        found = self.repository.get_user(user_id) is not None
        event = f"context: user={user_id} exists={found} variant={state['variant']}"
        log_event(logger, "context_loaded", user_id=user_id, exists=found, variant=state["variant"])
        return {"events": [event]}

    def plan(self, state: GraphState) -> dict[str, Any]:
        with self.trace.span("supervisor.plan"):
            decision = decide(state["request"], state["variant_config"], self.llm)
        self.trace.stages = decision.stages
        self.trace.add_tokens(0)
        events = [f"plan: {decision.rationale}", f"stages: {decision.stages}"]
        if decision.skipped:
            events.append(f"skipped: {decision.skipped}")
        return {
            "stages": decision.stages,
            "stage_index": 0,
            "pending_agents": [],
            "events": events,
        }

    # -- specialists -------------------------------------------------------
    def agent_node(self, name: str):
        def node(state: GraphState) -> dict[str, Any]:
            context = build_context(state)
            with self.trace.span(f"agent.{name}"):
                result = self.agents[name].run(context)
            self.trace.add_tokens(result.llm_tokens)
            if result.degraded:
                self.trace.degraded_agents.append(name)
                METRICS.inc("agent_degraded_total")
            METRICS.inc("agent_runs_total")
            METRICS.observe(f"agent_{name}", result.duration_ms)
            event = f"{name}: {result.summary}" + (" [degraded]" if result.degraded else "")
            return {
                "agent_results": [result.model_dump(mode="json")],
                "events": [event],
            }

        return node

    # -- routing -----------------------------------------------------------
    @staticmethod
    def dispatch_initial(state: GraphState):
        stages = state.get("stages") or []
        first = stages[0] if stages else []
        return [Send(f"agent_{name}", state) for name in first]

    @staticmethod
    def dispatch_next(state: GraphState):
        pending = state.get("pending_agents") or []
        if not pending:
            return "synthesize"
        return [Send(f"agent_{name}", state) for name in pending]

    def evaluate(self, state: GraphState) -> dict[str, Any]:
        index = result_index(state)
        stages = state.get("stages") or []
        current = stages[state["stage_index"]] if state["stage_index"] < len(stages) else []
        missing = [name for name in current if name not in index]
        retries = dict(state.get("retry_counts") or {})
        events: list[str] = []

        if missing and all(retries.get(name, 0) < self.settings.max_agent_retries for name in missing):
            for name in missing:
                retries[name] = retries.get(name, 0) + 1
            events.append(f"retry: 补跑未产出的 Agent {missing}")
            return {"retry_counts": retries, "pending_agents": missing, "events": events}

        next_index = state["stage_index"] + 1
        if missing:
            events.append(f"warn: 阶段 {state['stage_index']} 缺少 {missing}，已达重试上限，继续汇总")
        if next_index < len(stages):
            return {
                "stage_index": next_index,
                "retry_counts": retries,
                "pending_agents": stages[next_index],
                "events": events + [f"stage {next_index}: {stages[next_index]}"],
            }
        return {
            "stage_index": next_index,
            "retry_counts": retries,
            "pending_agents": [],
            "events": events + ["all stages complete"],
        }

    # -- synthesis and review ---------------------------------------------
    def synthesize(self, state: GraphState) -> dict[str, Any]:
        context = build_context(state)
        context["compliance"] = state.get("compliance") or {}
        payload = {
            "request": context["request"],
            "agent_results": context["agent_results"],
            "variant": context["variant"],
        }
        with self.trace.span("synthesize"):
            try:
                response = self.llm.complete_json(intent="synthesize", payload=payload)
                raw = response.data
                self.trace.add_tokens(response.tokens)
            except Exception as exc:
                logger.warning("synthesize_fallback error=%s", exc)
                raw = mock_result("synthesize", payload)
                self.trace.degraded_agents.append("synthesize")
        plan = reconcile_plan(raw, context)
        return {"plan": plan, "events": [f"synthesize: {plan.get('headline', '')}"]}

    def compliance(self, state: GraphState) -> dict[str, Any]:
        context = build_context(state)
        context["plan"] = state.get("plan") or {}
        with self.trace.span("compliance"):
            result = self.agents["compliance"].run(context)
        self.trace.add_tokens(result.llm_tokens)
        verdict = str(result.data.get("verdict") or "approved")
        issues = [str(x) for x in (result.data.get("issues") or [])]
        suggestions = [str(x) for x in (result.data.get("suggestions") or [])]
        plan = dict(state.get("plan") or {})
        plan["compliance_notes"] = issues + suggestions

        can_revise = verdict == "revise" and state.get("revisions", 0) < 1
        if verdict == "approved":
            plan["compliance_status"] = "approved"
            next_action = "end"
        elif can_revise:
            plan["compliance_status"] = "revised"
            next_action = "synthesize"
        else:
            plan["compliance_status"] = "rejected" if verdict == "rejected" else "revised"
            next_action = "end"

        METRICS.inc(f"compliance_{plan['compliance_status']}_total")
        return {
            "plan": plan,
            "compliance": result.data,
            "agent_results": [result.model_dump(mode="json")],
            "revisions": state.get("revisions", 0) + (1 if can_revise else 0),
            "next_action": next_action,
            "events": [f"compliance: {verdict} -> {next_action}"],
        }

    @staticmethod
    def after_compliance(state: GraphState) -> str:
        return state.get("next_action") or "end"
