import re

from pydantic import BaseModel, ConfigDict, Field

from .db import Database
from .identity import Store


def device_key(device: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", device.casefold())


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str | None = Field(default=None, max_length=150)
    category: str | None = Field(default=None, max_length=40)
    device: str | None = Field(default=None, max_length=80)
    max_price_cents: int | None = Field(default=None, ge=0, le=1_000_000, strict=True)
    in_stock: bool = True
    limit: int = Field(default=8, ge=1, le=20, strict=True)


PRODUCT_COLUMNS = "sku, name, category, description, compatible_devices, price_cents, stock"


def search_catalog(db: Database, store: Store, filters: SearchFilters) -> list[dict]:
    with db.connect() as conn:
        return conn.execute(
            f"""
            SELECT {PRODUCT_COLUMNS} FROM products
            WHERE channel = %(channel)s AND store_id = %(store_id)s
              AND (%(category)s::text IS NULL OR category = %(category)s)
              AND (%(device)s::text IS NULL OR %(device)s = ANY(compatible_devices))
              AND (%(budget)s::int IS NULL OR price_cents <= %(budget)s)
              AND (NOT %(in_stock)s OR stock > 0)
              AND (sku = %(sku_query)s OR NOT EXISTS (
                  SELECT 1 FROM unnest(%(terms)s::text[]) AS term
                  WHERE strpos(lower(name || ' ' || category || ' ' || description), term) = 0
              ))
            ORDER BY price_cents, sku
            LIMIT %(limit)s
            """,
            {
                "channel": store.channel,
                "store_id": store.store_id,
                "category": filters.category.lower() if filters.category else None,
                "device": device_key(filters.device) if filters.device else None,
                "budget": filters.max_price_cents,
                "in_stock": filters.in_stock,
                "sku_query": (filters.query or "").upper(),
                "terms": re.findall(r"[a-z0-9]+", (filters.query or "").lower()),
                "limit": filters.limit,
            },
        ).fetchall()


def get_product(db: Database, store: Store, sku: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute(
            f"SELECT {PRODUCT_COLUMNS} FROM products WHERE channel = %s AND store_id = %s AND sku = %s",
            (store.channel, store.store_id, sku.strip().upper()),
        ).fetchone()
