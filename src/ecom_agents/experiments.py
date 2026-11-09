"""Deterministic A/B assignment.

The bucket is derived from a hash of (experiment key, user id), so a user
always lands in the same variant without needing a shared assignment store.
"""

from __future__ import annotations

import hashlib
from typing import Any


def assignment_score(user_id: str, experiment_key: str) -> float:
    digest = hashlib.sha256(f"{experiment_key}:{user_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def assign_variant(user_id: str, experiment_key: str, split: float = 0.5, enabled: bool = True) -> str:
    """Return "A" or "B". When disabled, everyone gets "A"."""
    if not enabled:
        return "A"
    split = min(1.0, max(0.0, split))
    return "A" if assignment_score(user_id, experiment_key) < split else "B"


def variant_config(variant: str) -> dict[str, Any]:
    """Variant drives supervisor behaviour rather than hard-coded if/else in agents."""
    if variant == "B":
        return {
            "description": "激进增长组：启用定价 Agent，文案更强转化导向，推荐数量更多",
            "enable_pricing": True,
            "copy_style": "conversion_first",
            "top_n_bonus": 1,
            "discount_cap": 0.25,
        }
    return {
        "description": "稳健对照组：关闭定价 Agent，不做折扣投放，文案偏关怀与价值说明",
        "enable_pricing": False,
        "copy_style": "value_first",
        "top_n_bonus": 0,
        "discount_cap": 0.15,
    }


def describe(user_id: str, experiment_key: str, split: float = 0.5, enabled: bool = True) -> dict[str, Any]:
    variant = assign_variant(user_id, experiment_key, split, enabled)
    return {
        "user_id": user_id,
        "experiment_key": experiment_key,
        "score": round(assignment_score(user_id, experiment_key), 6),
        "variant": variant,
        "enabled": enabled,
        "config": variant_config(variant),
    }
