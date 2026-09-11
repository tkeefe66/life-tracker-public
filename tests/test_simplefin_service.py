"""SimpleFIN transport + normalization. The access URL must never escape."""
import math

import httpx
import pytest


def _payload():
    return {
        "accounts": [
            {
                "id": "acct-1", "name": "EVERYDAY CHECKING ...1001",
                "org": {"name": "Wells Fargo"}, "currency": "USD",
                "balance": "1234.56",
                "transactions": [
                    {"id": "t1", "posted": 1751328000, "transacted_at": 1751241600,
                     "amount": "-14.20", "description": "COFFEE SHOP",
                     "payee": "Coffee Shop", "memo": "", "mcc": "5814"},
                    {"id": "t2", "posted": 1751414400, "amount": "3200.00",
                     "description": "DIRECT DEP"},
                ],
            },
            {"id": "acct-2", "name": "Platinum Card", "org": {"domain": "americanexpress.com"},
             "transactions": []},
        ],
        "errors": [],
    }


def test_normalize_flattens_accounts_and_transactions(monkeypatch):
    from services import simplefin_service
    # Pin the conversion timezone so the expected date below is deterministic
    # regardless of the machine (or CI runner) actually running the test.
    # TIMEZONE is resolved at point of use (pytz.timezone(TIMEZONE) inside
    # _epoch_to_day), so patching the module-level TIMEZONE string is what
    # actually pins behavior — patching a cached tzinfo object would not.
    monkeypatch.setattr(simplefin_service, "TIMEZONE", "America/Denver")
    accounts, txns = simplefin_service.normalize(_payload())

    assert [a["simplefin_id"] for a in accounts] == ["acct-1", "acct-2"]
    assert accounts[0]["org"] == "Wells Fargo"
    assert accounts[1]["org"] == "americanexpress.com"  # falls back to domain
    assert [t["simplefin_id"] for t in txns] == ["t1", "t2"]
    assert txns[0]["account_simplefin_id"] == "acct-1"
    assert txns[0]["amount"] == -14.20
    # epoch 1751328000 == 2025-07-01 05:20:00 UTC == 2025-06-30 23:20:00 in
    # America/Denver (MDT, UTC-6) — the exact expected date, not a tautology.
    assert txns[0]["posted"] == "2025-06-30"
    assert txns[0]["transacted_at"] == "2025-06-29"


def test_epoch_to_day_uses_the_configured_timezone_not_the_machines(monkeypatch):
    """The direct regression test for the naive-fromtimestamp bug: pick an
    epoch that lands on different calendar days in UTC vs. America/Denver and
    assert the configured (pinned) zone wins, independent of the host's TZ."""
    from services import simplefin_service

    epoch = 1751328000  # 2025-07-01 UTC, 2025-06-30 in America/Denver
    monkeypatch.setattr(simplefin_service, "TIMEZONE", "UTC")
    assert simplefin_service._epoch_to_day(epoch) == "2025-07-01"

    monkeypatch.setattr(simplefin_service, "TIMEZONE", "America/Denver")
    assert simplefin_service._epoch_to_day(epoch) == "2025-06-30"


def test_normalize_tolerates_missing_optional_fields():
    """mcc is absent on 74% of real transactions — every card account has none."""
    from services import simplefin_service
    _, txns = simplefin_service.normalize(_payload())
    assert txns[1]["mcc"] is None
    assert txns[1]["payee"] == ""
    assert txns[1]["memo"] == ""
    assert txns[1]["transacted_at"] is None


def test_normalize_coerces_an_integer_mcc_to_string():
    """All 965 rows in the real snapshot are str/None, but the DB column is TEXT
    and nothing upstream guarantees a bridge always sends mcc as a string. An
    uncoerced int inserts fine in SQLite but raises against Postgres — failing
    the whole sync, not one row."""
    from services import simplefin_service
    payload = _payload()
    payload["accounts"][0]["transactions"].append(
        {"id": "t-int-mcc", "posted": 1751328000, "amount": "-9.00", "mcc": 5814}
    )
    _, txns = simplefin_service.normalize(payload)
    t = next(t for t in txns if t["simplefin_id"] == "t-int-mcc")
    assert t["mcc"] == "5814"
    assert isinstance(t["mcc"], str)


