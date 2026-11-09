"""Deterministic rule engine used by the mock LLM and as agent fallback.

This module is the reason the whole system can be exercised without an API key:
it consumes exactly the same tool payload a real LLM would receive, so prompts,
state transitions and API responses stay identical in both modes.
"""

from __future__ import annotations

from typing import Any

from ..config import DEFAULT_STAGES
from ..tools.catalog import FORBIDDEN_CLAIMS


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _profile(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = _as_dict(payload.get("user_snapshot"))
    if not snapshot.get("found"):
        return {
            "segment": "新客",
            "tags": ["无历史行为", "需冷启动"],
            "preferred_categories": [],
            "price_sensitivity": "medium",
            "avg_order_value": 0.0,
            "reasoning": "未找到该用户历史数据，按冷启动新客处理，推荐热销商品。",
        }
    user = _as_dict(snapshot.get("user"))
    tags = [str(t) for t in _as_list(user.get("tags"))]
    aov = float(snapshot.get("avg_order_value") or 0.0)
    latest = snapshot.get("latest_order_days_ago")
    register_days = int(user.get("register_days") or 0)
    preferred = [str(c) for c in _as_list(snapshot.get("preferred_categories"))]

    if latest is None or float(latest) > 180:
        segment = "沉睡待召回"
    elif register_days < 120:
        segment = "新客"
    elif aov >= 500:
        segment = "高价值"
    elif "价格敏感" in tags:
        segment = "价格敏感"
    else:
        segment = "成长型"

    if "价格敏感" in tags or aov < 250:
        sensitivity = "high"
    elif aov >= 600:
        sensitivity = "low"
    else:
        sensitivity = "medium"

    derived = [segment]
    if preferred:
        derived.append(f"偏好{preferred[0]}")
    derived.append(f"累计{snapshot.get('order_count', 0)}单")
    return {
        "segment": segment,
        "tags": (derived + tags)[:5],
        "preferred_categories": preferred,
        "price_sensitivity": sensitivity,
        "avg_order_value": aov,
        "reasoning": (
            f"近 {snapshot.get('order_count', 0)} 单，客单价 {aov:.0f} 元，"
            f"最近下单距今 {latest if latest is not None else '未知'} 天，据此判定为{segment}。"
        ),
    }


def _recommendation(payload: dict[str, Any]) -> dict[str, Any]:
    request = _as_dict(payload.get("request"))
    profile = _as_dict(payload.get("profile"))
    candidates = [p for p in _as_list(payload.get("candidates")) if isinstance(p, dict)]
    top_n = int(request.get("top_n") or 3)
    preferred = [str(c) for c in _as_list(profile.get("preferred_categories"))]
    segment = str(profile.get("segment") or "")

    scored: list[tuple[float, dict[str, Any]]] = []
    excluded: list[str] = []
    for product in candidates:
        sku = str(product.get("sku"))
        stock = int(product.get("stock") or 0)
        if stock <= 0:
            excluded.append(sku)
            continue
        score = float(product.get("rating") or 0) * 10
        if preferred and product.get("category") in preferred:
            score += 12
        if segment == "价格敏感" and float(product.get("price") or 0) < 200:
            score += 6
        if stock < 20:
            score -= 4
        scored.append((score, product))

    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("sku"))))
    items = [
        {
            "sku": str(p["sku"]),
            "title": str(p["title"]),
            "reason": (
                f"{p['category']}类目评分 {p['rating']}，"
                + ("与历史偏好一致" if p.get("category") in preferred else "可作为拓展选择")
                + f"，当前库存 {p['stock']} 件"
            ),
            "score": round(score, 2),
        }
        for score, p in scored[:top_n]
    ]
    return {
        "items": items,
        "excluded_skus": excluded,
        "reasoning": f"从 {len(candidates)} 个候选中按类目亲和、评分、库存可售性排序，剔除 {len(excluded)} 个缺货 SKU。",
    }


