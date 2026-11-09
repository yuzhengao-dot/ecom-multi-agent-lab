from __future__ import annotations

import pytest

from ecom_agents.agents.specialists import build_agents
from ecom_agents.experiments import variant_config
from ecom_agents.llm.client import MockLLM
from ecom_agents.schemas import CampaignRequest
from ecom_agents.tools.catalog import CatalogTools
from tests.conftest import FailingLLM


def context(tools: CatalogTools, **overrides):
    base = {
        "request": CampaignRequest(user_id="U1001", scene="618大促", budget=500).model_dump(mode="json"),
        "variant": variant_config("B"),
        "variant_name": "B",
        "trace_id": "test-trace",
        "agent_data": {},
        "agent_results": {},
        "plan": {},
    }
    base.update(overrides)
    return base


def test_agents_produce_typed_results(settings, tools):
    agents = build_agents(MockLLM(), tools, settings)
    for name in ("profile", "recommendation", "inventory", "pricing", "marketing", "compliance"):
        result = agents[name].run(context(tools))
        assert result.agent == name
        assert result.ok is True
        assert result.degraded is False
        assert result.data
        assert result.tool_calls, f"{name} should record tool calls"


def test_profile_segments_high_value_and_dormant(settings, tools):
    profile = build_agents(MockLLM(), tools, settings)["profile"]
    assert profile.run(context(tools)).data["segment"] == "高价值"
    dormant = context(tools, request=CampaignRequest(user_id="U1003").model_dump(mode="json"))
    assert profile.run(dormant).data["segment"] == "沉睡待召回"


def test_recommendation_excludes_out_of_stock(settings, tools):
    result = build_agents(MockLLM(), tools, settings)["recommendation"].run(context(tools))
    assert "SKU-1005" in result.data["excluded_skus"]
    assert all(item["sku"] != "SKU-1005" for item in result.data["items"])


def test_inventory_flags_low_stock(settings, tools):
    result = build_agents(MockLLM(), tools, settings)["inventory"].run(context(tools))
    assert "SKU-1005" in result.data["risky_skus"]
    assert result.data["sellable_skus"]


def test_pricing_respects_discount_cap(settings, tools):
    agents = build_agents(MockLLM(), tools, settings)
    for variant_name in ("A", "B"):
        cfg = variant_config(variant_name)
        result = agents["pricing"].run(context(tools, variant=cfg))
        assert 0 <= result.data["discount_rate"] <= cfg["discount_cap"]
        assert result.data["expected_margin"] >= 0


def test_compliance_rejects_banned_claims(settings, tools):
    plan = {"headline": "全网最低价", "body": "绝对第一品牌，100%有效", "cta": "下单"}
    result = build_agents(MockLLM(), tools, settings)["compliance"].run(context(tools, plan=plan))
    assert result.data["verdict"] == "revise"
    assert result.data["issues"]


@pytest.mark.parametrize("name", ["profile", "recommendation", "inventory", "pricing", "marketing", "compliance"])
def test_llm_outage_falls_back_to_rules(settings, tools, name):
    agents = build_agents(FailingLLM(), tools, settings)
    result = agents[name].run(context(tools))
    assert result.degraded is True
    assert result.ok is True
    assert result.error and "outage" in result.error
    assert result.data.get("reasoning")
