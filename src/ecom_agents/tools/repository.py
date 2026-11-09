"""SQLite-backed demo data layer.

A fresh database is created and seeded from ``data/seed.json`` on first use.
The repository intentionally contains no business logic: agents only see data
through :class:`~ecom_agents.tools.catalog.CatalogTools`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_INIT_LOCK = threading.Lock()


class Repository:
    def __init__(self, db_path: Path, seed_path: Path) -> None:
        self.db_path = Path(db_path)
        self.seed_path = Path(seed_path)
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_ready(self, force_reseed: bool = False) -> None:
        with _INIT_LOCK:
            if self._initialized and not force_reseed:
                return
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id TEXT PRIMARY KEY,
                        nickname TEXT NOT NULL,
                        city TEXT NOT NULL,
                        register_days INTEGER NOT NULL,
                        tags TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS products (
                        sku TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        category TEXT NOT NULL,
                        price REAL NOT NULL,
                        cost REAL NOT NULL,
                        rating REAL NOT NULL,
                        stock INTEGER NOT NULL,
                        warehouse TEXT NOT NULL,
                        competitor_price REAL NOT NULL,
                        tags TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS orders (
                        order_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        sku TEXT NOT NULL,
                        qty INTEGER NOT NULL,
                        amount REAL NOT NULL,
                        days_ago INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS browsing (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        sku TEXT NOT NULL,
                        category TEXT NOT NULL,
                        days_ago INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS coupons (
                        code TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        value REAL NOT NULL,
                        min_spend REAL NOT NULL
                    );
                    """
                )
                count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
                if count == 0 or force_reseed:
                    self._reseed(conn)
            self._initialized = True

    def _reseed(self, conn: sqlite3.Connection) -> None:
        seed: dict[str, Any] = json.loads(self.seed_path.read_text(encoding="utf-8"))
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM browsing")
        conn.execute("DELETE FROM coupons")
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM users")
        conn.executemany(
            "INSERT INTO users VALUES (:user_id, :nickname, :city, :register_days, :tags)",
            [{**u, "tags": json.dumps(u["tags"], ensure_ascii=False)} for u in seed["users"]],
        )
        conn.executemany(
            "INSERT INTO products VALUES (:sku, :title, :category, :price, :cost, :rating, "
            ":stock, :warehouse, :competitor_price, :tags)",
            [{**p, "tags": json.dumps(p["tags"], ensure_ascii=False)} for p in seed["products"]],
        )
        conn.executemany(
            "INSERT INTO orders VALUES (:order_id, :user_id, :sku, :qty, :amount, :days_ago)",
            seed["orders"],
        )
        conn.executemany(
            "INSERT INTO browsing (user_id, sku, category, days_ago) VALUES (:user_id, :sku, :category, :days_ago)",
            seed["browsing"],
        )
        conn.executemany(
            "INSERT INTO coupons VALUES (:code, :kind, :value, :min_spend)",
            seed["coupons"],
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if "tags" in data and isinstance(data["tags"], str):
            try:
                data["tags"] = json.loads(data["tags"])
            except json.JSONDecodeError:
                data["tags"] = []
        return data

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        self.ensure_ready()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return self._row(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY user_id").fetchall()
        return [self._row(r) for r in rows]

    def list_orders(self, user_id: str) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY days_ago ASC", (user_id,)
            ).fetchall()
        return [self._row(r) for r in rows]

    def list_browsing(self, user_id: str) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, sku, category, days_ago FROM browsing WHERE user_id = ? ORDER BY days_ago ASC",
                (user_id,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def list_products(
        self,
        category: str | None = None,
        max_price: float | None = None,
        include_out_of_stock: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.ensure_ready()
        sql = "SELECT * FROM products WHERE 1=1"
        params: list[Any] = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if max_price is not None:
            sql += " AND price <= ?"
            params.append(max_price)
        if not include_out_of_stock:
            sql += " AND stock > 0"
        sql += " ORDER BY rating DESC, price ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    def get_product(self, sku: str) -> dict[str, Any] | None:
        self.ensure_ready()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
        return self._row(row) if row else None

    def get_inventory(self, sku: str) -> dict[str, Any]:
        product = self.get_product(sku)
        if not product:
            return {"sku": sku, "found": False, "stock": 0}
        stock = int(product["stock"])
        return {
            "sku": sku,
            "found": True,
            "stock": stock,
            "warehouse": product["warehouse"],
            "status": "out_of_stock" if stock == 0 else ("low" if stock < 20 else "healthy"),
            "days_of_supply": round(stock / 8.0, 1),
        }

    def get_coupons(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM coupons ORDER BY min_spend").fetchall()
        return [self._row(r) for r in rows]
