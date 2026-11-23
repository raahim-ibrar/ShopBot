from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .db import Database
from .identity import Shopper
from .safety import confirms_checkout


class CheckoutError(ValueError):
    """A checkout that requires a new quote or a different cart."""


def _lock_cart_products(conn, shopper: Shopper, quantities: dict[str, int]) -> list[dict]:
    products = conn.execute(
        """
        SELECT sku, name, price_cents, stock FROM products
        WHERE channel = %s AND store_id = %s AND sku = ANY(%s)
        ORDER BY sku FOR UPDATE
        """,
        (shopper.store.channel, shopper.store.store_id, list(quantities)),
    ).fetchall()
    if len(products) != len(quantities):
        raise CheckoutError("A cart item is no longer available in this store.")

    items = []
    for product in products:
        quantity = quantities[product["sku"]]
        if product["stock"] < quantity:
            raise CheckoutError(f"Insufficient stock for {product['sku']}.")
        items.append({
            "sku": product["sku"],
            "name": product["name"],
            "quantity": quantity,
            "unit_price_cents": product["price_cents"],
        })
    return items


def quote_cart(db: Database, shopper: Shopper, cart: dict[str, int]) -> dict:
    """Capture what the shopper must review and explicitly confirm on a later turn."""
    if not 1 <= len(cart) <= 10:
        raise CheckoutError("Choose between one and ten different products.")
    if any(type(quantity) is not int or not 1 <= quantity <= 10 for quantity in cart.values()):
        raise CheckoutError("Use a quantity between 1 and 10 for each product.")
    quantities = {sku.strip().upper(): quantity for sku, quantity in cart.items()}
    if len(quantities) != len(cart) or any(not sku or len(sku) > 64 for sku in quantities):
        raise CheckoutError("Each cart item must have a distinct, valid SKU.")

    with db.connect() as conn:
        items = _lock_cart_products(conn, shopper, quantities)
        total = sum(item["unit_price_cents"] * item["quantity"] for item in items)
        quote_id = uuid4()
        expires_at = conn.execute(
            """
            INSERT INTO checkout_quotes (id, channel, store_id, customer_key, thread_key, items, total_cents)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING expires_at
            """,
            (
                quote_id, shopper.store.channel, shopper.store.store_id,
                shopper.customer_key, shopper.thread_key, Jsonb(items), total,
            ),
        ).fetchone()["expires_at"]
    return {
        "quote_id": str(quote_id), "items": items, "total_cents": total,
        "currency": "USD", "expires_at": expires_at.isoformat(),
    }


def place_order(
    db: Database, shopper: Shopper, *, confirmed_quote_id: UUID, confirmation_message: str,
) -> dict:
    """Finalize a pending quote using the shopper's actual follow-up message."""
    if not confirms_checkout(confirmation_message):
        raise CheckoutError("Review the quote and explicitly confirm the order to continue.")

    with db.connect() as conn:
        quote = conn.execute(
            """
            SELECT items, total_cents, expires_at <= now() AS expired
            FROM checkout_quotes
            WHERE id = %s AND channel = %s AND store_id = %s AND customer_key = %s AND thread_key = %s
            FOR UPDATE
            """,
            (
                confirmed_quote_id, shopper.store.channel, shopper.store.store_id,
                shopper.customer_key, shopper.thread_key,
            ),
        ).fetchone()
        if quote is None:
            raise CheckoutError("No matching checkout quote for this conversation.")

        existing = conn.execute(
            "SELECT id FROM orders WHERE quote_id = %s", (confirmed_quote_id,),
        ).fetchone()
        if existing is not None:
            order_id = existing["id"]
        else:
            if quote["expired"]:
                raise CheckoutError("The checkout quote expired. Review a new quote to continue.")
            quantities = {item["sku"]: item["quantity"] for item in quote["items"]}
            current_items = _lock_cart_products(conn, shopper, quantities)
            if current_items != quote["items"]:
                raise CheckoutError("The cart details changed. Review a new quote to continue.")

            with conn.cursor() as cursor:
                cursor.executemany(
                    "UPDATE products SET stock = stock - %s WHERE channel = %s AND store_id = %s AND sku = %s",
                    [
                        (quantity, shopper.store.channel, shopper.store.store_id, sku)
                        for sku, quantity in quantities.items()
                    ],
                )
            order_id = uuid4()
            conn.execute(
                """
                INSERT INTO orders (id, quote_id, channel, store_id, customer_key, items, total_cents)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    order_id, confirmed_quote_id, shopper.store.channel, shopper.store.store_id,
                    shopper.customer_key, Jsonb(quote["items"]), quote["total_cents"],
                ),
            )
    return {
        "order_id": str(order_id), "items": quote["items"],
        "total_cents": quote["total_cents"], "currency": "USD",
    }
