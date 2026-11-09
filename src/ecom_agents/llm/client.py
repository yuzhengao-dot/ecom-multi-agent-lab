"""LLM client abstraction with an OpenAI-compatible implementation.

The graph only depends on :class:`LLMClient`, so swapping providers (or running
fully offline with :class:`MockLLM`) requires no changes to agents.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Settings
from .mock import mock_result
from .prompts import system_prompt, user_prompt

logger = logging.getLogger("ecom_agents.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    data: dict[str, Any]
    tokens: int = 0
    model: str = ""
    cached: bool = False


class LLMClient(Protocol):
    """Return a parsed JSON object for a given intent and tool payload."""

    model: str

    def complete_json(
        self,
        *,
        intent: str,
        payload: dict[str, Any],
        system: str | None = None,
        user: str | None = None,
    ) -> LLMResponse: ...


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
        cleaned = cleaned.rstrip("`").strip()
    return cleaned


class MockLLM:
    """Offline, deterministic client backed by the rule engine."""

    model = "mock-rule-engine"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(
        self,
        *,
        intent: str,
        payload: dict[str, Any],
        system: str | None = None,
        user: str | None = None,
    ) -> LLMResponse:
        self.calls.append(intent)
        return LLMResponse(data=mock_result(intent, payload), tokens=0, model=self.model)


class OpenAICompatibleLLM:
    """Any endpoint implementing the OpenAI chat-completions JSON API."""

    def __init__(self, settings: Settings) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise LLMError("openai package is required for LLM_PROVIDER=openai") from exc
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.retries = max(0, settings.llm_max_retries)
        self._client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
            max_retries=0,
        )
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def complete_json(
        self,
        *,
        intent: str,
        payload: dict[str, Any],
        system: str | None = None,
        user: str | None = None,
    ) -> LLMResponse:
        sys_msg = system or system_prompt(intent)
        usr_msg = user or user_prompt(intent, payload)
        cache_key = hashlib.sha256(f"{self.model}|{sys_msg}|{usr_msg}".encode("utf-8")).hexdigest()
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return LLMResponse(data=dict(cached), tokens=0, model=self.model, cached=True)

        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": usr_msg},
        ]
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "response_format": {"type": "json_object"},
            }
            try:
                response = self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                # Some compatible gateways reject response_format; retry without it.
                if "response_format" in str(exc).lower():
                    kwargs.pop("response_format", None)
                    try:
                        response = self._client.chat.completions.create(**kwargs)
                    except Exception as inner:
                        last_error = inner
                        continue
                else:
                    last_error = exc
                    continue

            content = response.choices[0].message.content if response.choices else ""
            try:
                data = json.loads(_strip_fences(content or "{}"))
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if not isinstance(data, dict):
                last_error = LLMError("LLM returned a JSON array or scalar, expected an object")
                continue
            usage = getattr(response, "usage", None)
            tokens = int(getattr(usage, "total_tokens", 0) or 0)
            with self._lock:
                self._cache[cache_key] = dict(data)
            return LLMResponse(data=data, tokens=tokens, model=self.model)

        logger.warning("llm_call_failed intent=%s attempts=%d error=%s", intent, self.retries + 1, last_error)
        raise LLMError(f"LLM call failed for {intent}: {last_error}")


def build_llm(settings: Settings) -> LLMClient:
    if settings.uses_real_llm:
        return OpenAICompatibleLLM(settings)
    return MockLLM()
