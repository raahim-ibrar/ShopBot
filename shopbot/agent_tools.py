from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .catalog import SearchFilters
from .memory import ShoppingPreferences


Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductArgs(ToolArgs):
    sku: Sku


class CartLine(ToolArgs):
    sku: Sku
    quantity: int = Field(ge=1, le=10, strict=True)


class CartArgs(ToolArgs):
    items: list[CartLine] = Field(max_length=10)


TOOL_INPUTS = {
    "search_catalog": SearchFilters,
    "get_product": ProductArgs,
    "set_cart": CartArgs,
    "prepare_checkout": ToolArgs,
    "remember_preferences": ShoppingPreferences,
}

TOOL_DESCRIPTIONS = {
    "search_catalog": "Find products in this store. Filter by device (e.g. iPhone 15), category, max price in USD cents, and availability. Use short product terms or a category. For details about a single exact SKU, prefer get_product.",
    "get_product": "Find current details and stock for one exact SKU. Resolve references like 'the second one' from the prior results first.",
    "set_cart": "Replace this shopper's current cart with the desired complete list of items and quantities. Use an empty list to clear it. A cart change does not place an order.",
    "prepare_checkout": "Obtain a quote for the current cart. For an immediate checkout request naming products but no cart yet, call set_cart first in this same turn. A quote requires separate user confirmation.",
    "remember_preferences": "Save only a stable shopping device, category or usual budget. Do not save personal details or this trip's temporary budget.",
}

TOOLS = [
    {"name": name, "description": TOOL_DESCRIPTIONS[name], "input_schema": model.model_json_schema()}
    for name, model in TOOL_INPUTS.items()
]
