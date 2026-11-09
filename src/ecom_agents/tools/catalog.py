"""Tool surface exposed to specialist agents.

Agents are never given raw database access. Each tool returns plain JSON-ready
data, which makes prompts deterministic and lets the mock LLM replay the same
inputs offline.
"""

from __future__ import annotations

from typing import Any

from .repository import Repository

FORBIDDEN_CLAIMS = (
    "全网最低",
    "最便宜",
    "第一品牌",
    "100%有效",
    "绝对",
    "包治",
    "国家级",
    "永久",
)


class CatalogTools:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @staticmethod
    def describe() -> list[dict[str, str]]:
        return [
            {"name": "user_snapshot", "description": "用户基础信息、订单与浏览历史、消费统计"},
            {"name": "candidate_products", "description": "按类目与预算筛选候选商品"},
            {"name": "inventory_batch", "description": "批量查询库存、仓位与可售状态"},
            {"name": "price_book", "description": "商品价格、成本、毛利与竞品价"},
            {"name": "coupons", "description": "可用优惠券列表"},
            {"name": "policy_lexicon", "description": "广告法禁用词表"},
        ]

    def user_snapshot(self, user_id: str) -> dict[str, Any]:
        user = self.repository.get_user(user_id)
        if user is None:
            return {"found": False, "user_id": user_id}
        orders = self.repository.list_orders(user_id)
        browsing = self.repository.list_browsing(user_id)
        amounts = [float(o["amount"]) for o in orders]
        categories: dict[str, int] = {}
        for order in orders:
            product = self.repository.get_product(order["sku"])
            if product:
                categories[product["category"]] = categories.get(product["category"], 0) + 1
        for item in browsing:
            categories[item["category"]] = categories.get(item["category"], 0) + 1
        preferred = sorted(categories, key=lambda k: (-categories[k], k))
        return {
            "found": True,
            "user": user,
            "order_count": len(orders),
            "total_spend": round(sum(amounts), 2),
            "avg_order_value": round(sum(amounts) / len(amounts), 2) if amounts else 0.0,
            "latest_order_days_ago": orders[0]["days_ago"] if orders else None,
            "preferred_categories": preferred,
            "recent_orders": orders[:5],
            "recent_browsing": browsing[:5],
        }

    def candidate_products(
        self,
        category: str | None = None,
        max_price: float | None = None,
        limit: int = 8,
        include_out_of_stock: bool = False,
    ) -> list[dict[str, Any]]:
        return self.repository.list_products(
            category=category,
            max_price=max_price,
            include_out_of_stock=include_out_of_stock,
            limit=limit,
        )

    def inventory_batch(self, skus: list[str]) -> list[dict[str, Any]]:
        return [self.repository.get_inventory(sku) for sku in skus]

    def price_book(self, skus: list[str]) -> list[dict[str, Any]]:
        result = []
        for sku in skus:
            product = self.repository.get_product(sku)
            if not product:
                continue
            price = float(product["price"])
            cost = float(product["cost"])
            result.append(
                {
                    "sku": sku,
                    "title": product["title"],
                    "price": price,
                    "cost": cost,
                    "gross_margin": round((price - cost) / price, 4) if price else 0.0,
                    "competitor_price": product["competitor_price"],
                    "price_gap": round(price - float(product["competitor_price"]), 2),
                }
            )
        return result

    def coupons(self) -> list[dict[str, Any]]:
        return self.repository.get_coupons()

    @staticmethod
    def policy_lexicon() -> list[str]:
        return list(FORBIDDEN_CLAIMS)
