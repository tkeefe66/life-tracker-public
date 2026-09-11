"""jobs/backup_db: daily PostgreSQL backup to an off-Railway destination.

Never exercises a real S3-compatible client or a real pg_dump — the upload and
dump steps are isolated behind small functions the tests monkeypatch, per the
plan's testing note ("do not test the actual upload")."""
import pytest


@pytest.fixture(autouse=True)
def no_ambient_encryption_key(monkeypatch):
    """Neutralize whatever BACKUP_ENCRYPTION_KEY the developer's .env happens
    to hold, so these tests describe the code and not the local machine.

    Without this the suite reads the ambient key: on 2026-08-06 a malformed
    value in .env made three unrelated tests fail inside Fernet, on main, for
    three days, with nothing pointing at the real cause -- every test whose
    path reaches _encrypt_file. The encryption tests below set the key
    explicitly and are unaffected by this default."""
    from jobs import backup_db
    monkeypatch.setattr(backup_db, "BACKUP_ENCRYPTION_KEY", "")


def _raise_dump_error(path):
    from jobs.backup_db import BackupDumpError
    raise BackupDumpError("pg_dump exited with status 1")


def _write_plausible_dump(path):
    """Writes a file comfortably over MIN_DUMP_BYTES so
    _assert_dump_is_plausible passes."""
    with open(path, "wb") as f:
        f.write(b"x" * 4096)


def test_backup_skipped_when_sqlite(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    called = []
    monkeypatch.setattr(backup_db, "_upload", lambda *a, **k: called.append(a))
    assert db.USE_POSTGRES is False  # temp_db_path fixture forces SQLite

    backup_db.run()

    assert called == []
    assert db.get_setting("backup_last_status") is None  # never even attempted


def test_backup_skipped_when_unconfigured(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "")
    called = []
    monkeypatch.setattr(backup_db, "_upload", lambda *a, **k: called.append(a))

    backup_db.run()  # must not raise

    assert called == []
    assert db.get_setting("backup_last_status") == "error: not configured"


def test_backup_uploads_with_expected_key_and_prunes(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    plausible_dump = b"x" * (backup_db.MIN_DUMP_BYTES + 1)
    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: open(path, "wb").write(plausible_dump))
    upload_calls = []
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: upload_calls.append(key))
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    prune_calls = []
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: prune_calls.append(True))

    backup_db.run()

    assert len(upload_calls) == 1
    assert upload_calls[0].startswith("on-track-backups/")
    assert upload_calls[0].endswith(".dump")
    assert prune_calls == [True]
    assert db.get_setting("backup_last_status") == "ok"
    assert db.get_setting("backup_last_run") is not None


def test_backup_refuses_to_upload_an_implausibly_small_dump(temp_db_path, monkeypatch):
    """A pg_dump that exits 0 but produces a truncated/empty file must not be
    uploaded — and, critically, must not cause _prune_old_backups to run and
    delete the last-known-good backups on the strength of a bad one."""
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: open(path, "wb").write(b"truncated"))
    upload_calls = []
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: upload_calls.append(key))
    prune_calls = []
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: prune_calls.append(True))

    backup_db.run()  # must not raise

    assert upload_calls == []
    assert prune_calls == []
    assert db.get_setting("backup_last_status") == "error: see logs"


def test_backup_does_not_prune_unless_upload_is_confirmed_in_listing(temp_db_path, monkeypatch):
    """If the just-uploaded key doesn't show up in a post-upload listing, treat
    the upload as unverified and skip pruning — never delete good backups on
    the strength of an upload we can't confirm actually landed."""
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    plausible_dump = b"x" * (backup_db.MIN_DUMP_BYTES + 1)
    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: open(path, "wb").write(plausible_dump))
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: None)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: False)
    prune_calls = []
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: prune_calls.append(True))

    backup_db.run()  # must not raise

    assert prune_calls == []
    assert db.get_setting("backup_last_status") == "error: see logs"


