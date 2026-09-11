import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Web app login password (single-user). Required.
APP_PASSWORD = os.environ["APP_PASSWORD"]
if not APP_PASSWORD.strip():
    raise ValueError("APP_PASSWORD must be set to a nonempty, unique password before startup.")

# Telegram is now an OPTIONAL send-only notification channel.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0") or "0")

TIMEZONE = os.getenv("TIMEZONE", "America/Denver")

# coach-web usage reporting. After each Anthropic call, ai_metrics.py POSTs the
# token counts to the app-builder-coach dashboard, which prices them — that is
# the only way this app's real API spend is knowable (Anthropic's Admin API is
# unavailable on an individual org). Read here rather than inside usage.py so
# config.py stays the only file touching os.environ; usage.py is a verbatim copy
# of the canonical reporter and must not be edited. Both unset = reporting
# no-ops, so local dev is unaffected.
COACH_USAGE_URL = os.getenv("COACH_USAGE_URL", "")
COACH_USAGE_TOKEN = os.getenv("COACH_USAGE_TOKEN", "")

# Google OAuth2 (shared by Calendar + Gmail; refresh token must carry both scopes)
GOOGLE_CALENDAR_CLIENT_ID = os.getenv("GOOGLE_CALENDAR_CLIENT_ID", "")
GOOGLE_CALENDAR_CLIENT_SECRET = os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET", "")
GOOGLE_CALENDAR_REFRESH_TOKEN = os.getenv("GOOGLE_CALENDAR_REFRESH_TOKEN", "")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Database: PostgreSQL if DATABASE_URL is set, otherwise SQLite (local dev fallback)
_raw_db_url = os.getenv("DATABASE_URL", "")
if _raw_db_url.startswith("postgres://"):
    _raw_db_url = _raw_db_url.replace("postgres://", "postgresql://", 1)
DATABASE_URL = _raw_db_url
DATABASE_PATH = os.getenv("DATABASE_PATH", "weekly_updates.db")

# Job schedules
GMAIL_SCAN_INTERVAL_HOURS = int(os.getenv("GMAIL_SCAN_INTERVAL_HOURS", "4"))
GMAIL_SCAN_LOOKBACK_DAYS = int(os.getenv("GMAIL_SCAN_LOOKBACK_DAYS", "7"))
CALENDAR_SCAN_HOUR = int(os.getenv("CALENDAR_SCAN_HOUR", "6"))
WEEKLY_PUSH_HOUR = int(os.getenv("WEEKLY_PUSH_HOUR", "9"))

# SimpleFIN bank ingestion. The access URL is a BEARER CREDENTIAL — it carries
# its own authentication inside the URL. It is read here and nowhere else, never
# logged, never stored in the database, never returned by any route. Unset =
# jobs/sync_bank.py no-ops with a "not configured" status, so local dev and an
# un-configured deploy are unaffected.
SIMPLEFIN_ACCESS_URL = os.getenv("SIMPLEFIN_ACCESS_URL", "")
SIMPLEFIN_SYNC_INTERVAL_HOURS = int(os.getenv("SIMPLEFIN_SYNC_INTERVAL_HOURS", "12"))
# SimpleFIN caps history at a rolling 90 days; asking for more is harmless (the
# API caps it and reports the cap as a non-fatal error) but pointless.
SIMPLEFIN_LOOKBACK_DAYS = int(os.getenv("SIMPLEFIN_LOOKBACK_DAYS", "90"))
# How many days apart the two halves of a transfer may post and still pair.
# Settlement routinely lags a day or two; 3 is deliberately generous because a
# missed pair becomes phantom spending, which is the failure mode that matters.
PAIR_WINDOW_DAYS = int(os.getenv("PAIR_WINDOW_DAYS", "3"))
# Payroll signatures, comma-separated, matched case-insensitively against a
# transaction's payee and description. Deliberately conservative: only an
# unpaired deposit that matches one of these is called income. See the SoFi
# hazard in the spec — an unmatched deposit is never silently income.
INCOME_PAYEE_HINTS = [
    h.strip() for h in os.getenv("INCOME_PAYEE_HINTS", "").split(",") if h.strip()
]

# Session lifetime, in days. The session token itself is random
# (secrets.token_urlsafe) and stored server-side — APP_PASSWORD can no longer be
# used to compute a valid cookie offline.
SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "14"))

# Absolute session lifetime cap, in days, regardless of sliding renewal. Sliding
# renewal only ever advances expires_at, never created_at — without a cap, an
# actively-used (or stolen and replayed) cookie would renew forever.
SESSION_MAX_DAYS = int(os.getenv("SESSION_MAX_DAYS", "60"))

# Weekly database backup — an off-Railway S3-compatible destination (e.g.
# Cloudflare R2, Backblaze B2). All optional: jobs/backup_db.py logs a warning
# and no-ops when any is unset, so local dev and an un-configured deploy are
# unaffected.
BACKUP_S3_BUCKET = os.getenv("BACKUP_S3_BUCKET", "")
BACKUP_S3_ENDPOINT = os.getenv("BACKUP_S3_ENDPOINT", "")
BACKUP_S3_ACCESS_KEY = os.getenv("BACKUP_S3_ACCESS_KEY", "")
BACKUP_S3_SECRET_KEY = os.getenv("BACKUP_S3_SECRET_KEY", "")
BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "4"))

# Fernet key (44-char urlsafe base64) used to encrypt the pg_dump before it
# leaves the machine. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Unset means backups still run, unencrypted, with a logged warning -- a
# backup gap is unrecoverable (SimpleFIN keeps 90 days), an unencrypted
# backup is not. Store the key somewhere OTHER than Railway as well, or a
# Railway-side loss takes the backups with it.
BACKUP_ENCRYPTION_KEY = os.getenv("BACKUP_ENCRYPTION_KEY", "")

# Discrete PostgreSQL connection vars — Railway's Postgres plugin sets these
# directly, already decoded (no percent-encoding to strip). jobs/backup_db.py
# prefers them over parsing DATABASE_URL: urlparse() never percent-decodes
# the userinfo, so a password containing "@", "%", or "/" would otherwise
# reach pg_dump literally percent-encoded and authentication would fail
# permanently. All optional — unset unless Railway (or the operator) provides
# them; jobs/backup_db.py falls back to parsing DATABASE_URL when any is
# missing.
PGHOST = os.getenv("PGHOST", "")
PGPORT = os.getenv("PGPORT", "")
PGUSER = os.getenv("PGUSER", "")
PGPASSWORD = os.getenv("PGPASSWORD", "")
PGDATABASE = os.getenv("PGDATABASE", "")
# Optional even within the discrete-var path — e.g. sslmode. Also used to
# carry sslmode forward when falling back to parsing DATABASE_URL's query
# string, whose sslmode= (etc.) is otherwise silently dropped by the rebuilt
# connection.
PGSSLMODE = os.getenv("PGSSLMODE", "")
