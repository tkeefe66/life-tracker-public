"""SimpleFIN transport and normalization — and the hardest instance of this
repo's redaction boundary.

A SimpleFIN access URL carries its credentials INSIDE THE URL. httpx puts the
request URL into most of its exception messages, so an exception that escapes
this module would carry the user's bank credentials into a log line, a status
string, or an API response.

The rule (see CLAUDE.md): prevent the credential-bearing string from being
constructed; never scrub it afterwards. Concretely, every call in this module is
wrapped, and the only thing that ever crosses the boundary is `SimpleFinError`,
which carries a `status` from safe_status's CLOSED_SET and NO message text.
Callers must never log the original exception object — this module already did,
server-side, with logger.exception.
"""
import datetime
import logging
import math
import time

import httpx
import pytz

from config import SIMPLEFIN_ACCESS_URL, SIMPLEFIN_LOOKBACK_DAYS, TIMEZONE
from services.safe_status import safe_status

logger = logging.getLogger(__name__)

# Matches the repo-wide convention (services/gmail_service.py, database.py,
# app/scorecard.py, all four jobs): epoch -> local date must go through the
# configured TIMEZONE, never the container's local tz. Railway runs UTC while
# dev runs America/Denver — a naive conversion silently misfiles any
# transaction posted after ~17:00 local into the next day/week.
#
# Resolved at point of use (pytz.timezone(TIMEZONE) inside _epoch_to_day), not
# cached at import — matching services/gmail_service.py. A module-level cache
# is invisible to the repo's own test idiom (conftest.py: monkeypatch.setenv +
# importlib.reload(config)), which reloads config but not every module that
# already imported a value from it at import time.


class SimpleFinError(Exception):
    """Carries a closed-set status and nothing else. Deliberately constructed with
    no message argument so `str(e)` and `e.args` cannot leak the access URL."""

    def __init__(self, status):
        super().__init__()
        self.status = status

    def __str__(self):
        return self.status

    def __repr__(self):
        return f"SimpleFinError({self.status!r})"


def is_configured() -> bool:
    return bool(SIMPLEFIN_ACCESS_URL.strip())


def fetch_accounts(days=None):
    """GET /accounts for the lookback window. Raises SimpleFinError on any failure.

    The URL is built inside the try, used once, and never returned or logged.
    """
    days = SIMPLEFIN_LOOKBACK_DAYS if days is None else days
    start = int(time.time()) - days * 86400
    try:
        resp = httpx.get(f"{SIMPLEFIN_ACCESS_URL.rstrip('/')}/accounts",
                         params={"start-date": start}, timeout=180)
    except Exception as e:
        # logger.exception is safe: Railway logs are server-side only, and the
        # operator needs the real detail. The DB and the API get `status` alone.
        logger.exception("SimpleFIN request failed")
        raise SimpleFinError(safe_status(e)) from None  # `from None`: drop the chained cause

    if resp.status_code != 200:
        logger.error("SimpleFIN returned HTTP %d", resp.status_code)
        # Build a bare object carrying only the code — never the response itself,
        # whose .request holds the credential-bearing URL.
        raise SimpleFinError(safe_status(_StatusOnly(resp.status_code)))

    try:
        return resp.json()
    except Exception:
        logger.exception("SimpleFIN returned a non-JSON body")
        raise SimpleFinError("error: see logs") from None


class _StatusOnly(Exception):
    """A minimal carrier so safe_status can map an HTTP code without ever seeing
    the httpx response (which holds the request URL)."""

    def __init__(self, status_code):
        super().__init__()
        self.status_code = status_code


def _epoch_to_day(value):
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    # 0 and negative epochs are not real posted dates — SimpleFIN bridges
    # commonly emit `posted: 0` for a still-pending transaction. Treat them
    # like a missing value rather than resolving to 1969/1970.
    if value <= 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(value, pytz.timezone(TIMEZONE)).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def normalize(payload):
    """Flatten SimpleFIN's nested payload into (accounts, transactions).

    Balances are dropped here and never propagate further — the most sensitive
    field is safest when it is never stored. `mcc` is absent on roughly
    three-quarters of real transactions (every credit card reports none), so
    every optional field tolerates absence rather than assuming presence.
    """
    accounts, txns = [], []
    if not isinstance(payload, dict):
        return accounts, txns
    for acct in payload.get("accounts", []) or []:
        if not isinstance(acct, dict):
            continue
        sfid = acct.get("id")
        if not sfid:
            continue
        org = acct.get("org") or {}
        if isinstance(org, str):
            # Some bridges send `org` as a bare name string rather than an object.
            org = {"name": org}
        elif not isinstance(org, dict):
            org = {}
        accounts.append({
            "simplefin_id": str(sfid),
            "name": acct.get("name") or "?",
            "org": org.get("name") or org.get("domain") or "",
            # SimpleFIN's own type, stored verbatim. Not all bridges populate it.
            "kind": acct.get("type") or "",
        })
        for t in acct.get("transactions", []) or []:
            if not isinstance(t, dict):
                continue
            tid = t.get("id")
            if not tid:
                continue
            posted = _epoch_to_day(t.get("posted"))
            if not posted:
                continue
            try:
                amount = float(t.get("amount"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(amount):
                continue
            txns.append({
                "simplefin_id": str(tid),
                "account_simplefin_id": str(sfid),
                "posted": posted,
                "transacted_at": _epoch_to_day(t.get("transacted_at")),
                "amount": amount,
                "description": t.get("description") or "",
                "payee": t.get("payee") or "",
                "memo": t.get("memo") or "",
                # Coerced to str: the column is TEXT, and while every real bridge
                # observed so far sends mcc as a string, a bridge that sends it as
                # an int would insert fine in SQLite and raise in Postgres — one
                # bad field failing the entire sync rather than one row. Same
                # falsy-collapses-to-None semantics as the other optional fields.
                "mcc": str(t.get("mcc")) if t.get("mcc") else None,
            })
    return accounts, txns


def _to_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_holdings(payload):
    """Per-account holdings from a SimpleFIN payload — the live-only
    investments view's input. Same defensive posture as normalize(): floats
    coerced (bridges send numerics as strings), non-finite dropped, absent
    fields tolerated. A holding with no market_value cannot be displayed and
    is dropped; accounts with no holdings are omitted (every checking/credit
    account has an empty array). Balances still never cross this boundary."""
    out = []
    if not isinstance(payload, dict):
        return out
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        return out
    for acct in accounts:
        if not isinstance(acct, dict):
            continue
        sfid = acct.get("id")
        if not sfid:
            continue
        holdings_raw = acct.get("holdings")
        if not isinstance(holdings_raw, list):
            continue
        holdings = []
        for h in holdings_raw:
            if not isinstance(h, dict):
                continue
            mv = _to_float(h.get("market_value"))
            if mv is None:
                continue
            holdings.append({
                "symbol": h.get("symbol") or "?",
                "description": h.get("description") or "",
                "shares": _to_float(h.get("shares")),
                "cost_basis": _to_float(h.get("cost_basis")),
                "market_value": mv,
            })
        if not holdings:
            continue
        org = acct.get("org") or {}
        if isinstance(org, str):
            org = {"name": org}
        elif not isinstance(org, dict):
            org = {}
        out.append({
            "simplefin_id": str(sfid),
            "name": acct.get("name") or "?",
            "org": org.get("name") or org.get("domain") or "",
            "holdings": holdings,
        })
    return out