def test_normalize_never_returns_a_balance():
    from services import simplefin_service
    accounts, _ = simplefin_service.normalize(_payload())
    for a in accounts:
        assert "balance" not in a


def test_normalize_skips_transactions_without_an_id():
    from services import simplefin_service
    payload = _payload()
    payload["accounts"][0]["transactions"].append({"amount": "-1.00"})
    _, txns = simplefin_service.normalize(payload)
    assert [t["simplefin_id"] for t in txns] == ["t1", "t2"]


@pytest.mark.parametrize("bad_epoch", [0, -1, -1751328000])
def test_epoch_to_day_rejects_zero_and_negative_epochs(bad_epoch):
    """SimpleFIN bridges commonly emit `posted: 0` for a pending transaction —
    naive fromtimestamp(0) resolves to 1969-12-31/1970-01-01 instead of being
    treated as absent."""
    from services import simplefin_service
    assert simplefin_service._epoch_to_day(bad_epoch) is None


def test_normalize_skips_transactions_with_posted_zero():
    from services import simplefin_service
    payload = _payload()
    payload["accounts"][0]["transactions"].append(
        {"id": "t-pending", "posted": 0, "amount": "-5.00"}
    )
    _, txns = simplefin_service.normalize(payload)
    assert [t["simplefin_id"] for t in txns] == ["t1", "t2"]


@pytest.mark.parametrize("bad_amount", ["nan", "inf", "-inf"])
def test_normalize_skips_non_finite_amounts(bad_amount):
    """amount: "nan" parses to a float NaN, which is not valid JSON and would
    poison every downstream sum."""
    from services import simplefin_service
    payload = _payload()
    payload["accounts"][0]["transactions"].append(
        {"id": "t-bad-amount", "posted": 1751328000, "amount": bad_amount}
    )
    _, txns = simplefin_service.normalize(payload)
    assert [t["simplefin_id"] for t in txns] == ["t1", "t2"]
    assert all(math.isfinite(t["amount"]) for t in txns)


@pytest.mark.parametrize("bad_payload", [None, [], "not a dict", 42])
def test_normalize_tolerates_a_non_dict_payload(bad_payload):
    from services import simplefin_service
    accounts, txns = simplefin_service.normalize(bad_payload)
    assert accounts == []
    assert txns == []


def test_normalize_tolerates_a_non_dict_account_entry():
    from services import simplefin_service
    payload = _payload()
    payload["accounts"].append("not an account object")
    accounts, txns = simplefin_service.normalize(payload)
    assert [a["simplefin_id"] for a in accounts] == ["acct-1", "acct-2"]


def test_normalize_treats_a_string_org_as_the_org_name():
    from services import simplefin_service
    payload = _payload()
    payload["accounts"][0]["org"] = "Wells Fargo"
    accounts, _ = simplefin_service.normalize(payload)
    assert accounts[0]["org"] == "Wells Fargo"


def test_not_configured_when_url_is_blank(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "")
    assert simplefin_service.is_configured() is False


@pytest.mark.parametrize("exc_factory", [
    lambda url: httpx.ConnectError(f"failed to connect to {url}"),
    lambda url: httpx.ReadTimeout(f"timed out reading {url}"),
    lambda url: RuntimeError(f"boom while requesting {url}"),
])
def test_the_access_url_never_survives_a_transport_failure(monkeypatch, exc_factory):
    """The whole point of the boundary: a credential-bearing exception goes in,
    only a closed-set token comes out."""
    from services import simplefin_service
    from services.safe_status import CLOSED_SET

    secret = "https://user:sup3rsecret@bridge.example.com/simplefin"
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", secret)

    def boom(*a, **kw):
        raise exc_factory(secret)

    monkeypatch.setattr(simplefin_service.httpx, "get", boom)

    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()

    err = ei.value
    assert err.status in CLOSED_SET
    blob = f"{err!r} {err} {err.args} {err.status}"
    assert "sup3rsecret" not in blob
    assert "bridge.example.com" not in blob
    # `raise SimpleFinError(...) from None` must suppress the original
    # exception's traceback context — without it, the credential-bearing
    # original exception (and its message) rides along on __context__/__cause__
    # and can surface via traceback formatting, Sentry, etc.
    assert err.__suppress_context__ is True
    assert err.__cause__ is None


