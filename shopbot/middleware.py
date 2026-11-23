from math import ceil
from threading import Lock
from time import monotonic
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class FixedWindowLimiter:
    def __init__(self, limit: int, *, seconds: int = 60, max_keys: int = 8192):
        self.limit = limit
        self.seconds = seconds
        self.max_keys = max_keys
        self.window = -1
        self.counts: dict[str, int] = {}
        self.lock = Lock()

    def retry_after(self, key: str) -> int | None:
        with self.lock:
            now = monotonic()
            window = int(now // self.seconds)
            if self.window != window:
                self.window = window
                self.counts.clear()

            hits = self.counts.get(key)
            if (hits is None and len(self.counts) >= self.max_keys) or hits == self.limit:
                return max(1, ceil((window + 1) * self.seconds - now))
            self.counts[key] = (hits or 0) + 1
            return None


def rate_limit_response(retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"detail": "Too many requests. Try again shortly."},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


class RequestGuardsMiddleware:
    def __init__(self, app: ASGIApp, *, ip_limit: int = 120, max_body_bytes: int = 65_536):
        self.app = app
        self.ip_limiter = FixedWindowLimiter(ip_limit)
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex

        async def with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        if scope["method"] != "POST" or scope["path"] != "/chat":
            await self.app(scope, receive, with_request_id)
            return

        client = scope.get("client")
        retry_after = self.ip_limiter.retry_after(client[0] if client else "unknown")
        if retry_after is not None:
            await rate_limit_response(retry_after)(scope, receive, with_request_id)
            return

        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            chunk = event.get("body", b"")
            if len(body) + len(chunk) > self.max_body_bytes:
                response = JSONResponse({"detail": "Chat request is too large."}, status_code=413)
                await response(scope, receive, with_request_id)
                return
            body.extend(chunk)
            if not event.get("more_body", False):
                break

        replayed = False

        async def replay_body() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_body, with_request_id)
