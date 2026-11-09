"""Supervisor: turns one campaign request into a staged execution plan.

A real LLM decides the plan; :func:`normalize` guards it so an untrusted model
can never route into an unknown node, drop mandatory stages, or create a graph
that cannot terminate. The compliance agent is intentionally not routable.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import DEFAULT_STAGES
from ..llm.client import LLMClient
from ..llm.mock import mock_result
from ..schemas import SupervisorDecision

logger = logging.getLogger("ecom_agents.supervisor")

ROUTABLE_AGENTS = ("profile", "recommendation", "inventory", "pricing", "marketing")


def normalize(decision: SupervisorDecision, variant: dict[str, Any]) -> SupervisorDecision:
    stages: list[list[str]] = []
    seen: set[str] = set()
    enable_pricing = bool(variant.get("enable_pricing", True))

    for raw_stage in decision.stages[:5]:
        stage: list[str] = []
        for name in raw_stage[:4]:
            key = str(name).strip().lower()
            if key not in ROUTABLE_AGENTS or key in seen:
                continue
            if key == "pricing" and not enable_pricing:
                continue
            stage.append(key)
            seen.add(key)
        if stage:
            stages.append(stage)

    if not stages:
        stages = [list(stage) for stage in DEFAULT_STAGES]
        seen = {name for stage in stages for name in stage}

    # Mandatory ordering: profile must be the sole first stage, marketing the sole last stage.
    if "profile" not in seen:
        stages.insert(0, ["profile"])
        seen.add("profile")
    elif stages[0] != ["profile"]:
        for stage in stages:
            if "profile" in stage:
                stage.remove("profile")
        stages = [s for s in stages if s]
        stages.insert(0, ["profile"])
    if "marketing" not in seen:
        stages.append(["marketing"])
    else:
        marketing_stages = [s for s in stages if "marketing" in s]
        if len(marketing_stages) != 1 or marketing_stages[0] is not stages[-1] or len(marketing_stages[0]) > 1:
            for stage in stages:
                if "marketing" in stage:
                    stage.remove("marketing")
            stages = [s for s in stages if s]
            stages.append(["marketing"])

    if not enable_pricing:
        stages = [[name for name in s if name != "pricing"] for s in stages]
        stages = [s for s in stages if s]

    skipped = [str(x) for x in decision.skipped]
    if not enable_pricing and "pricing" not in skipped:
        skipped.append("pricing")
    return SupervisorDecision(stages=stages, rationale=decision.rationale, skipped=skipped)


def decide(request: dict[str, Any], variant: dict[str, Any], llm: LLMClient) -> SupervisorDecision:
    payload = {
        "request": request,
        "variant": variant,
        "routable_agents": list(ROUTABLE_AGENTS),
        "constraints": {
            "profile_first": True,
            "marketing_last": True,
            "compliance_is_fixed": True,
            "max_stages": 5,
        },
    }
    try:
        response = llm.complete_json(intent="supervisor", payload=payload)
        decision = SupervisorDecision.model_validate(response.data)
    except Exception as exc:
        logger.warning("supervisor_fallback error=%s", exc)
        decision = SupervisorDecision.model_validate(mock_result("supervisor", payload))
        decision.rationale = f"LLM 规划不可用，回退默认编排：{type(exc).__name__}"
    return normalize(decision, variant)
