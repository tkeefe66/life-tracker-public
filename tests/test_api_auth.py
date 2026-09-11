from fastapi.testclient import TestClient


def _client(temp_db_path):
    from app.api import create_app
    # https base_url so the login cookie's Secure flag round-trips in TestClient
    return TestClient(create_app(), base_url="https://testserver")


def test_health_is_public(temp_db_path):
    client = _client(temp_db_path)
    assert client.get("/api/health").status_code == 200


def test_login_wrong_password(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401


def test_login_sets_cookie_and_grants_access(temp_db_path):
    client = _client(temp_db_path)
    import database as db
    db.seed_default_targets()

    assert client.get("/api/targets").status_code == 401  # protected before login
    resp = client.post("/api/login", json={"password": "test-password"})
    assert resp.status_code == 200
    assert "ontrack_session" in resp.cookies
    assert client.get("/api/targets").status_code == 200  # TestClient persists cookies


def test_login_rejects_oversized_password(temp_db_path):
    client = _client(temp_db_path)
    resp = client.post("/api/login", json={"password": "x" * 201})
    assert resp.status_code == 422


def test_docs_endpoints_are_disabled(temp_db_path):
    client = _client(temp_db_path)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_security_headers_present(temp_db_path):
    client = _client(temp_db_path)
    resp = client.get("/api/health")
    assert resp.headers["x-frame-options"] == "DENY"
    # Pin the directives that matter rather than the whole string, since the
    # policy is a multi-directive value that may gain entries over time.
    csp = resp.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "same-origin"
    assert "max-age=31536000" in resp.headers["strict-transport-security"]
    assert "includesubdomains" in resp.headers["strict-transport-security"].lower()


# ── Cache-Control (SPA stale-deploy fix) ──────────────────────────────────────
#
# Regression: index.html was served with an ETag/Last-Modified but no
# Cache-Control, so browsers heuristically cached it and kept requesting the
# previous deploy's content-hashed asset filenames until a hard refresh.

def test_api_responses_are_explicitly_uncacheable(temp_db_path):
    """/api/* states `no-store, private` explicitly rather than leaving the
    header absent. An absent header relied on browsers not disk-caching
    fetch() responses by default — an implicit assumption, not a stated
    policy — and these responses carry bank transactions, health check-ins,
    and calendar contents."""
    client = _client(temp_db_path)
    resp = client.get("/api/health")
    assert resp.headers["cache-control"] == "no-store, private"


def test_cache_control_for_path_pure_function():
    """Unit-test the decision function directly — the dist mount isn't built
    in the test environment (no frontend/dist), so this is the reliable way
    to pin the policy without depending on a built frontend."""
    from app.api import cache_control_for_path

    assert cache_control_for_path("/api/targets") == "no-store, private"
    assert cache_control_for_path("/api/health") == "no-store, private"
    assert cache_control_for_path("/assets/index-abc123.js") == "public, max-age=31536000, immutable"
    assert cache_control_for_path("/assets/index-abc123.css") == "public, max-age=31536000, immutable"
    assert cache_control_for_path("/") == "no-cache"
    assert cache_control_for_path("/index.html") == "no-cache"
    assert cache_control_for_path("/some/client/route") == "no-cache"  # SPA fallback


def test_spa_shell_and_assets_get_correct_cache_control_through_the_full_stack(tmp_path, temp_db_path, monkeypatch):
    """End-to-end through the real StaticFiles mount: build a throwaway dist/
    directory, point FRONTEND_DIST at it, and confirm the middleware header
    survives StaticFiles' own response (which only sets ETag/Last-Modified)."""
    from fastapi.testclient import TestClient

    from app import api as api_mod

    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html>shell</html>")
    (assets / "index-abc123.js").write_text("console.log('hi')")

    monkeypatch.setattr(api_mod, "FRONTEND_DIST", dist)
    client = TestClient(api_mod.create_app(), base_url="https://testserver")

    root_resp = client.get("/")
    assert root_resp.status_code == 200
    assert root_resp.headers["cache-control"] == "no-cache"

    asset_resp = client.get("/assets/index-abc123.js")
    assert asset_resp.status_code == 200
    assert asset_resp.headers["cache-control"] == "public, max-age=31536000, immutable"


# ── Sessions: expiry, revocation, logout ──────────────────────────────────────

def test_session_cookie_is_not_derivable_from_password(temp_db_path):
    """The old scheme was hmac(APP_PASSWORD, fixed-string) — the same password
    always produced the same cookie, and anyone who learned APP_PASSWORD could
    compute a valid session offline without ever calling /api/login. Two logins
    with the identical correct password must now yield two different, random
    tokens."""
    import hashlib
    import hmac as hmac_mod

    client_a = _client(temp_db_path)
    client_b = _client(temp_db_path)
    resp_a = client_a.post("/api/login", json={"password": "test-password"})
    resp_b = client_b.post("/api/login", json={"password": "test-password"})

    token_a = resp_a.cookies["ontrack_session"]
    token_b = resp_b.cookies["ontrack_session"]
    assert token_a != token_b  # random per-login, not a deterministic function of the password

    old_style_token = hmac_mod.new(b"test-password", b"on-track-session-v1", hashlib.sha256).hexdigest()
    assert token_a != old_style_token
    assert token_b != old_style_token


def test_unknown_session_token_401s(temp_db_path):
    client = _client(temp_db_path)
    client.cookies.set("ontrack_session", "not-a-real-token")
    assert client.get("/api/targets").status_code == 401


def test_expired_session_401s_and_is_swept(temp_db_path):
    import datetime

    import database as db
    client = _client(temp_db_path)

    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    created = past - datetime.timedelta(days=14)
    db.create_session("expired-token", created.isoformat(timespec="microseconds"), past.isoformat(timespec="microseconds"))
    client.cookies.set("ontrack_session", "expired-token")

    assert client.get("/api/targets").status_code == 401
    # require_auth deletes an expired session the moment it's presented.
    assert db.get_session("expired-token") is None


def test_logout_invalidates_only_that_session(temp_db_path):
    """Seed two independent sessions (two devices/browsers) and confirm logging
    one out never touches the other."""
    client_a = _client(temp_db_path)
    client_b = _client(temp_db_path)
    client_a.post("/api/login", json={"password": "test-password"})
    client_b.post("/api/login", json={"password": "test-password"})

    assert client_a.post("/api/logout").status_code == 200
    assert client_a.get("/api/targets").status_code == 401
    assert client_b.get("/api/targets").status_code == 200  # untouched


def test_sliding_renewal_extends_a_session_past_its_halfway_point(temp_db_path, monkeypatch):
    """A session more than halfway to expiry gets its expires_at pushed out on the
    next authenticated request — bounds a stolen cookie's life while keeping
    normal, regular use from ever hitting the original expiry."""

    import database as db
    from app import auth as auth_mod

    client = _client(temp_db_path)
    client.post("/api/login", json={"password": "test-password"})
    token = client.cookies["ontrack_session"]
    original_expiry = auth_mod._parse(db.get_session(token)["expires_at"])

    # Jump the clock to just past the session's halfway point.
    created_at = auth_mod._parse(db.get_session(token)["created_at"])
    past_halfway = created_at + (original_expiry - created_at) * 0.6
    monkeypatch.setattr(auth_mod, "_utcnow", lambda: past_halfway)

    assert client.get("/api/targets").status_code == 200
    renewed_expiry = auth_mod._parse(db.get_session(token)["expires_at"])
    assert renewed_expiry > original_expiry


def test_session_not_yet_halfway_is_not_renewed(temp_db_path, monkeypatch):
    import datetime

    import database as db
    from app import auth as auth_mod

    client = _client(temp_db_path)
    client.post("/api/login", json={"password": "test-password"})
    token = client.cookies["ontrack_session"]
    original_expiry = db.get_session(token)["expires_at"]

    created_at = auth_mod._parse(db.get_session(token)["created_at"])
    just_after_login = created_at + datetime.timedelta(seconds=1)
    monkeypatch.setattr(auth_mod, "_utcnow", lambda: just_after_login)

    assert client.get("/api/targets").status_code == 200
    assert db.get_session(token)["expires_at"] == original_expiry


def test_logout_clears_the_cookie(temp_db_path):
    client = _client(temp_db_path)
    client.post("/api/login", json={"password": "test-password"})
    resp = client.post("/api/logout")
    assert resp.status_code == 200
    # httpx/starlette expresses "clear this cookie" as an immediately-expired Set-Cookie.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "ontrack_session=" in set_cookie


# ── Login throttling ──────────────────────────────────────────────────────────

def test_lockout_triggers_after_five_failures_even_with_correct_password(temp_db_path):
    client = _client(temp_db_path)
    for _ in range(5):
        resp = client.post("/api/login", json={"password": "wrong"})
        assert resp.status_code == 401
    # The 6th attempt is rejected outright — even with the CORRECT password —
    # because the lockout is checked before verify_password runs at all.
    resp = client.post("/api/login", json={"password": "test-password"})
    assert resp.status_code == 429


def test_successful_login_before_threshold_clears_the_counter(temp_db_path):
    client = _client(temp_db_path)
    for _ in range(4):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200
    # Counter reset — a fresh run of 4 more wrong attempts must not trip the lockout.
    for _ in range(4):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200


def test_every_failed_attempt_is_logged(temp_db_path, caplog):
    import logging
    client = _client(temp_db_path)
    with caplog.at_level(logging.WARNING):
        client.post("/api/login", json={"password": "wrong"})
    assert any("Failed login attempt" in r.message for r in caplog.records)


def test_lockout_expires_and_access_is_restored(temp_db_path, monkeypatch):
    """Clock-controlled — no real sleeping. The lockout window (60s the first
    time) must pass and then a correct password must succeed again."""
    import datetime

    from app import api as api_mod

    client = _client(temp_db_path)
    for _ in range(5):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 429

    future = api_mod._utcnow() + datetime.timedelta(seconds=61)
    monkeypatch.setattr(api_mod, "_utcnow", lambda: future)
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200


def test_lockout_window_doubles_on_repeated_failure_after_expiry(temp_db_path, monkeypatch):
    """First lockout is 60s; if the attacker keeps failing after it expires, the
    next lockout window must be longer (doubling), not reset back to 60s."""
    import datetime

    from app import api as api_mod
    import database as db

    client = _client(temp_db_path)
    for _ in range(5):
        client.post("/api/login", json={"password": "wrong"})
    first_locked_until = api_mod._parse(db.get_setting(api_mod.LOGIN_LOCKED_UNTIL_KEY))

    # Jump past the first lockout window and fail once more.
    just_after_first = first_locked_until + datetime.timedelta(seconds=1)
    monkeypatch.setattr(api_mod, "_utcnow", lambda: just_after_first)
    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 401  # lockout had expired, so this attempt is processed normally

    second_locked_until = api_mod._parse(db.get_setting(api_mod.LOGIN_LOCKED_UNTIL_KEY))
    second_window = second_locked_until - just_after_first
    assert second_window > datetime.timedelta(seconds=60)  # longer than the base window — it doubled, not reset


# ── H1: atomic lockout counter ────────────────────────────────────────────────

def test_concurrent_failed_logins_increment_the_counter_atomically(temp_db_path, monkeypatch):
    """Regression for the non-atomic read-modify-write in _record_login_failure:
    `count = int(get_setting(...)) + 1; set_setting(...)` run from N concurrent
    threads (Starlette dispatches sync `def` routes onto the anyio threadpool)
    all read the same stale value and all write the same next value — the
    counter advances once per burst instead of once per request. Threshold is
    raised so this burst never trips the lockout itself; the assertion isolates
    the atomicity property from the separate lockout-cap behavior."""
    import concurrent.futures

    from app import api as api_mod
    import database as db

    monkeypatch.setattr(api_mod, "LOCKOUT_THRESHOLD", 10_000)

    client = _client(temp_db_path)
    n = 40

    def attempt(_):
        return client.post("/api/login", json={"password": "wrong"})

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        responses = list(pool.map(attempt, range(n)))

    assert all(r.status_code == 401 for r in responses)
    assert int(db.get_setting(api_mod.LOGIN_FAIL_COUNT_KEY)) == n


# ── M1: absolute session lifetime cap ─────────────────────────────────────────

def test_session_older_than_max_days_is_rejected_even_if_not_yet_expired(temp_db_path):
    """Sliding renewal only ever advances expires_at, never created_at, so an
    actively-used (or stolen-and-replayed) cookie could otherwise renew forever.
    A session created long ago must be rejected once it exceeds SESSION_MAX_DAYS,
    regardless of how far renewal has pushed expires_at into the future."""
    import datetime

    import database as db
    from config import SESSION_MAX_DAYS

    client = _client(temp_db_path)
    created = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=SESSION_MAX_DAYS + 1)
    far_future_expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
    db.create_session(
        "ancient-but-renewed-token",
        created.isoformat(timespec="microseconds"),
        far_future_expiry.isoformat(timespec="microseconds"),
    )
    client.cookies.set("ontrack_session", "ancient-but-renewed-token")

    assert client.get("/api/targets").status_code == 401
    assert db.get_session("ancient-but-renewed-token") is None  # swept on the attempt