def test_backup_records_safe_status_on_failure(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    def boom(path):
        # Mimics a real pg_dump failure whose message could carry a connection
        # string with a password — the job must never store this raw.
        raise RuntimeError("connection to server at postgres://user:hunter2@host failed")
    monkeypatch.setattr(backup_db, "_dump_to_file", boom)

    backup_db.run()  # must not raise

    status = db.get_setting("backup_last_status")
    assert status == "error: see logs"
    assert "hunter2" not in status
    assert "postgres://" not in status


# ── H2: pg_dump must never see the credential-bearing URL in argv ────────────

def test_pg_dump_invocation_never_puts_the_password_in_argv(tmp_path, monkeypatch):
    """DATABASE_URL embeds the password. Passing it to pg_dump as a positional
    argument puts the password in /proc/<pid>/cmdline and `ps` for any other
    process on the box to read. pg_dump must be invoked with -h/-p/-U/-d flags
    instead, and the password supplied only via the subprocess env (PGPASSWORD)
    — never as an argv element, and never via check=True (whose CalledProcessError
    embeds the full argv it was given)."""
    from jobs import backup_db

    for var in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSSLMODE"):
        monkeypatch.setattr(backup_db, var, "")
    monkeypatch.setattr(backup_db, "DATABASE_URL", "postgresql://dbuser:hunter2@dbhost.example.com:5432/mydb")

    captured = {}

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess()

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    dump_path = str(tmp_path / "out.dump")
    backup_db._dump_to_file(dump_path)

    args = captured["args"]
    assert "hunter2" not in args
    assert not any("hunter2" in str(a) for a in args)
    assert "postgresql://" not in " ".join(str(a) for a in args)
    assert "check" not in captured["kwargs"]  # never check=True — see docstring above

    # The password must reach pg_dump only through the environment.
    env = captured["kwargs"].get("env")
    assert env is not None
    assert env.get("PGPASSWORD") == "hunter2"

    # And the connection details pg_dump needs are passed as explicit flags.
    assert "-h" in args and args[args.index("-h") + 1] == "dbhost.example.com"
    assert "-p" in args and args[args.index("-p") + 1] == "5432"
    assert "-U" in args and args[args.index("-U") + 1] == "dbuser"
    assert "-d" in args and args[args.index("-d") + 1] == "mydb"


def test_pg_dump_failure_without_check_true_is_raised_from_our_own_exception(tmp_path, monkeypatch):
    """With check=True removed, a non-zero exit must still surface as a raised
    exception (so run()'s try/except and safe_status() still catch it) — but
    one we construct ourselves, never one built from a library that embeds argv."""
    from jobs import backup_db

    for var in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSSLMODE"):
        monkeypatch.setattr(backup_db, var, "")
    monkeypatch.setattr(backup_db, "DATABASE_URL", "postgresql://dbuser:hunter2@dbhost.example.com:5432/mydb")

    class _FakeCompletedProcess:
        returncode = 1
        stderr = b"pg_dump: error: some failure"

    monkeypatch.setattr(backup_db.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    dump_path = str(tmp_path / "out.dump")
    with pytest.raises(Exception) as exc_info:
        backup_db._dump_to_file(dump_path)
    assert "hunter2" not in str(exc_info.value)


def test_pg_dump_invoked_with_no_password_prompt_flag(tmp_path, monkeypatch):
    """-w tells pg_dump to fail fast on a missing/invalid password instead of
    attempting an interactive prompt, which would just hang a scheduled job."""
    from jobs import backup_db

    for var in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSSLMODE"):
        monkeypatch.setattr(backup_db, var, "")
    monkeypatch.setattr(backup_db, "DATABASE_URL", "postgresql://dbuser:hunter2@dbhost.example.com:5432/mydb")

    captured = {}

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _FakeCompletedProcess()

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    dump_path = str(tmp_path / "out.dump")
    backup_db._dump_to_file(dump_path)

    assert "-w" in captured["args"]


# ── Fix 1 (M): urlparse().password is never percent-decoded ──────────────────

def test_discrete_pg_vars_are_preferred_and_used_verbatim(tmp_path, monkeypatch):
    """When Railway's discrete PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE are
    all set, use them directly — no DATABASE_URL parsing, no percent-decoding
    needed since Railway already provides them decoded."""
    from jobs import backup_db

    # A DATABASE_URL that, if parsed instead, would produce different (wrong)
    # values — proves the discrete vars actually take priority.
    monkeypatch.setattr(backup_db, "DATABASE_URL", "postgresql://urluser:urlpass@urlhost:1111/urldb")
    monkeypatch.setattr(backup_db, "PGHOST", "pghost.example.com")
    monkeypatch.setattr(backup_db, "PGPORT", "5432")
    monkeypatch.setattr(backup_db, "PGUSER", "pguser")
    monkeypatch.setattr(backup_db, "PGPASSWORD", "p@ss/word%20")
    monkeypatch.setattr(backup_db, "PGDATABASE", "pgdb")
    monkeypatch.setattr(backup_db, "PGSSLMODE", "")

    captured = {}

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess()

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    dump_path = str(tmp_path / "out.dump")
    backup_db._dump_to_file(dump_path)

    args = captured["args"]
    assert "-h" in args and args[args.index("-h") + 1] == "pghost.example.com"
    assert "-p" in args and args[args.index("-p") + 1] == "5432"
    assert "-U" in args and args[args.index("-U") + 1] == "pguser"
    assert "-d" in args and args[args.index("-d") + 1] == "pgdb"
    assert "urluser" not in " ".join(args)
    assert "urlhost" not in " ".join(args)

    env = captured["kwargs"]["env"]
    # The password reaches pg_dump byte-for-byte via PGPASSWORD — never
    # re-encoded, never truncated, never assembled into a connection string.
    assert env["PGPASSWORD"] == "p@ss/word%20"


def test_fallback_to_database_url_percent_decodes_username_password_and_dbname(tmp_path, monkeypatch):
    """When the discrete PG* vars aren't all present, fall back to parsing
    DATABASE_URL — but unlike urlparse's raw .username/.password/.path,
    the resolved values must be percent-decoded. A password containing '@',
    '%', and '/' (encoded as %40, %25, %2F) must reach pg_dump exactly as
    typed, not as the literal percent-encoded text urlparse leaves behind."""
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "PGHOST", "")
    monkeypatch.setattr(backup_db, "PGPORT", "")
    monkeypatch.setattr(backup_db, "PGUSER", "")
    monkeypatch.setattr(backup_db, "PGPASSWORD", "")
    monkeypatch.setattr(backup_db, "PGDATABASE", "")
    monkeypatch.setattr(backup_db, "PGSSLMODE", "")
    # Real secret: p@ss/word%20  →  percent-encoded in the URL as p%40ss%2Fword%2520
    monkeypatch.setattr(
        backup_db, "DATABASE_URL",
        "postgresql://db%2Buser:p%40ss%2Fword%2520@dbhost.example.com:5432/my%2Ddb",
    )

    captured = {}

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess()

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    dump_path = str(tmp_path / "out.dump")
    backup_db._dump_to_file(dump_path)

    args = captured["args"]
    assert "-U" in args and args[args.index("-U") + 1] == "db+user"
    assert "-d" in args and args[args.index("-d") + 1] == "my-db"

    env = captured["kwargs"]["env"]
    assert env["PGPASSWORD"] == "p@ss/word%20"
    # The raw percent-encoded text must never survive into what pg_dump sees.
    assert "%40" not in env["PGPASSWORD"]
    assert "%2F" not in env["PGPASSWORD"] and "%2f" not in env["PGPASSWORD"]


# ── Fix 5 (L): sslmode from DATABASE_URL's query string must survive ─────────

def test_fallback_preserves_sslmode_from_database_url_query_string(tmp_path, monkeypatch):
    """The rebuilt -h/-p/-U/-d args drop any ?sslmode=... query param from
    DATABASE_URL, silently downgrading sslmode=require to libpq's default
    'prefer'. It must be preserved via the PGSSLMODE env var instead."""
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "PGHOST", "")
    monkeypatch.setattr(backup_db, "PGPORT", "")
    monkeypatch.setattr(backup_db, "PGUSER", "")
    monkeypatch.setattr(backup_db, "PGPASSWORD", "")
    monkeypatch.setattr(backup_db, "PGDATABASE", "")
    monkeypatch.setattr(backup_db, "PGSSLMODE", "")
    monkeypatch.setattr(
        backup_db, "DATABASE_URL",
        "postgresql://dbuser:hunter2@dbhost.example.com:5432/mydb?sslmode=require&connect_timeout=10",
    )

    captured = {}

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def fake_run(args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess()

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    dump_path = str(tmp_path / "out.dump")
    backup_db._dump_to_file(dump_path)

    assert captured["kwargs"]["env"]["PGSSLMODE"] == "require"


# ── Fix 3 (M): unversioned "postgresql" nix package + pg_dump version-mismatch
# detection, since the job (not the deploy pin) is now responsible for
# surfacing a diagnosable status when the server is newer than pg_dump. ──────

def test_version_mismatch_stderr_is_detected():
    from jobs import backup_db

    mismatch_stderr = (
        "pg_dump: error: aborting because of server version mismatch\n"
        "pg_dump: detail: server version: 17.2; pg_dump version: 15.4\n"
    )
    assert backup_db._looks_like_pg_dump_version_mismatch(mismatch_stderr) is True


def test_ordinary_failure_stderr_is_not_flagged_as_version_mismatch():
    from jobs import backup_db

    assert backup_db._looks_like_pg_dump_version_mismatch("pg_dump: error: connection refused") is False
    assert backup_db._looks_like_pg_dump_version_mismatch("") is False


def test_run_records_distinct_status_on_pg_dump_version_mismatch(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db
    from services.safe_status import PG_DUMP_VERSION_MISMATCH

    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ENDPOINT", "https://s3.example.com")
    monkeypatch.setattr(backup_db, "BACKUP_S3_ACCESS_KEY", "key")
    monkeypatch.setattr(backup_db, "BACKUP_S3_SECRET_KEY", "secret")

    class _FakeCompletedProcess:
        returncode = 1
        stderr = (
            b"pg_dump: error: aborting because of server version mismatch\n"
            b"pg_dump: detail: server version: 17.2; pg_dump version: 15.4\n"
        )

    monkeypatch.setattr(backup_db.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    backup_db.run()  # must not raise

    assert db.get_setting("backup_last_status") == PG_DUMP_VERSION_MISMATCH


def test_retention_keeps_a_month_of_daily_dumps(monkeypatch):
    """The job runs daily (main.py's CronTrigger has no day_of_week), so
    RETENTION counts days, not weeks. It was 8 while the schedule was weekly —
    switching to daily without raising it would have silently cut history from
    eight weeks to eight days, leaving nothing clean for a problem noticed a
    fortnight late. A dump is ~390 KB, so a month costs ~12 MB."""
    from jobs import backup_db

    assert backup_db.RETENTION >= 30


def test_prune_deletes_only_the_oldest_beyond_retention(monkeypatch):
    """Pins the retention arithmetic itself: the newest RETENTION dumps
    survive, everything older is deleted, and pruning is keyed on the sortable
    timestamp in the object name."""
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")

    keys = [f"{backup_db.BACKUP_PREFIX}2026{m:02d}{d:02d}T040000.dump"
            for m in (1, 2) for d in range(1, 21)]  # 40 dumps, oldest first
    deleted = []

    class _FakeClient:
        def list_objects_v2(self, **kw):
            return {"Contents": [{"Key": k} for k in keys]}

        def delete_object(self, Bucket, Key):
            deleted.append(Key)

    monkeypatch.setattr(backup_db, "_s3_client", lambda: _FakeClient())
    backup_db._prune_old_backups()

    assert len(deleted) == len(keys) - backup_db.RETENTION
    survivors = [k for k in keys if k not in deleted]
    assert survivors == sorted(keys)[-backup_db.RETENTION:]
    assert all(d < min(survivors) for d in deleted)


def test_prune_is_a_noop_when_under_retention(monkeypatch):
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "BACKUP_S3_BUCKET", "test-bucket")
    keys = [f"{backup_db.BACKUP_PREFIX}20260{i}01T040000.dump" for i in range(1, 4)]
    deleted = []

    class _FakeClient:
        def list_objects_v2(self, **kw):
            return {"Contents": [{"Key": k} for k in keys]}

        def delete_object(self, Bucket, Key):
            deleted.append(Key)

    monkeypatch.setattr(backup_db, "_s3_client", lambda: _FakeClient())
    backup_db._prune_old_backups()

    assert deleted == []


# ── Task 7: alert on backup status transitions ────────────────────────────

def test_alert_fires_when_backup_status_goes_from_ok_to_error(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_dump_error)

    backup_db.run()

    assert len(sent) == 1
    assert "backup" in sent[0].lower()


def test_alert_does_not_repeat_while_the_failure_persists(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_dump_error)

    backup_db.run()
    backup_db.run()
    backup_db.run()

    assert len(sent) == 1  # one alert for the transition, not one per run


def test_alert_fires_on_recovery_back_to_ok(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "error: see logs")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: _write_plausible_dump(path))
    monkeypatch.setattr(backup_db, "_upload", lambda *a, **k: None)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: None)

    backup_db.run()

    assert len(sent) == 1
    assert "recover" in sent[0].lower() or "ok" in sent[0].lower()


