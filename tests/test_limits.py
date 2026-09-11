"""app/limits: request body size cap.

Exercises the middleware directly through the ASGI interface rather than
through the app, so both the Content-Length fast path and the streaming
counter are covered without needing a route that reads a huge body."""
import pytest


def _scope(headers):
    return {"type": "http", "method": "POST", "path": "/api/login", "headers": headers}


async def _collect(middleware, scope, messages):
    """Drives the middleware with a canned sequence of receive messages and
    returns the ASGI messages it sent."""
    sent = []
    remaining = list(messages)

    async def receive():
        return remaining.pop(0)

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


class _EchoApp:
    """Minimal downstream app: drains the body, then returns 200."""

    def __init__(self):
        self.body = b""
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        while True:
            message = await receive()
            self.body += message.get("body", b"")
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def test_declared_content_length_parses_header():
    from app.limits import declared_content_length

    assert declared_content_length([(b"content-length", b"123")]) == 123
    assert declared_content_length([(b"Content-Length", b"123")]) == 123
    assert declared_content_length([(b"content-type", b"application/json")]) is None
    assert declared_content_length([(b"content-length", b"not-a-number")]) is None


@pytest.mark.asyncio
async def test_oversized_content_length_is_rejected_before_the_app_runs():
    from app.limits import BodySizeLimitMiddleware

    downstream = _EchoApp()
    middleware = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = _scope([(b"content-length", b"5000")])

    sent = await _collect(middleware, scope, [])

    assert sent[0]["status"] == 413
    assert downstream.called is False  # the app never ran at all
    assert downstream.body == b""  # the app never saw a single byte


@pytest.mark.asyncio
async def test_streamed_body_over_the_cap_is_rejected_without_content_length():
    from app.limits import BodySizeLimitMiddleware

    downstream = _EchoApp()
    middleware = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = _scope([(b"transfer-encoding", b"chunked")])
    messages = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 60, "more_body": False},
    ]

    sent = await _collect(middleware, scope, messages)

    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_body_within_the_cap_passes_through_untouched():
    from app.limits import BodySizeLimitMiddleware

    downstream = _EchoApp()
    middleware = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = _scope([(b"content-length", b"10")])
    messages = [{"type": "http.request", "body": b"x" * 10, "more_body": False}]

    sent = await _collect(middleware, scope, messages)

    assert sent[0]["status"] == 200
    assert downstream.body == b"x" * 10


@pytest.mark.asyncio
async def test_non_http_scopes_pass_straight_through():
    from app.limits import BodySizeLimitMiddleware

    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    middleware = BodySizeLimitMiddleware(downstream, max_bytes=100)
    await middleware({"type": "lifespan"}, None, None)

    assert seen == ["lifespan"]
