"""services.safe_status: the redaction boundary. Full exception detail is logged
server-side by the caller (logger.exception); this module maps the exception to a
value from a small closed set and must NEVER leak message text — the future
SimpleFIN bank-access URL carries its credentials inside the URL itself, and HTTP
libraries routinely embed the request URL in exception messages."""
import socket

import pytest

from services.safe_status import safe_status


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _HttpLikeError(Exception):
    """Mimics requests/httpx-style errors: a .response with .status_code."""
    def __init__(self, message, status_code):
        super().__init__(message)
        self.response = _FakeResponse(status_code)


class _GoogleHttpLikeError(Exception):
    """Mimics googleapiclient.errors.HttpError: a .resp with .status."""
    class _Resp:
        def __init__(self, status):
            self.status = status

    def __init__(self, message, status):
        super().__init__(message)
        self.resp = self._Resp(status)


class _AnthropicLikeStatusError(Exception):
    """Mimics anthropic.APIStatusError: a direct .status_code attribute."""
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


class RefreshError(Exception):
    """Stand-in for google.auth.exceptions.RefreshError — matched by type name
    only, so this test does not need the real google-auth exception module."""


def test_http_401_maps_to_auth():
    assert safe_status(_HttpLikeError("nope", 401)) == "error: auth"


def test_http_403_maps_to_auth():
    assert safe_status(_GoogleHttpLikeError("nope", 403)) == "error: auth"


def test_anthropic_401_maps_to_auth():
    assert safe_status(_AnthropicLikeStatusError("nope", 401)) == "error: auth"


def test_refresh_error_maps_to_auth():
    assert safe_status(RefreshError("token refresh failed")) == "error: auth"


def test_http_429_maps_to_rate_limited():
    assert safe_status(_HttpLikeError("slow down", 429)) == "error: rate limited"
    assert safe_status(_GoogleHttpLikeError("slow down", 429)) == "error: rate limited"


def test_connection_error_maps_to_unreachable():
    assert safe_status(ConnectionError("connection refused")) == "error: unreachable"


def test_timeout_error_maps_to_unreachable():
    assert safe_status(TimeoutError("timed out")) == "error: unreachable"
    assert safe_status(socket.timeout("timed out")) == "error: unreachable"


def test_generic_os_error_maps_to_unreachable():
    assert safe_status(OSError("network unreachable")) == "error: unreachable"


def test_unmapped_exception_falls_back_to_see_logs():
    assert safe_status(ValueError("weird parse failure")) == "error: see logs"
    assert safe_status(RuntimeError("something odd")) == "error: see logs"


def test_unmapped_http_status_falls_back_to_see_logs():
    assert safe_status(_HttpLikeError("teapot", 418)) == "error: see logs"


@pytest.mark.parametrize("exc_factory", [
    lambda url: RuntimeError(f"Failed to connect to {url}"),
    lambda url: ConnectionError(f"Connection to {url} refused"),
    lambda url: ValueError(f"could not parse response from {url}"),
    lambda url: _HttpLikeError(f"401 Unauthorized for url: {url}", 401),
    lambda url: _GoogleHttpLikeError(f"<HttpError 403 when requesting {url}>", 403),
    lambda url: Exception(url),
])
def test_credential_bearing_url_never_survives_the_boundary(exc_factory):
    """The critical property test: whatever the exception type, whatever it embeds
    in its message, the returned status string must never contain the credential
    or any recognizable fragment of the URL. Assert on the returned value directly
    — not on a regex applied to it, which could be fooled by encoding tricks."""
    url = "https://user:supersecret@bridge.example.com/path?token=abc"
    result = safe_status(exc_factory(url))

    forbidden = ["user", "supersecret", "token", "abc", "http", "bridge.example.com"]
    for fragment in forbidden:
        assert fragment not in result, f"{fragment!r} leaked into safe_status() output: {result!r}"

    # And it must be a member of the closed set — never anything ad hoc.
    assert result in {"ok", "error: auth", "error: unreachable", "error: rate limited", "error: see logs"}


# ── L3: every literal status string written outside safe_status() must still
# be a CLOSED_SET member, via a named constant rather than an ad hoc literal ──

def test_not_configured_constants_are_closed_set_members():
    from services.safe_status import CLOSED_SET, GOOGLE_NOT_CONFIGURED, NOT_CONFIGURED

    assert NOT_CONFIGURED in CLOSED_SET
    assert GOOGLE_NOT_CONFIGURED in CLOSED_SET


def test_job_modules_use_the_shared_constants_not_ad_hoc_literals():
    """jobs/backup_db.py, jobs/scan_gmail.py, jobs/scan_calendar.py, jobs/sync_bank.py
    each write a literal "not configured" status outside the safe_status() call —
    they must reference the named constants (so CLOSED_SET is the single source of
    truth) rather than duplicating the string."""
    from jobs import backup_db, scan_calendar, scan_gmail, sync_bank
    from services.safe_status import GOOGLE_NOT_CONFIGURED, NOT_CONFIGURED

    assert backup_db.NOT_CONFIGURED == NOT_CONFIGURED
    assert scan_gmail.GOOGLE_NOT_CONFIGURED == GOOGLE_NOT_CONFIGURED
    assert scan_calendar.GOOGLE_NOT_CONFIGURED == GOOGLE_NOT_CONFIGURED
    assert sync_bank.NOT_CONFIGURED == NOT_CONFIGURED


def test_safe_status_never_returns_str_of_the_exception():
    """Even for a completely generic exception, the boundary must not fall back to
    str(exc) — that would defeat the whole point on any exception type this module
    doesn't yet recognize."""
    exc = Exception("https://user:hunter2@evil.example.com/secret-token")
    result = safe_status(exc)
    assert str(exc) not in result
    assert result == "error: see logs"