def test_session_within_max_days_is_accepted(temp_db_path):
    """Sanity check the cap doesn't false-positive on an ordinary, recent session."""
    client = _client(temp_db_path)
    resp = client.post("/api/login", json={"password": "test-password"})
    assert resp.status_code == 200
    import database as db
    db.seed_default_targets()
    assert client.get("/api/targets").status_code == 200


# ── M2: lockout can't permanently lock the owner out ──────────────────────────

def test_valid_session_cookie_bypasses_the_lockout(temp_db_path):
    """An unauthenticated attacker must not be able to lock the owner out of an
    already-authenticated device: a request to /api/login that itself carries a
    still-valid session cookie skips the lockout check entirely."""
    owner_client = _client(temp_db_path)
    assert owner_client.post("/api/login", json={"password": "test-password"}).status_code == 200

    attacker_client = _client(temp_db_path)
    for _ in range(5):
        attacker_client.post("/api/login", json={"password": "wrong"})
    # Confirm the lockout is really in effect for anyone without the cookie.
    assert attacker_client.post("/api/login", json={"password": "test-password"}).status_code == 429

    # The owner's own already-authenticated browser is unaffected.
    resp = owner_client.post("/api/login", json={"password": "test-password"})
    assert resp.status_code == 200


def test_wrong_password_from_a_valid_session_still_counts_toward_lockout(temp_db_path):
    """M2 fix: a stolen session cookie must not grant unlimited free password
    guesses. Only a *correct* password bypasses the lockout for an
    already-authenticated device (M2a, see test_valid_session_cookie_bypasses_
    the_lockout above) — a wrong one from that same device is still recorded
    and, once the threshold is crossed, is refused with 429 exactly like a
    session-less attacker's guess."""
    client = _client(temp_db_path)
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200

    for _ in range(5):
        resp = client.post("/api/login", json={"password": "wrong"})
        # Matches the session-less lockout's own off-by-one (see
        # test_lockout_triggers_after_five_failures_even_with_correct_password):
        # the attempt that tips the counter over the threshold still 401s —
        # only the NEXT one, made while already locked out, gets 429.
        assert resp.status_code == 401

    # Proves the failures were actually recorded, not silently exempted just
    # because the request carried a valid session cookie — this is the
    # vulnerability being fixed.
    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 429

    # The owner's correct password from this same device still succeeds
    # immediately despite the active lockout — M2a preserved.
    resp = client.post("/api/login", json={"password": "test-password"})
    assert resp.status_code == 200


