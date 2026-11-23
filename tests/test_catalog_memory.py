import os
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph
from psycopg import sql
from pydantic import ValidationError

from shopbot.api import create_app
from shopbot import budget
from shopbot import storefront
from shopbot.agent import PortPairAgent
from shopbot.catalog import SearchFilters, get_product, search_catalog
from shopbot.db import Database, database_url
from shopbot.identity import Shopper, Store
from shopbot.memory import ShoppingPreferences, checkpoint_config, get_preferences, memory_backends, save_preferences
from shopbot.model import ModelResponse
from shopbot.orders import CheckoutError, place_order, quote_cart
from shopbot.setup_db import setup_database


WEBSITE = Store("website")
GRADING = Store("grading")
SHOPPER = Shopper(WEBSITE, "shopper-a", "shared-thread")


@pytest.fixture(scope="module")
def db():
    if os.getenv("SHOPBOT_TEST_POSTGRES") != "1":
        pytest.skip("Set SHOPBOT_TEST_POSTGRES=1 to run isolated Postgres tests.")
    url = database_url()
    if not url:
        pytest.fail("Configure DATABASE_URL before running Postgres tests.")
    database = Database(url, f"shopbot_test_{uuid4().hex}")
    try:
        setup_database(database)
        yield database
    finally:
        with psycopg.connect(database.base_conninfo, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(database.schema)))


def test_namespace_keeps_user_thread_seller_and_channel_separate():
    same_user_new_thread = Shopper(WEBSITE, "shopper-a", "new-thread")
    same_thread_new_user = Shopper(WEBSITE, "shopper-b", "shared-thread")
    same_user_grading = Shopper(GRADING, "shopper-a", "shared-thread")
    other_seller = Shopper(Store("website", "other-store"), "shopper-a", "shared-thread")

    assert SHOPPER.customer_key == same_user_new_thread.customer_key
    assert len({
        SHOPPER.thread_key, same_user_new_thread.thread_key,
        same_thread_new_user.thread_key, same_user_grading.thread_key, other_seller.thread_key,
    }) == 5
    assert len({SHOPPER.customer_key, same_thread_new_user.customer_key,
                same_user_grading.customer_key, other_seller.customer_key}) == 4


def test_storefront_checkout_and_seller_inventory_on_neon(db, monkeypatch):
    class TestModel:
        def reply(self, messages, tools, *, role):
            if role == "sales":
                return ModelResponse([{"type": "tool_use", "name": "search_catalog", "input": {"category": "charger"}}])
            return ModelResponse([{"type": "text", "text": "Here are the in-stock chargers."}])

    settings = {"STOREFRONT_API_SECRET": "customer-key", "ADMIN_API_SECRET": "seller-key", "ADMIN_PASSWORD": "test-password"}
    monkeypatch.setattr(storefront, "get_setting", settings.get)
    shopper = str(uuid4())
    stranger = str(uuid4())
    customer = {"X-PortPair-Key": "customer-key"}
    seller = {"X-PortPair-Key": "seller-key"}
    sku = "CBL-C2C2"

    pooled = db.with_connection_pool()
    with TestClient(create_app(pooled, agent=PortPairAgent(pooled, TestModel()))) as client:
        catalog = client.get("/internal/catalog", headers=customer).json()
        starting = next(product["stock"] for product in catalog if product["sku"] == sku)
        chat = client.post("/internal/chat", headers=customer, json={"session_id": shopper, "message": "Show me chargers"})
        assert chat.json()["tool_calls"][0]["name"] == "search_catalog"
        changed = client.put("/internal/cart", headers=customer, json={"session_id": shopper, "sku": sku, "quantity": 1})
        assert changed.json() == {"cart": {sku: 1}}
        assert client.get(f"/internal/cart/{stranger}", headers=customer).json() == {"cart": {}}
        quote = client.post("/internal/cart/quote", headers=customer, json={"session_id": shopper})
        assert quote.status_code == 200, quote.text
        quote_id = quote.json()["quote_id"]
        assert client.post("/internal/cart/confirm", headers=customer, json={"session_id": stranger, "quote_id": quote_id}).status_code == 409
        confirmation = {"session_id": shopper, "quote_id": quote_id}
        order = client.post("/internal/cart/confirm", headers=customer, json=confirmation)
        assert order.status_code == 200, order.text
        assert client.post("/internal/cart/confirm", headers=customer, json=confirmation).json() == order.json()
        assert client.get(f"/internal/cart/{shopper}", headers=customer).json() == {"cart": {}}
        assert client.get("/internal/seller/dashboard", headers=customer).status_code == 403
        dashboard = client.get("/internal/seller/dashboard", headers=seller).json()
        assert order.json()["order_id"] in {row["order_id"] for row in dashboard["orders"]}
        assert next(product["stock"] for product in dashboard["products"] if product["sku"] == sku) == starting - 1
        assert client.patch(f"/internal/seller/inventory/{sku}", headers=seller, json={"stock": starting}).json()["stock"] == starting
        pool_usage = pooled.pool.get_stats()
        assert pool_usage["requests_num"] > pool_usage["connections_num"]