def test_alert_never_contains_exception_text(temp_db_path, monkeypatch):
    """The redaction boundary applies to notifications too -- a pg_dump or S3
    error can embed DATABASE_URL or the S3 credentials in its message."""
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)

    def _raise_with_secret(path):
        raise RuntimeError("postgres://user:SENTINELSECRET@host:5432/railway")

    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_with_secret)

    backup_db.run()

    assert sent
    assert "SENTINELSECRET" not in " ".join(sent)


# ── Task 7 fix round 1: first-observation matrix (previous is None) ───────
#
# backup_last_status is unset on a fresh deploy, so db.get_setting returns
# None. A plain `previous == status` inequality can't tell "first observation
# ever" apart from "the status genuinely changed" -- both a first success
# and a never-opted-into-backups deploy would otherwise read as a
# transition and send a false alert.

def test_first_successful_run_sends_no_alert(temp_db_path, monkeypatch):
    """Fresh deploy, backup_last_status never set. First run succeeds. This
    is a first success, not a recovery from a failure that never happened --
    nothing should be announced."""
    import database as db
    from jobs import backup_db

    assert db.get_setting("backup_last_status") is None
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: _write_plausible_dump(path))
    monkeypatch.setattr(backup_db, "_upload", lambda *a, **k: None)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: None)

    backup_db.run()

    assert sent == []
    assert db.get_setting("backup_last_status") == "ok"