def test_abandoned_attack_heals_after_30_minutes_of_inactivity(temp_db_path, monkeypatch):
    """The failure counter only ever reset on a successful login — impossible
    while locked — so one wrong guess a minute could keep the owner locked out
    forever. If the last failure was more than 30 minutes ago, the next failed
    attempt must be treated as a fresh #1, not as a continuation of the old
    count (which would otherwise instantly re-trigger a fresh lock)."""
    import datetime

    from app import api as api_mod
    import database as db

    client = _client(temp_db_path)

    # Simulate an ongoing attack: each failure's lockout window is allowed to
    # naturally expire before the next attempt, so the count keeps climbing —
    # exactly the "one guess a minute, forever" pattern from the finding.
    now = api_mod._utcnow()
    for _ in range(7):
        monkeypatch.setattr(api_mod, "_utcnow", lambda n=now: n)
        client.post("/api/login", json={"password": "wrong"})
        raw_locked_until = db.get_setting(api_mod.LOGIN_LOCKED_UNTIL_KEY)
        if raw_locked_until:
            now = api_mod._parse(raw_locked_until) + datetime.timedelta(seconds=1)
        else:
            now = now + datetime.timedelta(seconds=1)  # below threshold — no lock set yet

    # The attacker gives up. No attempts land for 31 minutes.
    stale = now + datetime.timedelta(minutes=31)
    monkeypatch.setattr(api_mod, "_utcnow", lambda: stale)

    # One stray wrong attempt after the abandoned window must count as failure
    # #1, not #8 — which would otherwise instantly re-lock for another 15 min.
    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 401
    assert int(db.get_setting(api_mod.LOGIN_FAIL_COUNT_KEY)) == 1

    # The owner's very next correct-password attempt succeeds immediately.
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200


