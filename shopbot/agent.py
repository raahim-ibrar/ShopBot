import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import TypedDict
from uuid import UUID

import psycopg
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from .agent_tools import TOOLS, TOOL_INPUTS
from .budget import ModelBudgetExceeded
from .catalog import get_product, search_catalog
from .db import Database
from .identity import Shopper
from .memory import checkpoint_config, get_preferences, memory_backends, save_preferences
from .model import ModelUnavailable, ShoppingModel
from .orders import CheckoutError, place_order, quote_cart
from .safety import SENSITIVE_CONTENT, asks_for_saved_device, confirms_checkout, refusal_for


LOGGER = logging.getLogger(__name__)
MAX_TOOLS_PER_TURN = 4
DEFAULT_REPLY = "I can help with PortPair products, compatibility, stock, and demo orders. What are you looking for?"
UNAVAILABLE = "I couldn't complete that shopping request right now. Please try again."


class ShoppingState(TypedDict, total=False):
    history: list[dict[str, str]]
    last_skus: list[str]
    cart: dict[str, int]
    pending_quote_id: str | None
    last_order_quote_id: str | None
    actions: list[dict]
    reply: str
    tool_calls: list[dict]
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class Turn:
    shopper: Shopper
    message: str


def _amount(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def build_graph(db: Database, model: ShoppingModel, checkpointer, store):
    def intake(state: ShoppingState, runtime: Runtime[Turn]) -> dict:
        message = runtime.context.message
        refusal = refusal_for(message)
        if refusal:
            return {"reply": refusal}
        if asks_for_saved_device(message):
            device = get_preferences(store, runtime.context.shopper).device
            reply = f"Your saved device is {device}." if device else "You don't have a saved device yet."
            return {"reply": reply, "pending_quote_id": None}
        if confirms_checkout(message):
            if state.get("pending_quote_id"):
                return {}
            if state.get("cart") and "order" in message.casefold():
                return {"actions": [{"name": "prepare_checkout", "input": {}}]}
            return {"reply": "There is no order waiting for confirmation. Ask me to prepare your cart first."}
        if message.casefold().strip(" .!") in {"cancel", "cancel checkout", "cancel my order"}:
            return {"pending_quote_id": None, "reply": "I cleared the pending checkout. Your cart is still available."}
        return {"pending_quote_id": None}

    def route_intake(state: ShoppingState, runtime: Runtime[Turn]) -> str:
        if state["reply"]:
            return "finish"
        if state["actions"]:
            return "execute_tools"
        return "checkout" if confirms_checkout(runtime.context.message) else "sales_agent"

    def sales_agent(state: ShoppingState, runtime: Runtime[Turn]) -> dict:
        preferences = get_preferences(store, runtime.context.shopper).model_dump(exclude_none=True)
        context = {
            "shopper_message": runtime.context.message,
            "recent_turns": state.get("history", []),
            "prior_result_skus_in_order": state.get("last_skus", []),
            "cart": state.get("cart", {}),
            "shopper_preferences": preferences,
        }
        try:
            model_reply = model.reply([{"role": "user", "content": json.dumps(context)}], TOOLS, role="sales")
        except ModelBudgetExceeded:
            return {"reply": "This request exceeds PortPair's current model limit. Try a shorter question or try again later."}
        except ModelUnavailable as exc:
            LOGGER.warning("%s", exc)
            return {"reply": UNAVAILABLE}

        actions = [block for block in model_reply.blocks if block["type"] == "tool_use"][:MAX_TOOLS_PER_TURN]
        return {
            "actions": actions, "reply": "" if actions else (model_reply.text or DEFAULT_REPLY),
            "tokens_in": model_reply.input_tokens, "tokens_out": model_reply.output_tokens,
        }

    def execute_tools(state: ShoppingState, runtime: Runtime[Turn]) -> dict:
        shopper = runtime.context.shopper
        cart = dict(state.get("cart", {}))
        calls = []
        changes: dict = {}
        quote = None
        requested_checkout = False
        for action in state["actions"]:
            name = action["name"]
            if name not in TOOL_INPUTS or SENSITIVE_CONTENT.search(json.dumps(action.get("input"))):
                continue
            call = {"name": name, "arguments": {}}
            try:
                arguments = TOOL_INPUTS[name].model_validate(action["input"])
                call["arguments"] = arguments.model_dump(exclude_none=True)
                if name == "search_catalog":
                    result = search_catalog(db, shopper.store, arguments)
                    changes["last_skus"] = [product["sku"] for product in result]
                elif name == "get_product":
                    result = get_product(db, shopper.store, arguments.sku)
                elif name == "set_cart":
                    new_cart = {item.sku.upper(): item.quantity for item in arguments.items}
                    if len(new_cart) != len(arguments.items):
                        raise CheckoutError("Each cart item must have a distinct SKU.")
                    if any(get_product(db, shopper.store, sku) is None for sku in new_cart):
                        raise CheckoutError("A cart item was not found in this store.")
                    cart = new_cart
                    result = {"cart": cart}
                elif name == "remember_preferences":
                    save_preferences(store, shopper, arguments)
                    result = {"saved": list(arguments.model_dump(exclude_none=True))}
                else:
                    requested_checkout = True
                    continue
                call.update(status="ok", result=result)
            except ValidationError:
                call.update(status="error", result="Invalid tool arguments.")
            except CheckoutError as exc:
                call.update(status="error", result=str(exc))
            except psycopg.Error:
                call.update(status="error", result="The store data is temporarily unavailable.")
            calls.append(call)

        if requested_checkout:
            call = {"name": "prepare_checkout", "arguments": {}}
            try:
                quote = quote_cart(db, shopper, cart)
                call.update(status="ok", result={key: value for key, value in quote.items() if key != "quote_id"})
            except CheckoutError as exc:
                call.update(status="error", result=str(exc))
            except psycopg.Error:
                call.update(status="error", result="The checkout service is temporarily unavailable.")
            calls.append(call)

        if quote:
            lines = ", ".join(f"{item['quantity']} × {item['name']}" for item in quote["items"])
            changes.update(
                pending_quote_id=quote["quote_id"],
                reply=f"Your demo order is {lines}. Total: {_amount(quote['total_cents'])} USD. Reply “Confirm order” to place it.",
            )
        return {**changes, "cart": cart, "tool_calls": calls}

    def reply_agent(state: ShoppingState, runtime: Runtime[Turn]) -> dict:
        calls = state["tool_calls"]
        verified = {"shopper_message": runtime.context.message, "verified_tools": calls}
        try:
            response = model.reply([{"role": "user", "content": json.dumps(verified)}], [], role="reply")
        except (ModelBudgetExceeded, ModelUnavailable) as exc:
            LOGGER.warning("PortPair reply fallback (%s)", type(exc).__name__)
            return {"reply": _tool_summary(calls)}
        return {
            "reply": response.text or _tool_summary(calls),
            "tokens_in": state["tokens_in"] + response.input_tokens,
            "tokens_out": state["tokens_out"] + response.output_tokens,
        }

    def checkout(state: ShoppingState, runtime: Runtime[Turn]) -> dict:
        turn = runtime.context
        try:
            order = place_order(
                db, turn.shopper, confirmed_quote_id=UUID(state["pending_quote_id"]),
                confirmation_message=turn.message,
            )
        except CheckoutError as exc:
            return {"reply": str(exc), "pending_quote_id": None}
        except psycopg.Error:
            return {"reply": "Checkout is temporarily unavailable. Please confirm again to retry the same order."}
        return {
            "cart": {}, "pending_quote_id": None,
            "last_order_quote_id": state["pending_quote_id"],
            "reply": f"Demo order {order['order_id']} placed for {_amount(order['total_cents'])} USD.",
            "tool_calls": [{"name": "place_order", "arguments": {}, "status": "ok", "result": order}],
        }

    def finish(state: ShoppingState, runtime: Runtime[Turn]) -> dict:
        message = runtime.context.message
        reply = state["reply"] or DEFAULT_REPLY
        if SENSITIVE_CONTENT.search(reply):
            reply = DEFAULT_REPLY
        history = state.get("history", [])
        if not refusal_for(message):
            history = [*history, {"shopper": message[:600], "assistant": reply[:600]}][-3:]
        return {"reply": reply, "history": history, "actions": []}

    builder = StateGraph(ShoppingState, context_schema=Turn)
    builder.add_node("intake", intake)
    builder.add_node("sales_agent", sales_agent)
    builder.add_node("execute_tools", execute_tools)
    builder.add_node("reply_agent", reply_agent)
    builder.add_node("checkout", checkout)
    builder.add_node("finish", finish)
    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", route_intake, ["finish", "checkout", "sales_agent", "execute_tools"])
    builder.add_conditional_edges("sales_agent", lambda state: "execute_tools" if state["actions"] else "finish", ["execute_tools", "finish"])
    builder.add_conditional_edges("execute_tools", lambda state: "finish" if state["reply"] else "reply_agent", ["finish", "reply_agent"])
    builder.add_edge("checkout", "finish")
    builder.add_edge("reply_agent", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)


def _tool_summary(calls: list[dict]) -> str:
    for call in reversed(calls):
        if call["status"] == "error":
            return call["result"]
        if call["name"] == "search_catalog":
            products = call["result"]
            return "; ".join(f"{p['name']} ({_amount(p['price_cents'])})" for p in products) or "No matching products found."
        if call["name"] == "get_product" and call["result"]:
            product = call["result"]
            return f"{product['name']} costs {_amount(product['price_cents'])}. In stock: {product['stock']}."
    return UNAVAILABLE


def run_turn(graph, shopper: Shopper, message: str) -> ShoppingState:
    return graph.invoke(
        {"reply": "", "tool_calls": [], "actions": [], "tokens_in": 0, "tokens_out": 0},
        config=checkpoint_config(shopper), context=Turn(shopper, message),
    )


class PortPairAgent:
    def __init__(self, db: Database, model: ShoppingModel):
        self.db = db
        self.model = model
        self.thread_locks = [Lock() for _ in range(64)]

    @contextmanager
    def open_thread(self, shopper: Shopper, *, prune: bool = True):
        lock = self.thread_locks[int(shopper.thread_key[:2], 16) % len(self.thread_locks)]
        with lock, memory_backends(self.db) as (checkpointer, store):
            graph = build_graph(self.db, self.model, checkpointer, store)
            yield graph
            if not prune:
                return
            try:
                checkpointer.prune([shopper.thread_key], strategy="keep_latest")
            except psycopg.Error:
                LOGGER.warning("PortPair checkpoint pruning failed")

    def respond(self, shopper: Shopper, message: str) -> ShoppingState:
        with self.open_thread(shopper) as graph:
            return run_turn(graph, shopper, message)