def test_first_run_with_backups_unconfigured_sends_no_alert(temp_db_path, monkeypatch):
    """Fresh deploy, BACKUP_S3_* never set. Never opting into backups is a
    documented clean no-op (CLAUDE.md), not a fault -- must not alert as if
    it were a failure."""
    import database as db
    from jobs import backup_db

    assert db.get_setting("backup_last_status") is None
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: False)

    backup_db.run()

    assert sent == []
    assert db.get_setting("backup_last_status") == "error: not configured"


def test_first_run_that_fails_still_alerts(temp_db_path, monkeypatch):
    """Fresh deploy, backup_last_status never set, and the very first run
    genuinely fails. Unlike the first-success and never-configured cases,
    this IS something the user needs to hear about."""
    import database as db
    from jobs import backup_db

    assert db.get_setting("backup_last_status") is None
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_dump_error)

    backup_db.run()

    assert len(sent) == 1
    assert "backup" in sent[0].lower()


def test_alert_fires_when_backups_go_dark_from_ok(temp_db_path, monkeypatch):
    """Backups were working (status "ok") and then the deploy loses its
    BACKUP_S3_* configuration. This is the silent-failure case the whole
    task exists to catch, and it must still alert even though the new
    status is NOT_CONFIGURED rather than a hard error."""
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: False)

    backup_db.run()

    assert len(sent) == 1
    assert db.get_setting("backup_last_status") == "error: not configured"