def test_oversized_login_body_is_rejected_with_413(temp_db_path):
    client = _client(temp_db_path)
    resp = client.post("/api/login", json={"password": "x" * 200_000})
    assert resp.status_code == 413


def test_oversized_body_still_carries_security_headers(temp_db_path):
    client = _client(temp_db_path)
    resp = client.post("/api/login", json={"password": "x" * 200_000})
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_normal_login_body_is_unaffected_by_the_cap(temp_db_path):
    client = _client(temp_db_path)
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200


def test_chunked_oversized_body_is_rejected_with_413(temp_db_path):
    """httpx sends a generator body as Transfer-Encoding: chunked with no
    Content-Length, so this is the only test that exercises the streaming
    counter through the real FastAPI stack rather than the fast path."""
    client = _client(temp_db_path)

    def body():
        yield b'{"password":"'
        for _ in range(25):
            yield b"x" * 8192
        yield b'"}'

    resp = client.post("/api/login", content=body(),
                       headers={"content-type": "application/json"})
    assert resp.status_code == 413


# ── Task 6: Telegram alert on lockout crossing ─────────────────────────────────

def test_alert_fires_when_the_lockout_threshold_is_crossed(temp_db_path, monkeypatch):
    from app import api

    sent = []
    monkeypatch.setattr(api, "notify_background", lambda text: sent.append(text))
    client = _client(temp_db_path)

    for _ in range(api.LOCKOUT_THRESHOLD):
        client.post("/api/login", json={"password": "nope"})

    assert len(sent) == 1
    assert "lock" in sent[0].lower()