def test_http_401_maps_to_auth(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(401, text="nope"))
    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()
    assert ei.value.status == "error: auth"


def test_non_json_body_maps_to_see_logs(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(simplefin_service.SimpleFinError) as ei:
        simplefin_service.fetch_accounts()
    assert ei.value.status == "error: see logs"
    assert ei.value.__suppress_context__ is True
    assert ei.value.__cause__ is None


def test_successful_fetch_returns_the_payload(monkeypatch):
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "SIMPLEFIN_ACCESS_URL", "https://x@y.example/sf")
    monkeypatch.setattr(simplefin_service.httpx, "get",
                        lambda *a, **kw: httpx.Response(200, json=_payload()))
    data = simplefin_service.fetch_accounts(days=90)
    assert len(data["accounts"]) == 2


@pytest.mark.parametrize("logger_name", ["httpx", "httpcore"])
def test_http_client_logging_cannot_publish_the_access_url(logger_name):
    """httpx logs every request URL at INFO — and the SimpleFIN access URL *is*
    the credential, so an INFO-level httpx logger publishes it to the deploy
    logs on every sync. That path bypasses the exception-based redaction
    boundary entirely: nothing raised, nothing stored, credential leaked.

    This happened in production on 2026-07-23, the first time the credential
    was set in Railway. Importing main must leave these loggers above INFO.

    Asserts the level is set *explicitly on that logger*, not inherited: under
    pytest the root logger already has handlers, so main's basicConfig no-ops
    and the inherited level is WARNING regardless. An effective-level assertion
    passes here while production still leaks.
    """
    import logging

    import main  # noqa: F401  — import applies the logging configuration

    assert logging.getLogger(logger_name).level > logging.INFO, (
        f"{logger_name} has no explicit level above INFO; it will log full "
        "request URLs, including the SimpleFIN credential"
    )


def test_normalize_holdings_extracts_and_coerces():
    from services.simplefin_service import normalize_holdings
    payload = {"accounts": [
        {"id": "a1", "name": "ROTH IRA", "org": {"name": "Fidelity"},
         "balance": "9999.99",
         "holdings": [
             {"symbol": "VOO", "description": "Vanguard 500", "shares": "1.5",
              "cost_basis": "500.00", "market_value": "600.25"},
             {"symbol": "JUNK", "market_value": "nan"},          # non-finite -> dropped
             {"symbol": "NOMV", "cost_basis": "10"},              # no market_value -> dropped
             "not-a-dict",
         ]},
        {"id": "a2", "name": "CHECKING", "org": "Wells Fargo", "holdings": []},
        {"id": "a3", "name": "NOHOLD"},
    ]}
    out = normalize_holdings(payload)
    assert [a["simplefin_id"] for a in out] == ["a1"]      # empty/missing holdings omitted
    a1 = out[0]
    assert a1["org"] == "Fidelity"
    assert a1["holdings"] == [{"symbol": "VOO", "description": "Vanguard 500",
                               "shares": 1.5, "cost_basis": 500.0, "market_value": 600.25}]
    assert "balance" not in a1


def test_normalize_holdings_tolerates_garbage_payloads():
    from services.simplefin_service import normalize_holdings
    assert normalize_holdings(None) == []
    assert normalize_holdings({"accounts": None}) == []
    assert normalize_holdings({"accounts": [{"holdings": [{}]}]}) == []  # no account id
    assert normalize_holdings({"accounts": 5}) == []
    assert normalize_holdings({"accounts": [{"id": "a1", "holdings": 5}]}) == []
    assert normalize_holdings({"accounts": [{"id": "a1", "holdings": {"x": 1}}]}) == []
