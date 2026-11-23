from hmac import compare_digest
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .catalog import SearchFilters, get_product, search_catalog
from .db import get_setting
from .identity import Shopper, Store
from .memory import checkpoint_config
from .middleware import rate_limit_response
from .orders import CheckoutError, place_order, quote_cart


WEBSITE = Store("website")
Sku = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9-]{1,64}$")]


def matches_secret(candidate: str | None, expected: str | None) -> bool:
    return bool(candidate and expected and compare_digest(candidate.encode(), expected.encode()))


def storefront_auth(x_portpair_key: Annotated[str | None, Header()] = None) -> None:
    if not matches_secret(x_portpair_key, get_setting("STOREFRONT_API_SECRET")):
        raise HTTPException(403, "Storefront access denied.")


def seller_auth(x_portpair_key: Annotated[str | None, Header()] = None) -> None:
    if not matches_secret(x_portpair_key, get_setting("ADMIN_API_SECRET")):
        raise HTTPException(403, "Seller access denied.")


internal_router = APIRouter(prefix="/internal", include_in_schema=False)
storefront = APIRouter(dependencies=[Depends(storefront_auth)])
seller = APIRouter(prefix="/seller", dependencies=[Depends(seller_auth)])


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID

    @property
    def shopper(self) -> Shopper:
        return Shopper(WEBSITE, str(self.session_id), "storefront")


class WebsiteChat(SessionRequest):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class CartUpdate(SessionRequest):
    sku: Sku
    quantity: int = Field(ge=0, le=10, strict=True)


class OrderConfirmation(SessionRequest):
    quote_id: UUID


class SellerLogin(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class StockUpdate(BaseModel):
    stock: int = Field(ge=0, le=10_000, strict=True)


def database(request: Request):
    db = request.app.state.database
    if db is None:
        raise HTTPException(503, "Store database is unavailable.")
    return db


def shopping_agent(request: Request):
    agent = request.app.state.agent
    if agent is None:
        raise HTTPException(503, "Shopping service is unavailable.")
    return agent


@storefront.get("/catalog")
def catalog(request: Request):
    return search_catalog(database(request), WEBSITE, SearchFilters(in_stock=False, limit=20))


@storefront.post("/chat")
def chat(body: WebsiteChat, request: Request):
    from .api import respond_to_shopper

    return respond_to_shopper(request, body.shopper, body.message)


@storefront.get("/cart/{session_id}")
def get_cart(session_id: UUID, request: Request):
    shopper = SessionRequest(session_id=session_id).shopper
    with shopping_agent(request).open_thread(shopper, prune=False) as graph:
        state = graph.get_state(checkpoint_config(shopper)).values
    return {"cart": state.get("cart", {})}


@storefront.put("/cart")
def update_cart(body: CartUpdate, request: Request):
    shopper = body.shopper
    config = checkpoint_config(shopper)
    with shopping_agent(request).open_thread(shopper) as graph:
        cart = dict(graph.get_state(config).values.get("cart", {}))
        if body.quantity:
            product = get_product(database(request), shopper.store, body.sku)
            if product is None or body.quantity > product["stock"]:
                raise HTTPException(409, "That quantity is no longer available.")
            if body.sku not in cart and len(cart) >= 10:
                raise HTTPException(409, "You can add up to ten distinct products.")
            cart[body.sku] = body.quantity
        else:
            cart.pop(body.sku, None)
        graph.update_state(config, {"cart": cart, "pending_quote_id": None}, as_node="finish")
    return {"cart": cart}


@storefront.post("/cart/quote")
def checkout_quote(body: SessionRequest, request: Request):
    shopper = body.shopper
    config = checkpoint_config(shopper)
    with shopping_agent(request).open_thread(shopper) as graph:
        cart = graph.get_state(config).values.get("cart", {})
        try:
            quote = quote_cart(database(request), shopper, cart)
        except CheckoutError as exc:
            raise HTTPException(409, str(exc)) from exc
        graph.update_state(config, {"pending_quote_id": quote["quote_id"]}, as_node="finish")
    return quote


@storefront.post("/cart/confirm")
def confirm_quote(body: OrderConfirmation, request: Request):
    shopper = body.shopper
    config = checkpoint_config(shopper)
    quote_id = str(body.quote_id)
    with shopping_agent(request).open_thread(shopper) as graph:
        state = graph.get_state(config).values
        is_pending = state.get("pending_quote_id") == quote_id
        if not is_pending and state.get("last_order_quote_id") != quote_id:
            raise HTTPException(409, "Review your current cart before placing an order.")
        try:
            order = place_order(database(request), shopper, confirmed_quote_id=body.quote_id, confirmation_message="Confirm order")
        except CheckoutError as exc:
            raise HTTPException(409, str(exc)) from exc
        if is_pending:
            graph.update_state(config, {"cart": {}, "pending_quote_id": None, "last_order_quote_id": quote_id}, as_node="finish")
    return order


@storefront.post("/cart/cancel")
def cancel_quote(body: SessionRequest, request: Request):
    shopper = body.shopper
    with shopping_agent(request).open_thread(shopper) as graph:
        graph.update_state(checkpoint_config(shopper), {"pending_quote_id": None}, as_node="finish")
    return {"ok": True}


@seller.post("/login")
def login(body: SellerLogin, request: Request):
    retry_after = request.app.state.seller_login_limiter.retry_after("seller")
    if retry_after is not None:
        return rate_limit_response(retry_after)
    if not matches_secret(body.password, get_setting("ADMIN_PASSWORD")):
        raise HTTPException(401, "Incorrect seller password.")
    return {"ok": True}


@seller.get("/dashboard")
def dashboard(request: Request):
    db = database(request)
    with db.connect() as conn:
        orders = conn.execute(
            """
            SELECT id AS order_id, items, total_cents, created_at
            FROM orders WHERE channel = %s AND store_id = %s
            ORDER BY created_at DESC, id LIMIT 50
            """,
            (WEBSITE.channel, WEBSITE.store_id),
        ).fetchall()
    return {"products": search_catalog(db, WEBSITE, SearchFilters(in_stock=False, limit=20)), "orders": orders}


@seller.patch("/inventory/{sku}")
def update_inventory(sku: Sku, body: StockUpdate, request: Request):
    try:
        with database(request).connect() as conn:
            product = conn.execute(
                "UPDATE products SET stock = %s WHERE channel = %s AND store_id = %s AND sku = %s RETURNING sku, stock",
                (body.stock, WEBSITE.channel, WEBSITE.store_id, sku),
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(503, "Inventory is temporarily unavailable.") from exc
    if product is None:
        raise HTTPException(404, "Product not found.")
    return product


internal_router.include_router(storefront)
internal_router.include_router(seller)