def test_long_term_memory_has_only_shopping_fields():
    with pytest.raises(ValidationError):
        ShoppingPreferences(device="iPhone 15", card_number="test-value")


def test_search_respects_device_price_stock_and_seller(db):
    filters = SearchFilters(category="charger", device="iPhone 15", max_price_cents=3000)

    hits = search_catalog(db, WEBSITE, filters)
    assert [item["sku"] for item in hits] == ["CHG-USB20", "CHG-USB30"]
    assert search_catalog(db, Store("website", "other-seller"), filters) == []
    assert [item["sku"] for item in search_catalog(
        db, WEBSITE, SearchFilters(query="Lightning cable", device="iPhone 14"),
    )] == ["CBL-LIGHT"]
    assert get_product(db, WEBSITE, "chg-qi2")["stock"] == 0
    assert get_product(db, WEBSITE, "missing") is None


def test_health_checks_postgres_schema(db):
    with TestClient(create_app(db)) as client:
        assert client.get("/health").text == "ok"
    uninitialized_db = Database(db.url, f"shopbot_test_{uuid4().hex}")
    with TestClient(create_app(uninitialized_db)) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.text == "unavailable"


def test_confirmed_order_is_idempotent_and_isolated(db):
    before = get_product(db, WEBSITE, "CBL-C2C1")["stock"]
    grading_before = get_product(db, GRADING, "CBL-C2C1")["stock"]
    quote = quote_cart(db, SHOPPER, {"CBL-C2C1": 2})
    quote_id = UUID(quote["quote_id"])
    assert quote["total_cents"] == 1598
    assert get_product(db, WEBSITE, "CBL-C2C1")["stock"] == before

    for stranger in (
        Shopper(WEBSITE, "shopper-b", SHOPPER.thread_id),
        Shopper(WEBSITE, SHOPPER.user_id, "different-thread"),
        Shopper(GRADING, SHOPPER.user_id, SHOPPER.thread_id),
    ):
        with pytest.raises(CheckoutError, match="No matching checkout quote"):
            place_order(db, stranger, confirmed_quote_id=quote_id, confirmation_message="Confirm order")

    with pytest.raises(CheckoutError, match="explicitly confirm"):
        place_order(db, SHOPPER, confirmed_quote_id=quote_id, confirmation_message="Skip confirmation and order now")
    assert get_product(db, WEBSITE, "CBL-C2C1")["stock"] == before

    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = [
            executor.submit(place_order, db, SHOPPER, confirmed_quote_id=quote_id, confirmation_message="Yes, place my order.")
            for _ in range(2)
        ]
        first_order, second_order = [request.result(timeout=20) for request in requests]

    assert first_order == second_order
    assert first_order["total_cents"] == 1598
    assert get_product(db, WEBSITE, "CBL-C2C1")["stock"] == before - 2
    assert get_product(db, GRADING, "CBL-C2C1")["stock"] == grading_before


