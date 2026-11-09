"""Six specialist agents.

Stage 1: profile. Stage 2: recommendation / inventory / pricing (parallel).
Stage 3: marketing. Compliance runs after synthesis, outside the supervisor's
stage plan, because every plan must be reviewed regardless of routing.
"""

from __future__ import annotations

from typing import Any

from .base import BaseAgent


def _context_request(context: dict[str, Any]) -> dict[str, Any]:
    return dict(context.get("request") or {})


def _upstream(context: dict[str, Any], name: str) -> dict[str, Any]:
    value = (context.get("agent_data") or {}).get(name)
    return dict(value) if isinstance(value, dict) else {}


class ProfileAgent(BaseAgent):
    name = "profile"

    def build_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        user_id = _context_request(context).get("user_id", "")
        snapshot = self.tools.user_snapshot(user_id)
        self._record("user_snapshot", user_id=user_id)
        return {
            "request": _context_request(context),
            "user_snapshot": snapshot,
            "variant": context.get("variant") or {},
        }

    def required_fields(self) -> tuple[str, ...]:
        return ("segment", "tags", "preferred_categories", "price_sensitivity", "reasoning")

    def summarize(self, data: dict[str, Any]) -> str:
        return f"分层={data.get('segment')}，偏好={','.join(data.get('preferred_categories') or []) or '未知'}"


class RecommendationAgent(BaseAgent):
    name = "recommendation"

    def build_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        request = _context_request(context)
        variant = context.get("variant") or {}
        bonus = int(variant.get("top_n_bonus") or 0)
        request["top_n"] = max(1, int(request.get("top_n") or 3) + bonus)
        candidates = self.tools.candidate_products(limit=12, include_out_of_stock=True)
        self._record("candidate_products", limit=12, include_oos=True)
        return {
            "request": request,
            "profile": _upstream(context, "profile"),
            "candidates": candidates,
            "variant": variant,
        }

    def required_fields(self) -> tuple[str, ...]:
        return ("items", "excluded_skus", "reasoning")

    def summarize(self, data: dict[str, Any]) -> str:
        items = data.get("items") or []
        return f"推荐 {len(items)} 个 SKU，排除缺货 {len(data.get('excluded_skus') or [])} 个"


class InventoryAgent(BaseAgent):
    name = "inventory"

    def build_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        products = self.tools.candidate_products(limit=12, include_out_of_stock=True)
        self._record("candidate_products", limit=12, include_oos=True)
        skus = [str(p["sku"]) for p in products]
        inventory = self.tools.inventory_batch(skus)
        self._record("inventory_batch", skus=len(skus))
        return {
            "request": _context_request(context),
            "candidate_skus": skus,
            "inventory": inventory,
            "variant": context.get("variant") or {},
        }

    def required_fields(self) -> tuple[str, ...]:
        return ("sellable_skus", "risky_skus", "notes", "reasoning")

    def summarize(self, data: dict[str, Any]) -> str:
        return f"可售 {len(data.get('sellable_skus') or [])} 个，风险 {len(data.get('risky_skus') or [])} 个"


class PricingAgent(BaseAgent):
    name = "pricing"

    def build_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        variant = context.get("variant") or {}
        self._discount_cap = float(variant.get("discount_cap") or 0.15)
        products = self.tools.candidate_products(limit=8, include_out_of_stock=False)
        self._record("candidate_products", limit=8, in_stock_only=True)
        skus = [str(p["sku"]) for p in products]
        price_book = self.tools.price_book(skus)
        self._record("price_book", skus=len(skus))
        coupons = self.tools.coupons()
        self._record("coupons")
        self._coupons = coupons
        return {
            "request": _context_request(context),
            "price_book": price_book,
            "coupons": coupons,
            "variant": variant,
        }

    def required_fields(self) -> tuple[str, ...]:
        return ("discount_rate", "coupon_code", "expected_margin", "reasoning")

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        validated = super().validate(data)
        cap = getattr(self, "_discount_cap", 0.15)
        try:
            rate = float(validated.get("discount_rate") or 0.0)
        except (TypeError, ValueError):
            rate = 0.0
        code = validated.get("coupon_code")
        if rate > 0 and code:
            match = next(
                (c for c in getattr(self, "_coupons", []) if str(c.get("code")) == str(code)),
                None,
            )
            if match and str(match.get("kind")) == "percent":
                rate = min(rate, float(match.get("value") or 0.0))
        validated["discount_rate"] = round(max(0.0, min(cap, rate)), 4)
        return validated

    def summarize(self, data: dict[str, Any]) -> str:
        return f"折扣 {float(data.get('discount_rate') or 0) * 100:.0f}%，券 {data.get('coupon_code') or '无'}"


class MarketingAgent(BaseAgent):
    name = "marketing"

    def build_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        request = _context_request(context)
        user_id = request.get("user_id", "")
        snapshot = self.tools.user_snapshot(user_id)
        self._record("user_snapshot", user_id=user_id)
        return {
            "request": request,
            "user_snapshot": snapshot,
            "profile": _upstream(context, "profile"),
            "recommendations": _upstream(context, "recommendation"),
            "inventory": _upstream(context, "inventory"),
            "pricing": _upstream(context, "pricing"),
            "variant": context.get("variant") or {},
        }

    def required_fields(self) -> tuple[str, ...]:
        return ("headline", "body", "cta", "reasoning")

    def summarize(self, data: dict[str, Any]) -> str:
        return str(data.get("headline") or "")


class ComplianceAgent(BaseAgent):
    name = "compliance"

    def build_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        lexicon = self.tools.policy_lexicon()
        self._record("policy_lexicon")
        return {
            "request": _context_request(context),
            "plan": context.get("plan") or {},
            "lexicon": lexicon,
            "variant": context.get("variant") or {},
        }

    def required_fields(self) -> tuple[str, ...]:
        return ("verdict", "issues", "suggestions", "reasoning")

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        validated = super().validate(data)
        verdict = str(validated.get("verdict") or "approved").lower()
        if verdict not in {"approved", "revise", "rejected"}:
            verdict = "revise"
        validated["verdict"] = verdict
        for key in ("issues", "suggestions"):
            if not isinstance(validated.get(key), list):
                validated[key] = []
        return validated

    def summarize(self, data: dict[str, Any]) -> str:
        return f"合规裁决={data.get('verdict')}，问题 {len(data.get('issues') or [])} 项"


AGENT_CLASSES = (
    ProfileAgent,
    RecommendationAgent,
    InventoryAgent,
    PricingAgent,
    MarketingAgent,
    ComplianceAgent,
)


def build_agents(llm: Any, tools: Any, settings: Any) -> dict[str, BaseAgent]:
    return {cls.name: cls(llm, tools, settings) for cls in AGENT_CLASSES}
