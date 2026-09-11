"""Single-user auth: password login, server-side sessions with expiry, sliding
renewal, and revocation.

The session token is random (`secrets.token_urlsafe(32)`) and stored server-side
in the `sessions` table — it is NOT derived from APP_PASSWORD, so a leaked
password no longer lets an attacker compute a valid cookie offline, and a
session can be individually revoked (see `logout`)."""
import datetime
import hmac
import secrets

from fastapi import HTTPException, Request, Response

import database as db
from config import APP_PASSWORD, SESSION_MAX_DAYS, SESSION_TTL_DAYS

COOKIE_NAME = "ontrack_session"

_COOKIE_KWARGS = dict(httponly=True, samesite="lax", secure=True)


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


def verify_password(password: str) -> bool:
    return hmac.compare_digest(password, APP_PASSWORD)


def create_session() -> str:
    """Generates a new session, stores it, and returns the token. Caller sets
    the cookie (see `set_session_cookie`)."""
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    expires_at = now + datetime.timedelta(days=SESSION_TTL_DAYS)
    db.create_session(token, _iso(now), _iso(expires_at))
    return token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL_DAYS * 86400, **_COOKIE_KWARGS)


def logout(token: str) -> None:
    """Deletes exactly the session identified by `token`. A missing/unknown
    token is a no-op — logout is idempotent."""
    if token:
        db.delete_session(token)


def require_auth(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME, "")
    session = db.get_session(token) if token else None
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    now = _utcnow()
    expires_at = _parse(session["expires_at"])
    if now >= expires_at:
        db.delete_session(token)
        raise HTTPException(status_code=401, detail="Session expired")

    # Absolute lifetime cap: sliding renewal below only ever advances
    # expires_at, never created_at, so without this an actively-used (or
    # stolen-and-replayed) cookie would renew forever. Checked before renewal
    # so a session past the cap is rejected regardless of how far renewal has
    # already pushed expires_at into the future.
    created_at = _parse(session["created_at"])
    if now - created_at > datetime.timedelta(days=SESSION_MAX_DAYS):
        db.delete_session(token)
        raise HTTPException(status_code=401, detail="Session expired")

    # Sliding renewal: once a session is more than halfway to expiry, extend it.
    # Keeps normal use from logging the user out mid-session while still
    # bounding how long a stolen cookie stays valid if never used again.
    half_life = (expires_at - created_at) / 2
    if now - created_at > half_life:
        new_expires_at = now + datetime.timedelta(days=SESSION_TTL_DAYS)
        db.update_session_expiry(token, _iso(new_expires_at))
        set_session_cookie(response, token)


def has_valid_session(request: Request) -> bool:
    """Read-only validity check — does NOT renew or delete anything, unlike
    require_auth. Used by the login lockout (see app/api.py) so a request that
    already carries a still-valid session cookie is never blocked by a lockout
    an unauthenticated attacker triggered against the login endpoint."""
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return False
    session = db.get_session(token)
    if session is None:
        return False
    now = _utcnow()
    expires_at = _parse(session["expires_at"])
    if now >= expires_at:
        return False
    created_at = _parse(session["created_at"])
    if now - created_at > datetime.timedelta(days=SESSION_MAX_DAYS):
        return False
    return True