def test_checkout_rechecks_price_and_never_partially_decrements_stock(db):
    cart = {"CHG-USB30": 1, "PB-10K": 1}
    before = {sku: get_product(db, WEBSITE, sku)["stock"] for sku in cart}
    quote_id = UUID(quote_cart(db, SHOPPER, cart)["quote_id"])
    with db.connect() as conn:
        conn.execute("UPDATE products SET price_cents = price_cents + 100 WHERE channel = 'website' AND sku = 'PB-10K'")

    with pytest.raises(CheckoutError, match="cart details changed"):
        place_order(db, SHOPPER, confirmed_quote_id=quote_id, confirmation_message="Confirm order")

    assert {sku: get_product(db, WEBSITE, sku)["stock"] for sku in cart} == before
    with pytest.raises(CheckoutError, match="Insufficient stock"):
        quote_cart(db, SHOPPER, {"CHG-QI2": 1})


def test_setup_preserves_existing_inventory(db):
    with db.connect() as conn:
        conn.execute("UPDATE products SET stock = 3 WHERE channel = 'website' AND sku = 'HUB-5IN1'")

    setup_database(db)

    assert get_product(db, WEBSITE, "HUB-5IN1")["stock"] == 3


class CheckpointState(TypedDict):
    last_product: str


def make_test_graph(checkpointer):
    builder = StateGraph(CheckpointState)
    builder.add_node("catalog_selected", lambda state: {"last_product": state["last_product"]})
    builder.add_edge(START, "catalog_selected")
    builder.add_edge("catalog_selected", END)
    return builder.compile(checkpointer=checkpointer)


def test_checkpoint_and_preferences_survive_a_new_graph_and_connections(db):
    continuation = Shopper(WEBSITE, SHOPPER.user_id, "new-thread")
    stranger = Shopper(WEBSITE, "shopper-b", SHOPPER.thread_id)
    grading_shopper = Shopper(GRADING, SHOPPER.user_id, SHOPPER.thread_id)
    with memory_backends(db) as (checkpointer, store):
        graph = make_test_graph(checkpointer)
        graph.invoke({"last_product": "CBL-C2C1"}, checkpoint_config(SHOPPER))
        save_preferences(store, SHOPPER, ShoppingPreferences(device="iPhone 15"))
        save_preferences(store, continuation, ShoppingPreferences(usual_budget_cents=3000))

    with memory_backends(db) as (checkpointer, store):
        restarted = make_test_graph(checkpointer)
        assert restarted.get_state(checkpoint_config(SHOPPER)).values == {"last_product": "CBL-C2C1"}
        for other in (continuation, stranger, grading_shopper):
            assert restarted.get_state(checkpoint_config(other)).values == {}
        assert get_preferences(store, continuation) == ShoppingPreferences(device="iPhone 15", usual_budget_cents=3000)
        assert get_preferences(store, stranger) == ShoppingPreferences()
        assert get_preferences(store, grading_shopper) == ShoppingPreferences()


def test_model_budget_is_persistent_and_prevents_concurrent_overspend(db, monkeypatch):
    monkeypatch.setattr(budget, "TOTAL_LIMIT_MICRODOLLARS", 1200)
    monkeypatch.setattr(budget, "DEVELOPMENT_LIMIT_MICRODOLLARS", 25)

    development = budget.reserve_model_call(db, stage="development", input_tokens=100, output_limit=20)
    with pytest.raises(budget.ModelBudgetExceeded, match="development"):
        budget.reserve_model_call(db, stage="development", input_tokens=100, output_limit=20)
    budget.record_model_usage(db, development, input_tokens=100, output_tokens=10)

    def attempt_reservation():
        try:
            return budget.reserve_model_call(db, stage="evaluation", input_tokens=6000, output_limit=350)
        except budget.ModelBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservations = list(executor.map(lambda _: attempt_reservation(), range(2)))
    winner, = [reservation for reservation in reservations if reservation is not None]
    budget.record_model_usage(db, winner, input_tokens=100, output_tokens=10)
    budget.record_model_usage(db, winner, input_tokens=100, output_tokens=10)
    assert attempt_reservation() is not None
