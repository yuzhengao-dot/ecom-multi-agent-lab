"""Base class shared by all specialist agents.

It owns the boring but important parts: tool-call bookkeeping, LLM invocation,
structured validation, deterministic fallback, timing and error capture.
"""

from __future__ import annotations

import time
from typing import Any

from ..config import Settings
from ..llm.client import LLMClient
from ..llm.mock import mock_result
from ..schemas import AgentResult
from ..tools.catalog import CatalogTools


class BaseAgent:
    name: str = "base"

    def __init__(self, llm: LLMClient, tools: CatalogTools, settings: Settings) -> None:
        self.llm = llm
        self.tools = tools
        self.settings = settings
        self._tool_calls: list[str] = []

    def _record(self, tool: str, **kwargs: Any) -> None:
        suffix = ", ".join(f"{k}={v}" for k, v in kwargs.items())
        self._tool_calls.append(f"{tool}({suffix})" if suffix else f"{tool}()")

    def build_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def required_fields(self) -> tuple[str, ...]:
        return ("reasoning",)

    def summarize(self, data: dict[str, Any]) -> str:
        return str(data.get("reasoning") or f"{self.name} 完成")

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Fill missing optional fields; never silently invent business values."""
        validated = dict(data)
        for field in self.required_fields():
            if field not in validated:
                validated[field] = ""
        return validated

    def run(self, context: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        self._tool_calls = []
        error: str | None = None
        degraded = False
        tokens = 0
        try:
            payload = self.build_payload(context)
            response = self.llm.complete_json(intent=self.name, payload=payload)
            data = self.validate(response.data)
            tokens = response.tokens
        except Exception as exc:  # LLM outage, bad JSON, tool failure -> rule fallback
            degraded = True
            error = f"{type(exc).__name__}: {exc}"
            try:
                payload = self.build_payload(context)
            except Exception:
                payload = {"request": context.get("request", {}), "variant": context.get("variant", {})}
            data = self.validate(mock_result(self.name, payload))

        elapsed = (time.perf_counter() - started) * 1000
        return AgentResult(
            agent=self.name,
            ok=True,
            degraded=degraded,
            summary=self.summarize(data),
            data=data,
            reasoning=str(data.get("reasoning") or ""),
            tool_calls=list(self._tool_calls),
            duration_ms=round(elapsed, 2),
            llm_tokens=tokens,
            error=error,
        )
