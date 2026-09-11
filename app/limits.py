"""Request body size limiting.

POST /api/login is reachable without authentication, and Pydantic's
max_length on the password field is only enforced AFTER Starlette has read
and parsed the whole body into memory — so an oversized body is a memory-
exhaustion vector that the login lockout cannot mitigate, because the body is
buffered before the lockout code ever runs.

This is a pure ASGI middleware rather than a @app.middleware("http")
function on purpose: BaseHTTPMiddleware sits above the receive channel and
cannot refuse a request while its body is still arriving. Content-Length is
checked first (cheap, covers every ordinary client); a chunked request that
omits the header is counted as it streams, so an absent or dishonest
Content-Length cannot evade the cap.
"""
import logging
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Every JSON body this app accepts is a handful of short fields — the largest
# is a 500-character bank note. 64 KB is far above any legitimate request and
# far below anything that threatens the process.
MAX_BODY_BYTES = 64 * 1024


class _BodyTooLarge(HTTPException):
    """Raised from the receive wrapper. Subclasses HTTPException because
    FastAPI's body reader wraps `await request.body()` in
    `except Exception -> HTTPException(400)` but re-raises HTTPException
    untouched -- without this base class a chunked oversized body surfaces
    as a misleading 400 instead of a 413."""

    def __init__(self):
        super().__init__(status_code=413, detail="Request body too large")


def declared_content_length(headers) -> Optional[int]:
    """Content-Length from a raw ASGI header list, or None when absent or
    unparseable. A chunked request has no Content-Length; those are caught by
    counting bytes instead."""
    for name, value in headers:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = declared_content_length(scope.get("headers", []))
        if declared is not None and declared > self.max_bytes:
            logger.warning(
                "Rejected request declaring %d bytes (max %d)", declared, self.max_bytes
            )
            await self._reject(send)
            return

        received = 0
        response_started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    logger.warning(
                        "Rejected streamed request body over %d bytes", self.max_bytes
                    )
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            # Only safe to write our own response if the app hasn't begun one.
            # It won't have: it is still waiting on the body it never finished
            # receiving. The guard is here so a future streaming route that
            # responds early can't produce a malformed double-response.
            if not response_started:
                await self._reject(send)
            else:
                # A response already started, so we can't send our own —
                # but silently swallowing here would leave the client with a
                # truncated response and no logged cause. Re-raise so the
                # failure surfaces instead of vanishing.
                raise

    async def _reject(self, send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