# ── Task 8: encrypt the dump before upload ────────────────────────────────

def test_dump_is_encrypted_before_upload_when_a_key_is_set(temp_db_path, monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from jobs import backup_db

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(backup_db, "BACKUP_ENCRYPTION_KEY", key)

    plaintext = tmp_path / "plain.dump"
    plaintext.write_bytes(b"PGDMP-sentinel-payload")
    ciphertext = tmp_path / "plain.dump.enc"

    backup_db._encrypt_file(str(plaintext), str(ciphertext))

    raw = ciphertext.read_bytes()
    assert b"PGDMP-sentinel-payload" not in raw          # actually encrypted
    assert Fernet(key.encode()).decrypt(raw) == b"PGDMP-sentinel-payload"  # and reversible


def test_uploaded_key_ends_in_enc_when_encryption_is_on(temp_db_path, monkeypatch):
    from cryptography.fernet import Fernet
    from jobs import backup_db

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(backup_db, "BACKUP_ENCRYPTION_KEY", key)
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _write_plausible_dump)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: None)
    monkeypatch.setattr(backup_db, "notify_background", lambda text: None)

    uploaded = []

    def _fake_upload(path, upload_key):
        # Read the CONTENTS here, inside the mock: run()'s finally block
        # deletes both temp files as soon as run() returns, so this is the
        # only point at which the bytes that were actually "uploaded" can
        # still be inspected.
        with open(path, "rb") as f:
            uploaded.append((upload_key, f.read()))

    monkeypatch.setattr(backup_db, "_upload", _fake_upload)

    backup_db.run()

    assert len(uploaded) == 1
    key_used, contents = uploaded[0]
    assert key_used.endswith(".dump.enc")
    # A key-only assertion isn't enough: a regression that uploads the
    # PLAINTEXT temp file under the correctly ".enc"-suffixed key would still
    # pass it -- a backup labelled encrypted that isn't, the worst failure
    # this feature could have. So assert the uploaded bytes are actually
    # Fernet ciphertext: not the plaintext dump (_write_plausible_dump fills
    # it with b"x" * 4096) and decryptable back to that exact plaintext.
    assert not contents.startswith(b"xxxx")
    assert Fernet(key.encode()).decrypt(contents) == b"x" * 4096


