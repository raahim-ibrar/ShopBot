from contextlib import asynccontextmanager
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, StringConstraints

from .agent import UNAVAILABLE, PortPairAgent
from .db import Database, database_url, get_setting
from .identity import Shopper, Store
from .middleware import FixedWindowLimiter, RequestGuardsMiddleware, rate_limit_response
from .model import HaikuModel
from .safety import refusal_for


Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: Identifier
    thread_id: Identifier
    message: Message


class TokenUsage(BaseModel):
    in_: NonNegativeInt = Field(serialization_alias="in")
    out: NonNegativeInt


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]]
    tokens: TokenUsage


router = APIRouter()


@router.get("/health", response_class=PlainTextResponse)
def health(request: Request) -> PlainTextResponse:
    db = request.app.state.database
    if db is not None:
        try:
            with db.connect() as conn:
                ready = conn.execute(
                    """
                    SELECT to_regclass('products') IS NOT NULL
                       AND to_regclass('checkpoints') IS NOT NULL
                       AND to_regclass('store') IS NOT NULL AS ready
                    """,
                ).fetchone()["ready"]
        except psycopg.Error:
            ready = False
        if not ready:
            return PlainTextResponse("unavailable", status_code=503)
    return PlainTextResponse("ok")


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request) -> ChatResponse | Response:
    return respond_to_shopper(request, Shopper(Store("grading"), body.user_id, body.thread_id), body.message)


def respond_to_shopper(request: Request, shopper: Shopper, message: str) -> ChatResponse | Response:
    retry_after = request.app.state.shopper_limiter.retry_after(shopper.customer_key)
    if retry_after is not None:
        return rate_limit_response(retry_after)

    refusal = refusal_for(message)
    if refusal:
        return ChatResponse(reply=refusal, tool_calls=[], tokens=TokenUsage(in_=0, out=0))

    agent = request.app.state.agent
    if agent is None:
        return ChatResponse(
            reply="The PortPair API is running. Shopping features are being set up.",
            tool_calls=[], tokens=TokenUsage(in_=0, out=0),
        )

    try:
        turn = agent.respond(shopper, message)
    except psycopg.Error:
        return ChatResponse(reply=UNAVAILABLE, tool_calls=[], tokens=TokenUsage(in_=0, out=0))
    return ChatResponse(
        reply=turn["reply"], tool_calls=turn["tool_calls"],
        tokens=TokenUsage(in_=turn["tokens_in"], out=turn["tokens_out"]),
    )


def create_app(
    db: Database | None = None, *, agent: PortPairAgent | None = None,
    ip_rate: int = 120, shopper_rate: int = 30,
) -> FastAPI:
    from .storefront import internal_router

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if db is None or db.pool is None:
            yield
            return
        with db.pool:
            yield

    api = FastAPI(title="PortPair Agent API", lifespan=lifespan)
    api.state.database = db
    api.state.agent = agent
    api.state.shopper_limiter = FixedWindowLimiter(shopper_rate)
    api.add_middleware(RequestGuardsMiddleware, ip_limit=ip_rate)
    api.include_router(router)
    api.include_router(internal_router)
    api.state.seller_login_limiter = FixedWindowLimiter(12)

    @api.exception_handler(psycopg.Error)
    def database_error(request: Request, exc: psycopg.Error):
        return JSONResponse({"detail": "Store data is temporarily unavailable. Please try again."}, status_code=503)

    return api


def configured_app() -> FastAPI:
    url = database_url()
    db = Database(url).with_connection_pool() if url else None
    api_key = get_setting("ANTHROPIC_API_KEY")
    stage = get_setting("PORTPAIR_MODEL_STAGE") or "development"
    if stage not in {"development", "evaluation"}:
        raise ValueError("PORTPAIR_MODEL_STAGE must be development or evaluation.")
    agent = PortPairAgent(db, HaikuModel(db, api_key, stage=stage)) if db and api_key else None
    return create_app(db, agent=agent)


app = configured_app()
