"""Typed contracts shared by agents, the graph, the API and the CLI.

Every agent returns an :class:`AgentResult`; the orchestrator never exchanges
loose dictionaries with the outside world. Keeping the boundary typed makes
LLM output validation and mocked offline runs straightforward.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CampaignRequest(BaseModel):
    """One campaign decision task submitted by an operator."""

    user_id: str = Field(..., min_length=1, description="Target customer id")
    scene: str = Field("日常触达", description="Business scenario, e.g. 618大促 / 沉睡召回")
    goal: str = Field("提升复购", description="What this campaign should achieve")
    channel: Literal["app_push", "sms", "email", "in_app", "wechat"] = "app_push"
    budget: float = Field(0.0, ge=0.0, description="Coupon budget in CNY")
    top_n: int = Field(3, ge=1, le=10)
    experiment_key: str = "campaign_v1"
    dry_run: bool = Field(False, description="Skip side effects such as coupon reservation")


class AgentResult(BaseModel):
    """Uniform envelope produced by every specialist agent."""

    agent: str
    ok: bool = True
    degraded: bool = False
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    tool_calls: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    llm_tokens: int = 0
    error: str | None = None


class SupervisorDecision(BaseModel):
    """Execution plan produced by the supervisor LLM."""

    stages: list[list[str]] = Field(default_factory=list)
    rationale: str = ""
    skipped: list[str] = Field(default_factory=list)


class CampaignPlan(BaseModel):
    """The final decision handed back to the caller."""

    headline: str = ""
    body: str = ""
    cta: str = ""
    recommended_skus: list[str] = Field(default_factory=list)
    discount_rate: float = 0.0
    coupon_code: str | None = None
    inventory_notes: str = ""
    segment: str = ""
    rationale: str = ""
    compliance_status: Literal["pending", "approved", "revised", "rejected"] = "pending"
    compliance_notes: list[str] = Field(default_factory=list)


class TraceSpan(BaseModel):
    name: str
    duration_ms: float
    status: str = "ok"
    detail: str = ""


class TraceReport(BaseModel):
    trace_id: str
    variant: str
    stages: list[list[str]] = Field(default_factory=list)
    spans: list[TraceSpan] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    llm_tokens: int = 0
    degraded_agents: list[str] = Field(default_factory=list)
    elapsed_ms: float = 0.0


class CampaignResponse(BaseModel):
    trace_id: str
    variant: str
    plan: CampaignPlan
    agent_results: list[AgentResult] = Field(default_factory=list)
    trace: TraceReport
    mode: str = "mock"


class HealthResponse(BaseModel):
    status: str
    mode: str
    version: str = "0.1.0"
