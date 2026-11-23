from contextlib import contextmanager
from typing import Literal

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.base import BaseStore
from langgraph.store.postgres import PostgresStore
from pydantic import BaseModel, ConfigDict, Field

from .db import Database
from .identity import Shopper


class ShoppingPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device: str | None = Field(default=None, min_length=1, max_length=64)
    usual_budget_cents: int | None = Field(default=None, gt=0, le=100_000, strict=True)
    preferred_category: Literal["charger", "cable", "power bank", "hub", "audio"] | None = None


@contextmanager
def memory_backends(db: Database):
    if db.pool is not None:
        yield PostgresSaver(db.pool), PostgresStore(db.pool)
        return

    with (
        PostgresSaver.from_conn_string(db.conninfo) as checkpointer,
        PostgresStore.from_conn_string(db.conninfo) as store,
    ):
        yield checkpointer, store


def checkpoint_config(shopper: Shopper) -> dict:
    return {"configurable": {"thread_id": shopper.thread_key}}


def get_preferences(store: BaseStore, shopper: Shopper) -> ShoppingPreferences:
    values = store.search(("shopping_preferences", shopper.customer_key), limit=3)
    return ShoppingPreferences.model_validate({item.key: item.value["value"] for item in values})


def save_preferences(store: BaseStore, shopper: Shopper, updates: ShoppingPreferences) -> None:
    namespace = ("shopping_preferences", shopper.customer_key)
    for key, value in updates.model_dump(exclude_none=True).items():
        store.put(namespace, key, {"value": value})
