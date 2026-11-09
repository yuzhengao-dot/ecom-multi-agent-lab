from __future__ import annotations

from ecom_agents.experiments import assign_variant
from ecom_agents.graph.supervisor import normalize
from ecom_agents.runner import run_campaign
from ecom_agents.schemas import CampaignRequest, SupervisorDecision
from tests.conftest import FailingLLM, HallucinatingLLM, ScriptedLLM


def test_normalize_sanitizes_untrusted_plans():
    decision = SupervisorDecision(
        stages=[["unknown_agent", "marketing"], ["profile", "profile", "inventory"]],
        rationale="bad plan",
    )
    result = normalize(decision, {"enable_pricing": True})
    assert result.stages[0] == ["profile"]
    assert result.stages[-1] == ["marketing"]
    assert all(name in {"profile", "recommendation", "inventory", "pricing", "marketing"}
               for stage in result.stages for name in stage)
    assert "compliance" not in {name for stage in result.stages for name in stage}


def test_normalize_removes_pricing_for_control_group():
    decision = SupervisorDecision(stages=[["profile"], ["recommendation", "inventory", "pricing"], ["marketing"]])
    result = normalize(decision, {"enable_pricing": False})
    assert all("pricing" not in stage for stage in result.stages)
    assert "pricing" in result.skipped


def test_run_campaign_end_to_end(settings, repository):
    response = run_campaign(
        CampaignRequest(user_id="U1001", scene="618大促", budget=500),
        settings,
        repository=repository,
    )
    assert response.mode == "mock"
    assert response.plan.compliance_status == "approved"
    assert response.plan.recommended_skus
    assert response.plan.segment
    assert response.trace.spans
    assert response.trace.stages
    assert {r.agent for r in response.agent_results} >= {"profile", "recommendation", "inventory", "marketing", "compliance"}


def test_ab_variant_changes_agent_routing(settings, repository):
    user_a = next(f"u{i}" for i in range(50) if assign_variant(f"u{i}", "e", settings.ab_split) == "A")
    user_b = next(f"u{i}" for i in range(50) if assign_variant(f"u{i}", "e", settings.ab_split) == "B")
    res_a = run_campaign(CampaignRequest(user_id=user_a, experiment_key="e"), settings, repository=repository)
    res_b = run_campaign(CampaignRequest(user_id=user_b, experiment_key="e"), settings, repository=repository)
    assert res_a.variant == "A" and res_b.variant == "B"
    assert "pricing" not in {r.agent for r in res_a.agent_results}
    assert "pricing" in {r.agent for r in res_b.agent_results}
    assert res_b.plan.discount_rate >= res_a.plan.discount_rate


def test_full_llm_outage_still_delivers_a_plan(settings, repository):
    response = run_campaign(
        CampaignRequest(user_id="U1002", budget=300),
        settings,
        llm=FailingLLM(),
        repository=repository,
    )
    assert response.plan.compliance_status == "approved"
    assert response.trace.degraded_agents
    assert all(r.degraded for r in response.agent_results)


def test_reconciliation_blocks_hallucinated_numbers(settings, repository):
    response = run_campaign(
        CampaignRequest(user_id="U1001", budget=500),
        settings,
        llm=HallucinatingLLM(),
        repository=repository,
    )
    assert "SKU-DOES-NOT-EXIST" not in response.plan.recommended_skus
    assert response.plan.recommended_skus
    assert response.plan.discount_rate < 0.99
    assert response.plan.compliance_status != "approved"


def test_revision_loop_is_bounded(settings, repository):
    llm = ScriptedLLM({"compliance": {"verdict": "revise", "issues": ["测试"], "suggestions": ["调整"], "reasoning": "test"}})
    response = run_campaign(
        CampaignRequest(user_id="U1001"),
        settings,
        llm=llm,
        repository=repository,
    )
    assert response.plan.compliance_status in {"revised", "rejected"}
    assert llm.calls.count("synthesize") <= 2
