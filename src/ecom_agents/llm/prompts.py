"""Prompt templates and the JSON contracts each agent must satisfy.

Prompts are kept separate from agent code so they can be reviewed, versioned
and A/B tested independently. Every prompt demands a single JSON object; the
client parses and the agent validates before the result enters graph state.
"""

from __future__ import annotations

import json
from typing import Any

BASE_RULES = """You are a specialist agent inside an e-commerce campaign decision system.
Rules:
1. Reply with exactly ONE JSON object. No markdown fences, no commentary.
2. Only use facts present in the provided tool data. Never invent SKUs, prices or order ids.
3. Monetary values are in CNY. Keep numbers as JSON numbers, not strings.
4. Keep Chinese copy natural and free of superlatives banned by advertising law.
5. If data is insufficient, return a conservative result and explain it in "reasoning".
"""

OUTPUT_CONTRACTS: dict[str, dict[str, str]] = {
    "profile": {
        "segment": "string, one of 高价值/成长型/新客/沉睡待召回/价格敏感",
        "tags": "array of strings, 2-5 short persona tags",
        "preferred_categories": "array of strings ordered by affinity",
        "price_sensitivity": "string, one of low/medium/high",
        "avg_order_value": "number",
        "reasoning": "string, brief explanation in Chinese",
    },
    "recommendation": {
        "items": "array of objects: {sku, title, reason, score}",
        "excluded_skus": "array of strings for items deliberately dropped",
        "reasoning": "string",
    },
    "inventory": {
        "sellable_skus": "array of strings that can be promoted now",
        "risky_skus": "array of strings with low or zero stock",
        "notes": "string, restock or substitution advice",
        "reasoning": "string",
    },
    "pricing": {
        "discount_rate": "number between 0 and cap",
        "coupon_code": "string or null",
        "expected_margin": "number between 0 and 1",
        "reasoning": "string",
    },
    "marketing": {
        "headline": "string",
        "body": "string",
        "cta": "string",
        "reasoning": "string",
    },
    "compliance": {
        "verdict": "string, one of approved/revise/rejected",
        "issues": "array of strings",
        "suggestions": "array of strings",
        "reasoning": "string",
    },
    "supervisor": {
        "stages": "array of arrays of agent names; agents in the same inner array run in parallel",
        "rationale": "string",
        "skipped": "array of agent names intentionally skipped",
    },
    "synthesize": {
        "headline": "string",
        "body": "string",
        "cta": "string",
        "rationale": "string",
    },
}

AGENT_INSTRUCTIONS: dict[str, str] = {
    "profile": "根据用户订单、浏览与消费统计，输出画像分层与偏好，不要臆测 demographic 信息。",
    "recommendation": "结合画像偏好与候选商品，挑选最合适的商品并逐条给出理由，必须排除缺货商品。",
    "inventory": "评估候选商品库存风险，标记缺货与低库存，给出补货或替换建议。",
    "pricing": "在毛利与竞品价约束下给出折扣与优惠券，折扣不得超过 cap，且必须保证毛利率为正。",
    "marketing": "按场景、渠道与文案风格生成可直接投放的标题、正文与 CTA，禁用绝对化用语。",
    "compliance": "审查最终方案是否含广告法禁用词、虚假折扣或夸大承诺，给出明确裁决。",
    "supervisor": "把可用专家 Agent 编排为若干有序阶段；互不依赖的 Agent 放进同一阶段以并行执行。",
    "synthesize": "汇总各专家结论，输出一份面向运营人员的最终营销方案，不要推翻专家给出的数据。",
}


def system_prompt(intent: str) -> str:
    instruction = AGENT_INSTRUCTIONS.get(intent, "完成分配的任务。")
    contract = OUTPUT_CONTRACTS.get(intent, {})
    return (
        f"{BASE_RULES}\n\nROLE: {intent}\nTASK: {instruction}\n\n"
        "OUTPUT JSON FIELDS:\n" + json.dumps(contract, ensure_ascii=False, indent=2)
    )


def user_prompt(intent: str, payload: dict[str, Any]) -> str:
    return (
        f"Intent: {intent}\n"
        "Tool data and upstream agent results (JSON):\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
        + "\n\nReturn the JSON object defined in the system prompt."
    )
