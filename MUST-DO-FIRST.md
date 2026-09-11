# Before executing anything

`config.py` loads `.env`. A local script can therefore reach real services even
when shell variables were unset. Do not assume a checkout is isolated.

- Read `CLAUDE.md` and `CONTRIBUTING.md` before changing behavior.
- Use Python 3.11 and the explicit virtualenv interpreter.
- Run tests through `venv/bin/python scripts/test_safe.py`; this disables dotenv
  loading in process, clears integration credentials, and selects scratch SQLite.
- Do not run `main.py` or the ASGI lifespan with real credentials as a test.
  Configured Gmail, Calendar, and bank jobs run on startup.
- `scripts/cleardb.py`, `scripts/drop_v1_archive.py`, and
  `scripts/simplefin_backfill.py` modify persistent data. Confirm their target and
  the requested operation before execution. `--help` is the safe inspection path.
- `scripts/verify_backup.py` downloads and decrypts a real backup. Use it only
  for an authorized backup verification task. A successful upload alone does not
  prove a backup is readable, and a readable dump alone does not prove restoration.
- Keep real account identifiers, credentials, snapshots, and private audit reports
  outside Git. `.gitignore` is not protection against `git add -f`.
- Do not print secret values, credential-bearing URLs, or backup payloads.
- Do not change AGENTS.md without explicit confirmation. Ask before editing
  CLAUDE.md when a task has not already authorized that edit.

SQLite does not prove production PostgreSQL behavior. Secure session cookies need
HTTPS for browser testing. Cron jobs must use `main._cron()` so the configured
timezone is honored rather than the host/container timezone.
