"""The redaction boundary: maps an exception to a value from a small closed set.

This is the pattern the future SimpleFIN bank integration gets built on. A
SimpleFIN access URL carries its credentials *inside the URL itself*, and HTTP
client libraries routinely put the request URL into exception messages (Gmail
already leaked a full request URL this way once). Ingestion jobs must never
store `str(exception)` anywhere a user can read it.

Rule: prevent the credential-bearing string from being constructed in the first
place; never try to scrub it afterwards. This module never reads `.args`,
`str(exc)`, or any message text — only the exception's *type* and well-known
status-code attributes (`.status_code`, `.response.status_code`, `.resp.status`),
which never carry request URLs or secrets.

Callers are responsible for full-detail logging themselves:

    except Exception as e:
        logger.exception("job failed")           # full detail, server-side only
        db.set_setting("x_last_status", safe_status(e))   # closed-set value only
"""

# Not returned by safe_status() itself — these cover the "we never even tried"
# case that jobs check for *before* entering the try/except that calls
# safe_status(). They're named constants (not ad hoc literals at each call
# site) so the invariant "every status a job ever writes is a CLOSED_SET
# member" is actually enforceable and tested, not just true by convention.
NOT_CONFIGURED = "error: not configured"
GOOGLE_NOT_CONFIGURED = "error: Google not configured"

# A distinctly diagnosable failure category, not a preflight skip: pg_dump
# refuses to dump from a Postgres server newer than itself. Detected from
# vocabulary in pg_dump's own stderr (see
# jobs/backup_db.py._looks_like_pg_dump_version_mismatch) rather than
# safe_status()'s generic exception-type mapping, so the fix is actionable
# from Settings ("pg_dump needs upgrading") instead of a generic "see logs".
PG_DUMP_VERSION_MISMATCH = "error: pg_dump version mismatch"

CLOSED_SET = frozenset({
    "ok",
    "error: auth",
    "error: unreachable",
    "error: rate limited",
    "error: see logs",
    NOT_CONFIGURED,
    GOOGLE_NOT_CONFIGURED,
    PG_DUMP_VERSION_MISMATCH,
})

# Matched against every class name in the exception's MRO, so this recognizes
# third-party exception types (google.auth.exceptions.RefreshError,
# googleapiclient.errors.HttpError, requests/httpx variants, anthropic's SDK
# errors, ...) without importing any of those optional libraries directly.
_AUTH_TYPE_NAMES = {
    "RefreshError", "AuthenticationError", "AuthorizationError",
    "PermissionError", "Unauthorized", "Forbidden",
}
_TIMEOUT_OR_CONNECTION_TYPE_NAMES = {
    "Timeout", "TimeoutError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "ConnectError", "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
    "NewConnectionError", "MaxRetryError", "gaierror",
}


def _status_code(exc: BaseException):
    """Best-effort extraction of an HTTP status code from common client-library
    shapes. Never touches message text — only numeric status attributes."""
    for path in (("status_code",), ("response", "status_code"), ("resp", "status"), ("code",)):
        obj = exc
        for attr in path:
            obj = getattr(obj, attr, None)
            if obj is None:
                break
        if isinstance(obj, int) and not isinstance(obj, bool):
            return obj
    return None


def safe_status(exc: BaseException) -> str:
    """Map `exc` to a value from CLOSED_SET. Never returns text derived from the
    exception's message — see module docstring."""
    status = _status_code(exc)
    if status in (401, 403):
        return "error: auth"
    if status == 429:
        return "error: rate limited"

    type_names = {t.__name__ for t in type(exc).__mro__}
    if type_names & _AUTH_TYPE_NAMES:
        return "error: auth"
    if type_names & _TIMEOUT_OR_CONNECTION_TYPE_NAMES:
        return "error: unreachable"
    # Builtin socket/network failures (ConnectionError, TimeoutError, socket.timeout,
    # socket.gaierror, ...) are all OSError subclasses.
    if isinstance(exc, OSError):
        return "error: unreachable"

    return "error: see logs"
