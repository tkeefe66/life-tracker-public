"""FastAPI app factory."""
import datetime
import logging
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database as db
from app.auth import create_session, has_valid_session, set_session_cookie, verify_password
from app.limits import MAX_BODY_BYTES, BodySizeLimitMiddleware
from services.telegram_notify import notify_background

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    # style-src allows 'unsafe-inline' because React writes inline style
    # attributes; script-src deliberately does NOT, which is the directive
    # that actually matters. img-src allows data: for inline SVG/icon URIs.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


def cache_control_for_path(path: str) -> Optional[str]:
    """Cache-Control decision for a response path — a pure function so the
    policy is unit-testable without spinning up the ASGI app.

    - `/api/*`: `no-store, private`. These responses carry bank transactions,
      health check-ins, and calendar contents. Leaving the header absent
      relied on browsers not disk-caching fetch() responses by default —
      true today, but an implicit assumption rather than a stated policy,
      and any intermediary treating "no header" as cacheable would be free
      to store financial data.
    - `/assets/*`: vite content-hashes these filenames on every build, so a
      given URL's content never changes — safe to cache for a year and mark
      immutable.
    - everything else (the SPA shell `index.html`, served both at `/` and as
      the StaticFiles(html=True) fallback for unmatched client-side routes):
      `no-cache`, NOT `no-store`. `no-cache` still forces revalidation on
      every load (so a stale shell can't keep pointing at last deploy's
      hashed asset filenames — the bug this fixes), but the ETag StaticFiles
      already sets makes that revalidation a cheap 304 instead of a full
      re-fetch. `no-store` would throw that away for no benefit.
    """
    if path.startswith("/api/"):
        return "no-store, private"
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"
    return "no-cache"

# ── Login throttling ──────────────────────────────────────────────────────────
# State lives in app_settings (not memory) so it survives a redeploy. Single-user
# app: a global counter is correct — per-IP tracking would be trivially bypassed
# anyway and adds complexity for no real benefit here.
#
# Concurrency: `login()` is a sync `def`, so Starlette dispatches it onto the
# anyio threadpool (default up to 40 workers) rather than running it on the
# event loop. `_LOGIN_LOCK` serializes the whole check-verify-record sequence
# across those worker threads within this one process — this is a single
# Railway service/deploy (see CLAUDE.md), so a process-wide lock is sufficient;
# it would NOT be if this ever ran as multiple replicas sharing one DB.
#
# Manual recovery: if the owner is ever locked out and needs an emergency
# reset (e.g. to rule out an active attack before waiting it out), clear the
# three app_settings rows: `login_fail_count`, `login_locked_until`,
# `login_last_fail_at` — e.g. via `scripts/cleardb.py`-style direct SQL:
#   DELETE FROM app_settings WHERE key IN
#     ('login_fail_count', 'login_locked_until', 'login_last_fail_at');
# A *successful* login from any device holding a still-valid session cookie
# also bypasses the lockout entirely (see `has_valid_session` below and M2a
# in `login()`) — but only on success. A wrong password from that same
# session-holding device is still recorded and, once the threshold is
# crossed, is refused with 429 exactly like a session-less attacker's guess
# — a stolen session cookie must not grant unlimited free password guesses.
# An abandoned attack (no failures for 30+ minutes) heals itself automatically.
LOGIN_FAIL_COUNT_KEY = "login_fail_count"
LOGIN_LOCKED_UNTIL_KEY = "login_locked_until"
LOGIN_LAST_FAIL_AT_KEY = "login_last_fail_at"
LOCKOUT_THRESHOLD = 5
LOCKOUT_BASE_SECONDS = 60
LOCKOUT_MAX_SECONDS = 15 * 60
LOGIN_FAIL_RESET_MINUTES = 30

_LOGIN_LOCK = threading.Lock()