def _inventory(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in _as_list(payload.get("inventory")) if isinstance(r, dict)]
    sellable = [str(r["sku"]) for r in rows if r.get("found") and int(r.get("stock") or 0) > 0]
    risky = [str(r["sku"]) for r in rows if not r.get("found") or int(r.get("stock") or 0) < 20]
    notes = "库存充足，可正常投放。"
    if risky:
        notes = f"{', '.join(risky)} 库存偏低或缺货，建议先补货或在素材中用同品类替代品兜底。"
    return {
        "sellable_skus": sellable,
        "risky_skus": risky,
        "notes": notes,
        "reasoning": f"核查 {len(rows)} 个 SKU 的仓位与可售天数，按 20 件安全阈值划分风险。",
    }


def _pricing(payload: dict[str, Any]) -> dict[str, Any]:
    request = _as_dict(payload.get("request"))
    variant = _as_dict(payload.get("variant"))
    book = [p for p in _as_list(payload.get("price_book")) if isinstance(p, dict)]
    coupons = [c for c in _as_list(payload.get("coupons")) if isinstance(c, dict)]
    cap = float(variant.get("discount_cap") or 0.15)
    budget = float(request.get("budget") or 0.0)
    min_margin = min((float(p.get("gross_margin") or 0) for p in book), default=0.4)
    discount = round(min(cap, max(0.0, min_margin - 0.18)), 2)
    if budget <= 0:
        discount = 0.0

    eligible = [c for c in coupons if float(c.get("value") or 0) <= max(0.0, discount + 0.02)]
    chosen: dict[str, Any] | None = None
    if discount > 0 and eligible:
        chosen = min(eligible, key=lambda c: abs(float(c["value"]) - discount))
    coupon_code = str(chosen["code"]) if chosen else None
    if chosen and str(chosen.get("kind")) == "percent":
        # Never advertise a discount the coupon cannot actually deliver.
        discount = min(discount, float(chosen["value"]))
    return {
        "discount_rate": discount,
        "coupon_code": coupon_code,
        "expected_margin": round(max(0.0, min_margin - discount), 4),
        "reasoning": (
            f"最低毛利率 {min_margin:.2f}，保留 18 个点安全垫后折扣上限 {discount:.2f}，"
            f"预算 {budget:.0f} 元，命中券 {coupon_code or '无'}。"
        ),
    }


def _marketing(payload: dict[str, Any]) -> dict[str, Any]:
    request = _as_dict(payload.get("request"))
    variant = _as_dict(payload.get("variant"))
    profile = _as_dict(payload.get("profile"))
    items = [i for i in _as_list(_as_dict(payload.get("recommendations")).get("items")) if isinstance(i, dict)]
    scene = str(request.get("scene") or "专属活动")
    channel = str(request.get("channel") or "app_push")
    segment = str(profile.get("segment") or "会员")
    style = str(variant.get("copy_style") or "value_first")
    nickname = str(_as_dict(_as_dict(payload.get("user_snapshot")).get("user")).get("nickname") or "朋友")
    first_title = str(items[0]["title"]) if items else "本季精选好物"
    pricing = _as_dict(payload.get("pricing"))
    discount = float(pricing.get("discount_rate") or 0)

    if style == "conversion_first":
        headline = f"{scene}限时福利已就位"
        body = (
            f"{nickname}你好，根据你的浏览与购买记录，先为你挑了「{first_title}」。"
            + (f"本轮可享 {int(discount * 100)}% 专属优惠，" if discount > 0 else "")
            + "库存有限，喜欢就趁这次带走吧。"
        )
        cta = "立即查看我的专属清单"
    else:
        headline = f"{scene}｜一份给你的挑选清单"
        body = (
            f"{nickname}你好，我们结合你之前的订单，整理了「{first_title}」等几件可能用得上的商品。"
            + (f"随单附上一张小额优惠券，" if discount > 0 else "")
            + "不着急决定，慢慢看看就好。"
        )
        cta = "看看清单"
    if channel == "sms":
        body = body[:60]
    return {
        "headline": headline,
        "body": body,
        "cta": cta,
        "reasoning": f"面向{segment}用户，按 {channel} 渠道与 {style} 风格组织文案，标题不含绝对化表述。",
    }


