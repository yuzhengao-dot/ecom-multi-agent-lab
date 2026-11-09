"""Lightweight observability: structured logs, per-run traces and counters.

This is deliberately dependency-free (no OpenTelemetry / Prometheus client),
but the shapes mirror what those systems expect so the code can be swapped to
a real exporter without touching agent code.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from .schemas import TraceReport, TraceSpan

_TRACE_LOCK = threading.Lock()
TRACE_STORE: dict[str, dict[str, Any]] = {}
TRACE_STORE_SIZE = 200


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger("ecom_agents")
    if root.handlers:
        root.setLevel(level)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    logger.info(message, extra={"extra_fields": fields})


@dataclass
class RunTrace:
    """Collects spans for one campaign run."""

    trace_id: str
    variant: str = "A"
    started_at: float = field(default_factory=time.perf_counter)
    spans: list[TraceSpan] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    llm_tokens: int = 0
    degraded_agents: list[str] = field(default_factory=list)
    stages: list[list[str]] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, **detail: Any) -> Iterator[None]:
        start = time.perf_counter()
        status = "ok"
        try:
            yield
        except Exception as exc:  # pragma: no cover - defensive
            status = f"error: {type(exc).__name__}"
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            suffix = " ".join(f"{k}={v}" for k, v in detail.items())
            self.spans.append(TraceSpan(name=name, duration_ms=round(elapsed, 2), status=status, detail=suffix))

    def event(self, message: str) -> None:
        self.events.append(message)

    def add_tokens(self, amount: int) -> None:
        self.llm_tokens += max(0, amount)

    def finish(self) -> TraceReport:
        elapsed = (time.perf_counter() - self.started_at) * 1000
        return TraceReport(
            trace_id=self.trace_id,
            variant=self.variant,
            stages=self.stages,
            spans=self.spans,
            events=self.events,
            llm_tokens=self.llm_tokens,
            degraded_agents=self.degraded_agents,
            elapsed_ms=round(elapsed, 2),
        )


def store_trace(report: TraceReport, trace_store_size: int = TRACE_STORE_SIZE) -> None:
    with _TRACE_LOCK:
        TRACE_STORE[report.trace_id] = report.model_dump(mode="json")
        while len(TRACE_STORE) > trace_store_size:
            TRACE_STORE.pop(next(iter(TRACE_STORE)))


def get_trace(trace_id: str) -> dict[str, Any] | None:
    with _TRACE_LOCK:
        return TRACE_STORE.get(trace_id)


class Metrics:
    """Thread-safe in-process counters rendered as Prometheus text."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._lock = threading.Lock()

    def inc(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + value

    def observe(self, name: str, value_ms: float) -> None:
        self.inc(f"{name}_ms_total", value_ms)

    def render(self) -> str:
        with self._lock:
            snapshot = dict(self._counters)
        lines = [
            "# HELP ecom_campaign_runs_total Total campaign runs",
            "# TYPE ecom_campaign_runs_total counter",
        ]
        for key, value in sorted(snapshot.items()):
            safe = key.replace("-", "_").replace(".", "_")
            lines.append(f"ecom_{safe} {value}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()