def test_backup_still_runs_unencrypted_when_no_key_is_set(temp_db_path, monkeypatch):
    """A missing key must not stop backups -- a gap is unrecoverable, an
    unencrypted dump is not."""
    from jobs import backup_db

    monkeypatch.setattr(backup_db, "BACKUP_ENCRYPTION_KEY", "")
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _write_plausible_dump)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: None)
    monkeypatch.setattr(backup_db, "notify_background", lambda text: None)

    uploaded = []
    monkeypatch.setattr(backup_db, "_upload", lambda path, key: uploaded.append(key))

    backup_db.run()

    import database as db
    assert len(uploaded) == 1
    assert uploaded[0].endswith(".dump")
    assert not uploaded[0].endswith(".enc")
    assert db.get_setting("backup_last_status") == "ok"


# ── Sustained-failure reminder ────────────────────────────────────────────
#
# Alerting only on status CHANGE meant a failure that persisted went quiet
# after one message. In production the backup failed every day from
# 2026-08-04 to 2026-08-06 and exactly one Telegram alert was sent, so three
# days with no backup looked indistinguishable from three days of health.
# The change-only rule is still right for the common case; what was missing
# is a periodic reminder while the failure is still live.

def _failing_run(monkeypatch, sent):
    from jobs import backup_db
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)
    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_dump_error)