def test_alert_does_not_repeat_on_every_failure_past_the_threshold(temp_db_path, monkeypatch):
    """Must drive the failures from a client holding a valid session cookie.

    A session-less client gets short-circuited by the login route's lockout
    pre-check (`if already_locked and not has_valid_session(request): raise
    429`) the moment it's already locked out — that raises BEFORE
    verify_password and before _record_login_failure run again, so `count`
    never advances past the threshold and the `count == LOCKOUT_THRESHOLD`
    guard is never re-entered. A session-holding device is the one exemption
    to that pre-check (M2a — see test_wrong_password_from_a_valid_session_
    still_counts_toward_lockout): its wrong passwords keep being recorded
    and `count` keeps climbing past the threshold on every attempt, which is
    exactly the path where "only on the crossing" is load-bearing. Without a
    session here, this test cannot distinguish `count == LOCKOUT_THRESHOLD`
    from `count >= LOCKOUT_THRESHOLD`."""
    from app import api

    sent = []
    monkeypatch.setattr(api, "notify_background", lambda text: sent.append(text))
    client = _client(temp_db_path)
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200

    for _ in range(api.LOCKOUT_THRESHOLD + 3):
        client.post("/api/login", json={"password": "nope"})

    assert len(sent) == 1  # the crossing only, not one per guess


def test_no_alert_below_the_threshold(temp_db_path, monkeypatch):
    from app import api

    sent = []
    monkeypatch.setattr(api, "notify_background", lambda text: sent.append(text))
    client = _client(temp_db_path)

    for _ in range(api.LOCKOUT_THRESHOLD - 1):
        client.post("/api/login", json={"password": "nope"})

    assert sent == []


def test_alert_never_contains_the_attempted_password(temp_db_path, monkeypatch):
    from app import api

    sent = []
    monkeypatch.setattr(api, "notify_background", lambda text: sent.append(text))
    client = _client(temp_db_path)

    for _ in range(api.LOCKOUT_THRESHOLD):
        client.post("/api/login", json={"password": "sentinel-guess-value"})

    assert sent
    assert "sentinel-guess-value" not in " ".join(sent)
