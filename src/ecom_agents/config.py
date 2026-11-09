"""Runtime configuration.

Values are read from environment variables; a local ``.env`` file is loaded
when present. No third-party settings library is required on purpose, so the
package stays easy to vendored and to run inside a container.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "ecom.db"
DEFAULT_SEED_PATH = PROJECT_ROOT / "data" / "seed.json"


def _load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing environment wins."""
    env_file = path or PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of configuration used by a single run."""

    llm_provider: str = "mock"          # "mock" | "openai"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_timeout: float = 30.0
    llm_max_retries: int = 2

    db_path: Path = DEFAULT_DB_PATH
    seed_path: Path = DEFAULT_SEED_PATH

    require_approval: bool = False       # pause before compliance review
    max_agent_retries: int = 1           # retries per missing agent result
    ab_enabled: bool = True
    ab_split: float = 0.5                # share of traffic in variant A

    log_level: str = "INFO"
    trace_store_size: int = 200

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        api_key = os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip()
        if not provider:
            provider = "openai" if api_key else "mock"
        if provider == "openai" and not api_key:
            # A missing key should never crash a demo run; degrade instead.
            provider = "mock"
        base_url = os.getenv("LLM_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
        return cls(
            llm_provider=provider,
            llm_api_key=api_key,
            llm_base_url=base_url.rstrip("/"),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            llm_temperature=_float("LLM_TEMPERATURE", 0.2),
            llm_timeout=_float("LLM_TIMEOUT", 30.0),
            llm_max_retries=_int("LLM_MAX_RETRIES", 2),
            db_path=Path(os.getenv("ECOM_DB_PATH", str(DEFAULT_DB_PATH))),
            seed_path=Path(os.getenv("ECOM_SEED_PATH", str(DEFAULT_SEED_PATH))),
            require_approval=_bool("REQUIRE_APPROVAL", False),
            max_agent_retries=_int("MAX_AGENT_RETRIES", 1),
            ab_enabled=_bool("AB_ENABLED", True),
            ab_split=_float("AB_SPLIT", 0.5),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            trace_store_size=_int("TRACE_STORE_SIZE", 200),
        )

    @property
    def uses_real_llm(self) -> bool:
        return self.llm_provider == "openai" and bool(self.llm_api_key)


# Convenience helpers -------------------------------------------------------

AGENT_NAMES: tuple[str, ...] = (
    "profile",
    "recommendation",
    "inventory",
    "pricing",
    "marketing",
    "compliance",
)

DEFAULT_STAGES: tuple[tuple[str, ...], ...] = (
    ("profile",),
    ("recommendation", "inventory", "pricing"),
    ("marketing",),
)