def _utcnow() -> datetime.datetime:
    """Seam for tests — monkeypatch this, not the clock itself."""
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def _parse(value: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _is_locked_out() -> bool:
    locked_until = db.get_setting(LOGIN_LOCKED_UNTIL_KEY)
    return bool(locked_until and _utcnow() < _parse(locked_until))


def _record_login_failure() -> None:
    now = _utcnow()

    # Self-healing: if the last failure was long enough ago, treat this one as
    # a fresh start rather than letting a stale, already-abandoned attack keep
    # extending the lockout indefinitely (one guess a minute is slower than any
    # lockout window, so without this the count — and the lock — never resets).
    last_fail_raw = db.get_setting(LOGIN_LAST_FAIL_AT_KEY)
    if last_fail_raw and now - _parse(last_fail_raw) > datetime.timedelta(minutes=LOGIN_FAIL_RESET_MINUTES):
        db.set_setting(LOGIN_FAIL_COUNT_KEY, "0")
        db.set_setting(LOGIN_LOCKED_UNTIL_KEY, "")

    count = int(db.get_setting(LOGIN_FAIL_COUNT_KEY, "0") or "0") + 1
    db.set_setting(LOGIN_FAIL_COUNT_KEY, str(count))
    db.set_setting(LOGIN_LAST_FAIL_AT_KEY, _iso(now))
    logger.warning("Failed login attempt (count=%d)", count)
    if count >= LOCKOUT_THRESHOLD:
        extra = count - LOCKOUT_THRESHOLD
        seconds = min(LOCKOUT_BASE_SECONDS * (2 ** extra), LOCKOUT_MAX_SECONDS)
        db.set_setting(LOGIN_LOCKED_UNTIL_KEY, _iso(now + datetime.timedelta(seconds=seconds)))
        if count == LOCKOUT_THRESHOLD:
            # Only on the crossing. Past the threshold every further guess
            # would otherwise send its own message, turning a sustained
            # attack into a notification flood. Background thread because
            # this runs under _LOGIN_LOCK — see notify_background's docstring.
            # The attempted password is deliberately absent from the text.
            notify_background(
                f"On Track: login locked after {count} failed attempts. "
                f"If this wasn't you, rotate APP_PASSWORD and use "
                f"Settings -> Sign out everywhere."
            )


def _record_login_success() -> None:
    db.set_setting(LOGIN_FAIL_COUNT_KEY, "0")
    db.set_setting(LOGIN_LOCKED_UNTIL_KEY, "")
    db.set_setting(LOGIN_LAST_FAIL_AT_KEY, "")


class LoginBody(BaseModel):
    password: str = Field(max_length=200)


def create_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="On Track", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    # Registered BEFORE the security_headers decorator below, which means
    # security_headers ends up OUTSIDE it in the middleware stack (Starlette
    # makes the most recently added middleware outermost). That ordering is
    # deliberate: a 413 still gets the standard security headers.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        cache_control = cache_control_for_path(request.url.path)
        if cache_control is not None:
            response.headers["Cache-Control"] = cache_control
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response):
        # The whole check-verify-record sequence runs under one process-wide
        # lock — see the "Login throttling" section comment above for why a
        # lock (rather than just an atomic counter) is both necessary and
        # sufficient here.
        with _LOGIN_LOCK:
            already_locked = _is_locked_out()
            # The lockout short-circuits an attempt (blocking it before
            # verify_password ever runs) only for a session-less client that
            # is already locked out — this is deliberate, not just an
            # optimization: if verify_password ran first here, a locked-out
            # attacker who happens to guess correctly would learn "that
            # password was right" immediately, faster than waiting out the
            # window like every wrong guess has to. A request that already
            # carries a still-valid session cookie skips this pre-check — the
            # owner's already-authenticated device can always attempt to
            # (re-)log in (M2a) — but that's the ONLY exemption a session
            # buys: a wrong password from that same device still falls
            # through to the record-and-check below like anyone else's.
            if already_locked and not has_valid_session(request):
                raise HTTPException(status_code=429, detail="Too many failed attempts — try again later")
            if verify_password(body.password):
                # Success always wins, regardless of lockout state (M2a) —
                # this is the only path exempt from the lockout entirely.
                _record_login_success()
                token = create_session()
                set_session_cookie(response, token)
                return {"ok": True}
            _record_login_failure()
            if already_locked:
                # Already locked before this attempt (e.g. a session-holding
                # client whose own prior failures tipped the counter over the
                # threshold) — no exemption for a wrong password.
                raise HTTPException(status_code=429, detail="Too many failed attempts — try again later")
            raise HTTPException(status_code=401, detail="Wrong password")

    from app.routes import router  # imported late so routes can import database freely
    app.include_router(router, prefix="/api")

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
    return app
