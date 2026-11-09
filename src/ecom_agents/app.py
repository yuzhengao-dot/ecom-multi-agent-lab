"""HTTP surface. ``POST /v1/campaigns`` is the single entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .config import Settings
from .experiments import describe
from .observability import METRICS, configure_logging, get_trace
from .runner import run_campaign
from .schemas import CampaignRequest, CampaignResponse, HealthResponse
from .tools.catalog import CatalogTools
from .tools.repository import Repository


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    configure_logging(cfg.log_level)
    repository = Repository(cfg.db_path, cfg.seed_path)
    tools = CatalogTools(repository)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        repository.ensure_ready()
        yield

    app = FastAPI(
        title="Ecom Multi-Agent Lab",
        version="0.1.0",
        description="LangGraph 驱动的电商营销多智能体决策系统",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.repository = repository

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok", mode="openai" if cfg.uses_real_llm else "mock")

    @app.post("/v1/campaigns", response_model=CampaignResponse)
    def create_campaign(request: CampaignRequest) -> CampaignResponse:
        try:
            return run_campaign(request, cfg, repository=repository)
        except Exception as exc:  # surface a usable message instead of an opaque 500
            raise HTTPException(status_code=502, detail=f"campaign run failed: {type(exc).__name__}: {exc}") from exc

    @app.get("/v1/traces/{trace_id}")
    def read_trace(trace_id: str) -> dict[str, Any]:
        report = get_trace(trace_id)
        if report is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return report

    @app.get("/v1/experiments/assign")
    def assign(
        user_id: str = Query(..., min_length=1),
        experiment_key: str = Query("campaign_v1"),
    ) -> dict[str, Any]:
        return describe(user_id, experiment_key, cfg.ab_split, cfg.ab_enabled)

    @app.get("/v1/catalog/users")
    def list_users() -> list[dict[str, Any]]:
        return repository.list_users()

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return METRICS.render()

    return app


app = create_app()