def _at(monkeypatch, day):
    """Pin the job's clock to 2026-08-<day> 04:00 local."""
    import datetime
    import pytz
    from jobs import backup_db
    from config import TIMEZONE
    moment = pytz.timezone(TIMEZONE).localize(datetime.datetime(2026, 8, day, 4, 0, 0))
    monkeypatch.setattr(backup_db, "_now", lambda: moment)


def test_persistent_failure_re_alerts_after_the_reminder_interval(temp_db_path, monkeypatch):
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    _failing_run(monkeypatch, sent)

    _at(monkeypatch, 1)
    backup_db.run()
    assert len(sent) == 1                      # the transition

    for day in (2, 3, 5, 7):                   # still inside the interval
        _at(monkeypatch, day)
        backup_db.run()
    assert len(sent) == 1, f"nagged early: {sent}"

    _at(monkeypatch, 8)                        # 7 days after the first alert
    backup_db.run()
    assert len(sent) == 2, "no reminder after the interval elapsed"
    assert "FAILED for 7 days" in sent[1], sent[1]

    _at(monkeypatch, 9)
    backup_db.run()
    assert len(sent) == 2, "reminder repeated the very next day"

    _at(monkeypatch, 15)                       # 7 days after the reminder
    backup_db.run()
    assert len(sent) == 3, "the reminder does not recur"
    assert "FAILED for 14 days" in sent[2], sent[2]


def test_reminder_text_carries_no_exception_detail(temp_db_path, monkeypatch):
    """The redaction boundary applies to the reminder exactly as it does to
    the first alert -- it is built from the closed-set status, nothing else."""
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: True)

    def _raise_with_secret(path):
        raise RuntimeError("postgres://user:SENTINELSECRET@host:5432/railway")

    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_with_secret)

    _at(monkeypatch, 1)
    backup_db.run()
    _at(monkeypatch, 20)
    backup_db.run()

    assert len(sent) == 2
    assert "SENTINELSECRET" not in " ".join(sent)


def test_recovery_resets_the_reminder_clock(temp_db_path, monkeypatch):
    """After a recovery, a later failure is a fresh transition -- it must not
    inherit the previous streak's age or be suppressed by its alert clock."""
    import database as db
    from jobs import backup_db

    db.set_setting("backup_last_status", "ok")
    sent = []
    _failing_run(monkeypatch, sent)
    _at(monkeypatch, 1)
    backup_db.run()
    assert len(sent) == 1

    monkeypatch.setattr(backup_db, "_dump_to_file", lambda path: _write_plausible_dump(path))
    monkeypatch.setattr(backup_db, "_upload", lambda *a, **k: None)
    monkeypatch.setattr(backup_db, "_verify_uploaded", lambda key: True)
    monkeypatch.setattr(backup_db, "_prune_old_backups", lambda: None)
    _at(monkeypatch, 2)
    backup_db.run()
    assert len(sent) == 2 and "recover" in sent[1].lower()

    monkeypatch.setattr(backup_db, "_dump_to_file", _raise_dump_error)
    _at(monkeypatch, 3)
    backup_db.run()

    assert len(sent) == 3
    # "FAILED for N days", not the "90 days" boilerplate every message carries.
    assert "FAILED for" not in sent[2], f"a fresh failure claimed a stale streak: {sent[2]}"


def test_never_configured_deploy_is_never_nagged(temp_db_path, monkeypatch):
    """Not opting into backups is a documented clean no-op (CLAUDE.md). It
    alerts on neither the first observation nor any reminder afterwards."""
    import database as db
    from jobs import backup_db

    assert db.get_setting("backup_last_status") is None
    sent = []
    monkeypatch.setattr(backup_db, "notify_background", lambda text: sent.append(text))
    monkeypatch.setattr(backup_db, "_using_postgres", lambda: True)
    monkeypatch.setattr(backup_db, "_is_configured", lambda: False)

    for day in (1, 2, 10, 20, 28):
        _at(monkeypatch, day)
        backup_db.run()

    assert sent == []