def _compliance(payload: dict[str, Any]) -> dict[str, Any]:
    plan = _as_dict(payload.get("plan"))
    lexicon = [str(w) for w in _as_list(payload.get("lexicon"))] or list(FORBIDDEN_CLAIMS)
    text = " ".join(str(plan.get(k) or "") for k in ("headline", "body", "cta"))
    hits = [word for word in lexicon if word in text]
    if "rejected" in str(plan.get("compliance_status") or ""):
        return {"verdict": "rejected", "issues": ["上游已标记拒绝"], "suggestions": ["重新生成方案"], "reasoning": "沿用上游裁决。"}
    if hits:
        return {
            "verdict": "revise",
            "issues": [f"命中禁用词：{', '.join(hits)}"],
            "suggestions": ["用客观描述替换绝对化表述", "核对折扣是否与券面值一致"],
            "reasoning": "广告法合规词表扫描未通过。",
        }
    return {
        "verdict": "approved",
        "issues": [],
        "suggestions": [],
        "reasoning": "文案未含禁用词，折扣与毛利约束一致，可进入投放。",
    }


def _supervisor(payload: dict[str, Any]) -> dict[str, Any]:
    variant = _as_dict(payload.get("variant"))
    request = _as_dict(payload.get("request"))
    stages = [list(stage) for stage in DEFAULT_STAGES]
    skipped: list[str] = []
    if not variant.get("enable_pricing", True):
        stages[1] = [name for name in stages[1] if name != "pricing"]
        skipped.append("pricing")
    if request.get("dry_run"):
        skipped.append("coupon_reservation")
    return {
        "stages": stages,
        "rationale": (
            "画像先行；召回、库存与定价互不依赖，放入同一阶段并行；"
            "文案依赖前三阶段产出，单独放在最后。"
            + ("对照组关闭定价 Agent 以保持保守折扣。" if skipped and "pricing" in skipped else "")
        ),
        "skipped": skipped,
    }


def _synthesize(payload: dict[str, Any]) -> dict[str, Any]:
    results = _as_dict(payload.get("agent_results"))
    marketing = _as_dict(results.get("marketing", {}).get("data") if isinstance(results.get("marketing"), dict) else {})
    recommendation = _as_dict(results.get("recommendation", {}).get("data") if isinstance(results.get("recommendation"), dict) else {})
    pricing = _as_dict(results.get("pricing", {}).get("data") if isinstance(results.get("pricing"), dict) else {})
    inventory = _as_dict(results.get("inventory", {}).get("data") if isinstance(results.get("inventory"), dict) else {})
    profile = _as_dict(results.get("profile", {}).get("data") if isinstance(results.get("profile"), dict) else {})
    items = [i for i in _as_list(recommendation.get("items")) if isinstance(i, dict)]
    return {
        "headline": str(marketing.get("headline") or "本季精选清单"),
        "body": str(marketing.get("body") or "根据你的历史订单整理了一份商品清单。"),
        "cta": str(marketing.get("cta") or "查看清单"),
        "recommended_skus": [str(i["sku"]) for i in items],
        "discount_rate": float(pricing.get("discount_rate") or 0.0),
        "coupon_code": pricing.get("coupon_code"),
        "inventory_notes": str(inventory.get("notes") or ""),
        "segment": str(profile.get("segment") or ""),
        "rationale": "由画像、召回、库存、定价、文案五位专家的结论汇总而成，关键数值均来自工具数据。",
        "compliance_status": "pending",
        "compliance_notes": [],
    }


_HANDLERS = {
    "profile": _profile,
    "recommendation": _recommendation,
    "inventory": _inventory,
    "pricing": _pricing,
    "marketing": _marketing,
    "compliance": _compliance,
    "supervisor": _supervisor,
    "synthesize": _synthesize,
}


def mock_result(intent: str, payload: dict[str, Any]) -> dict[str, Any]:
    handler = _HANDLERS.get(intent)
    if handler is None:
        return {"reasoning": f"no rule for intent {intent}"}
    return handler(payload)
