from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ecom_agents.app import create_app
from ecom_agents.config import Settings
from ecom_agents.llm.client import LLMResponse
from ecom_agents.llm.mock import mock_result
from ecom_agents.tools.catalog import CatalogTools
from ecom_agents.tools.repository import Repository


class FailingLLM:
    """Simulates a provider outage for every intent."""

    model = "failing"

    def complete_json(self, *, intent: str, payload: dict[str, Any], **_: Any) -> LLMResponse:
        raise RuntimeError("simulated LLM outage")


class ScriptedLLM:
    """Returns canned responses for selected intents, rule-engine output otherwise."""

    model = "scripted"

    def __init__(self, overrides: dict[str, dict[str, Any]] | None = None) -> None:
        self.overrides = overrides or {}
        self.calls: list[str] = []

    def complete_json(self, *, intent: str, payload: dict[str, Any], **_: Any) -> LLMResponse:
        self.calls.append(intent)
        data = self.overrides.get(intent, mock_result(intent, payload))
        return LLMResponse(data=dict(data), tokens=1, model=self.model)


class HallucinatingLLM(ScriptedLLM):
    """A model that invents SKUs and prices; reconciliation must override them."""

    def __init__(self) -> None:
        super().__init__(
            {
                "synthesize": {
                    "headline": "全网最低价！",
                    "body": "绝对第一品牌，100%有效",
                    "cta": "立即下单",
                    "recommended_skus": ["SKU-DOES-NOT-EXIST"],
                    "discount_rate": 0.99,
                }
            }
        )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        llm_provider="mock",
        db_path=tmp_path / "test.db",
        require_approval=False,
        ab_enabled=True,
        ab_split=0.5,
        log_level="WARNING",
    )


@pytest.fixture
def repository(settings: Settings) -> Repository:
    return Repository(settings.db_path, settings.seed_path)


@pytest.fixture
def tools(repository: Repository) -> CatalogTools:
    return CatalogTools(repository)


@pytest.fixture
def api_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
