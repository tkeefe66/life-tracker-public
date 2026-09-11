"""
Database layer — supports PostgreSQL (Railway) and SQLite (local dev).
Set DATABASE_URL to use PostgreSQL. Leave it blank to fall back to SQLite.
"""

import datetime
import logging
from contextlib import contextmanager
from datetime import date

import pytz

from config import DATABASE_URL, DATABASE_PATH, TIMEZONE
from metrics import AMBIGUOUS_CONFIDENCE, NIGHT_CUTOFF_HOUR


def _today() -> date:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).date()

logger = logging.getLogger(__name__)

USE_POSTGRES = bool(DATABASE_URL)

# The v1 archive tables — read by no v2 code path. The ONLY tables
# dump_table/drop_table will touch, so a bug or a bad argument can never
# reach a live v2 table.
_DUMPABLE_TABLES = frozenset({
    "life_log_entries", "people", "life_log_people", "activity_log",
    "habits", "habit_logs", "categories", "conversation_state",
    "accomplishments", "weekly_focus", "later_items",
})

# ── Connection helpers ────────────────────────────────────────────────────────

@contextmanager
def _cursor(write=False):
    """Yield a dict-like cursor. Commits on exit if write=True."""
    if USE_POSTGRES:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            if write:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
    else:
        import sqlite3
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            yield cur
            if write:
                conn.commit()
        finally:
            cur.close()
            conn.close()


def _p():
    """Return the correct placeholder character for the active database."""
    return "%s" if USE_POSTGRES else "?"


def _serial():
    return "SERIAL" if USE_POSTGRES else "INTEGER"


def _bool_type():
    return "BOOLEAN" if USE_POSTGRES else "INTEGER"


# ── Rides: effective-date (night cutoff) SQL helpers ──────────────────────────
# Dialect-aware twin of metrics.effective_date — both must agree on the exact
# cutoff. See rides queries below for usage.

def _ride_true_ts_expr() -> str:
    """SQL expression resolving to the TRUE ride timestamp for a `rides` row:
    `ride_key` when it has the ISO shape ('YYYY-MM-DDTHH:MM...') the parsed
    trip time takes, else `ride_at` (the email-arrival fallback). `ride_key`
    falls back to the non-ISO "{day}|{subject}" shape when parsing failed
    (see jobs/scan_gmail.py) — that shape never matches the pattern below."""
    if USE_POSTGRES:
        return "CASE WHEN ride_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}' THEN ride_key ELSE ride_at END"
    return ("CASE WHEN ride_key GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]*' "
            "THEN ride_key ELSE ride_at END")


def _effective_date_expr(ts_expr: str) -> str:
    """Wrap a SQL timestamp expression to bucket/filter it by "effective
    date" — a timestamp before NIGHT_CUTOFF_HOUR local belongs to the
    PREVIOUS calendar day (metrics.effective_date is the pure Python twin).
    Returns a TEXT 'YYYY-MM-DD' expression, matching every other date
    comparison in this module (e.g. `substr(ride_at, 1, 10) >= {p}`).

    Deliberately NOT `date(<ts>, '-N hours')` / `(<ts>::timestamp - interval)`
    parsed as a real timestamp: `ride_at` strings carry a trailing local UTC
    offset (e.g. "-06:00"), and SQLite's date()/datetime() functions silently
    normalize an offset-bearing string to UTC before doing arithmetic on it —
    verified empirically: `date('2026-07-15T02:34:00-06:00', '-4 hours')`
    returns '2026-07-15', not '2026-07-14', because SQLite converts through
    UTC first. That would defeat the cutoff for exactly the timestamps that
    need it most (the ride_at fallback). Operating on the literal wall-clock
    date/hour substrings sidesteps timezone parsing entirely on both dialects,
    so they can never drift apart."""
    hour_expr = f"CAST(substr(({ts_expr}), 12, 2) AS INTEGER)"
    day_expr = f"substr(({ts_expr}), 1, 10)"
    if USE_POSTGRES:
        shifted = f"to_char(({day_expr})::date - INTERVAL '1 day', 'YYYY-MM-DD')"
    else:
        shifted = f"date({day_expr}, '-1 day')"
    return f"(CASE WHEN {hour_expr} < {NIGHT_CUTOFF_HOUR} THEN {shifted} ELSE {day_expr} END)"


# ── Schema ────────────────────────────────────────────────────────────────────

def initialize_db():
    serial = _serial()
    bool_t = _bool_type()

    if USE_POSTGRES:
        _init_postgres(serial, bool_t)
    else:
        _init_sqlite(bool_t)

    _init_v2_tables()

    logger.info("Database initialized (%s)", "PostgreSQL" if USE_POSTGRES else "SQLite")


def _init_postgres(serial, bool_t):
    with _cursor(write=True) as c:
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS accomplishments (
                id {serial} PRIMARY KEY,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS weekly_focus (
                id {serial} PRIMARY KEY,
                week_start TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS later_items (
                id {serial} PRIMARY KEY,
                content TEXT NOT NULL,
                target_date TEXT,
                source TEXT DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS habits (
                id {serial} PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                days_of_week TEXT NOT NULL,
                active {bool_t} DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS habit_logs (
                id {serial} PRIMARY KEY,
                habit_id INTEGER NOT NULL REFERENCES habits(id),
                date TEXT NOT NULL,
                completed {bool_t},
                miss_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(habit_id, date)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS focus_summary_cache (
                week_start TEXT PRIMARY KEY,
                summary_text TEXT NOT NULL,
                entry_count INTEGER NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS later_org_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                groups_json TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversation_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'idle',
                entry_date TEXT,
                pending_dates TEXT DEFAULT '[]',
                later_item_draft TEXT,
                temp_data TEXT DEFAULT '{}',
                bot_start_date TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS calendar_sync_log (
                id {serial} PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                active BOOLEAN DEFAULT TRUE,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS life_log_entries (
                id {serial} PRIMARY KEY,
                date_start DATE NOT NULL,
                date_end DATE,
                categories TEXT[] NOT NULL DEFAULT '{{}}',
                description TEXT NOT NULL,
                location TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'confirmed',
                source TEXT NOT NULL DEFAULT 'manual',
                source_id TEXT,
                ai_proposed_at TIMESTAMP,
                user_confirmed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_date_start ON life_log_entries(date_start)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_status ON life_log_entries(status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_source_id ON life_log_entries(source_id)")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS people (
                id {serial} PRIMARY KEY,
                name TEXT NOT NULL,
                aliases TEXT[] DEFAULT '{{}}',
                relationship_type TEXT,
                status TEXT DEFAULT 'active',
                first_seen DATE,
                last_seen DATE,
                start_date DATE,
                end_date DATE,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_people_name_lower ON people(LOWER(name))")
        c.execute("""
            CREATE TABLE IF NOT EXISTS life_log_people (
                entry_id INTEGER NOT NULL REFERENCES life_log_entries(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                PRIMARY KEY (entry_id, person_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_people_person_id ON life_log_people(person_id)")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS activity_log (
                id {serial} PRIMARY KEY,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TIMESTAMP,
                payload JSONB,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                promoted_to_life_log {bool_t} DEFAULT FALSE,
                UNIQUE(source, source_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_activity_log_occurred_at ON activity_log(occurred_at)")
        # Seed initial categories
        INITIAL_CATEGORIES = [
            "Vacation", "Relationship", "Outdoors", "Skiing", "Concert",
            "Wedding", "Bachelor Party", "Life Event", "Visitors", "Tattoo",
            "Move/Housing", "Job/Career", "Health", "Achievement", "Pet", "Loss",
        ]
        for name in INITIAL_CATEGORIES:
            c.execute(
                f"INSERT INTO categories (name) VALUES ({_p()}) ON CONFLICT DO NOTHING",
                (name,),
            )
        # Add new columns safely
        for col, defn in [
            ("later_item_draft", "TEXT"),
            ("temp_data", "TEXT DEFAULT '{}'"),
        ]:
            c.execute(f"ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS {col} {defn}")
        for col, defn in [
            ("status", "TEXT DEFAULT 'pending'"),
            ("ai_status", "TEXT"),
            ("ai_notes", "TEXT"),
            ("event_id", "TEXT"),
            ("end_date", "TEXT"),
        ]:
            c.execute(f"ALTER TABLE later_items ADD COLUMN IF NOT EXISTS {col} {defn}")
        for col, defn in [
            ("sheet_synced", "BOOLEAN DEFAULT FALSE"),
            ("sheet_deleted", "BOOLEAN DEFAULT FALSE"),
        ]:
            c.execute(f"ALTER TABLE accomplishments ADD COLUMN IF NOT EXISTS {col} {defn}")
            c.execute(f"ALTER TABLE weekly_focus ADD COLUMN IF NOT EXISTS {col} {defn}")
        for col, defn in [
            ("recurrence_type", "TEXT DEFAULT 'weekly'"),
            ("recurrence_config", "TEXT DEFAULT '{}'"),
        ]:
            c.execute(f"ALTER TABLE habits ADD COLUMN IF NOT EXISTS {col} {defn}")
        for col, defn in [
            ("parent_id", "INTEGER REFERENCES life_log_entries(id) ON DELETE SET NULL"),
            ("story_type", "TEXT"),
            ("why_mattered", "TEXT"),
            ("highlights", "TEXT"),  # JSON-encoded list
            ("extras", "TEXT"),       # JSON-encoded dict
        ]:
            c.execute(f"ALTER TABLE life_log_entries ADD COLUMN IF NOT EXISTS {col} {defn}")
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_life_log_entries_parent_id "
            "ON life_log_entries(parent_id)"
        )
        # Migrate Archie Meds to monthly_date (1st of every month)
        c.execute(
            "UPDATE habits SET recurrence_type='monthly_date', "
            "recurrence_config='{\"day\": 1}', days_of_week='[]' "
            "WHERE LOWER(name)='archie meds' AND recurrence_type='weekly'"
        )

        p = _p()
        c.execute(
            f"INSERT INTO conversation_state (id, state, bot_start_date) VALUES (1, 'idle', {p}) "
            f"ON CONFLICT(id) DO NOTHING",
            (_today().isoformat(),),
        )


def _init_sqlite(bool_t):
    def _add_col(c, table, col, defn):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        except Exception:
            pass

    with _cursor(write=True) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS accomplishments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS weekly_focus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL, content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS later_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL, target_date TEXT,
                source TEXT DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, description TEXT NOT NULL,
                days_of_week TEXT NOT NULL,
                active {bool_t} DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL REFERENCES habits(id),
                date TEXT NOT NULL, completed {bool_t},
                miss_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(habit_id, date)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS focus_summary_cache (
                week_start TEXT PRIMARY KEY,
                summary_text TEXT NOT NULL, entry_count INTEGER NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS later_org_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                groups_json TEXT NOT NULL, item_count INTEGER NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversation_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'idle',
                entry_date TEXT, pending_dates TEXT DEFAULT '[]',
                later_item_draft TEXT, temp_data TEXT DEFAULT '{}',
                bot_start_date TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS calendar_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                active INTEGER DEFAULT 1,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS life_log_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_start TEXT NOT NULL,
                date_end TEXT,
                categories TEXT NOT NULL DEFAULT '[]',
                description TEXT NOT NULL,
                location TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'confirmed',
                source TEXT NOT NULL DEFAULT 'manual',
                source_id TEXT,
                ai_proposed_at TEXT,
                user_confirmed_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_date_start ON life_log_entries(date_start)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_status ON life_log_entries(status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_entries_source_id ON life_log_entries(source_id)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                aliases TEXT DEFAULT '[]',
                relationship_type TEXT,
                status TEXT DEFAULT 'active',
                first_seen TEXT,
                last_seen TEXT,
                start_date TEXT,
                end_date TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_people_name_lower ON people(LOWER(name))")
        c.execute("""
            CREATE TABLE IF NOT EXISTS life_log_people (
                entry_id INTEGER NOT NULL REFERENCES life_log_entries(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                PRIMARY KEY (entry_id, person_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_life_log_people_person_id ON life_log_people(person_id)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT,
                payload TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                promoted_to_life_log INTEGER DEFAULT 0,
                UNIQUE(source, source_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_activity_log_occurred_at ON activity_log(occurred_at)")
        # Seed initial categories
        INITIAL_CATEGORIES = [
            "Vacation", "Relationship", "Outdoors", "Skiing", "Concert",
            "Wedding", "Bachelor Party", "Life Event", "Visitors", "Tattoo",
            "Move/Housing", "Job/Career", "Health", "Achievement", "Pet", "Loss",
        ]
        for name in INITIAL_CATEGORIES:
            c.execute(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                (name,),
            )
        for col, defn in [("later_item_draft", "TEXT"), ("temp_data", "TEXT DEFAULT '{}'")]:
            _add_col(c, "conversation_state", col, defn)
        for col, defn in [
            ("status", "TEXT DEFAULT 'pending'"),
            ("ai_status", "TEXT"),
            ("ai_notes", "TEXT"),
            ("event_id", "TEXT"),
            ("end_date", "TEXT"),
        ]:
            _add_col(c, "later_items", col, defn)
        for table in ["accomplishments", "weekly_focus"]:
            _add_col(c, table, "sheet_synced", "INTEGER DEFAULT 0")
            _add_col(c, table, "sheet_deleted", "INTEGER DEFAULT 0")
        _add_col(c, "habits", "recurrence_type", "TEXT DEFAULT 'weekly'")
        _add_col(c, "habits", "recurrence_config", "TEXT DEFAULT '{}'")
        for col, defn in [
            ("parent_id", "INTEGER REFERENCES life_log_entries(id)"),
            ("story_type", "TEXT"),
            ("why_mattered", "TEXT"),
            ("highlights", "TEXT"),
            ("extras", "TEXT"),
        ]:
            _add_col(c, "life_log_entries", col, defn)
        try:
            c.execute(
                "CREATE INDEX IF NOT EXISTS ix_life_log_entries_parent_id "
                "ON life_log_entries(parent_id)"
            )
        except Exception:
            pass
        # Migrate Archie Meds to monthly_date (1st of every month)
        c.execute(
            "UPDATE habits SET recurrence_type='monthly_date', "
            "recurrence_config='{\"day\": 1}', days_of_week='[]' "
            "WHERE name LIKE 'Archie Meds' AND (recurrence_type IS NULL OR recurrence_type='weekly')"
        )

        c.execute(
            "INSERT OR IGNORE INTO conversation_state (id, state, bot_start_date) VALUES (1, 'idle', ?)",
            (_today().isoformat(),),
        )


# ── v2 schema ─────────────────────────────────────────────────────────────────

def _init_v2_tables():
    serial = _serial()
    bool_t = _bool_type()
    with _cursor(write=True) as c:
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS checkins (
                id {serial} PRIMARY KEY,
                date TEXT NOT NULL,
                type TEXT NOT NULL,
                level INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, type)
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS delivery_orders (
                id {serial} PRIMARY KEY,
                gmail_message_id TEXT NOT NULL UNIQUE,
                service TEXT NOT NULL,
                subject TEXT DEFAULT '',
                ordered_at TEXT NOT NULL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id {serial} PRIMARY KEY,
                gcal_event_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                is_social {bool_t},
                confidence REAL,
                classified_at TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                metric TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                value INTEGER NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS weekly_reflections (
                id {serial} PRIMARY KEY,
                week_start TEXT NOT NULL UNIQUE,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS rides (
                id {serial} PRIMARY KEY,
                gmail_message_id TEXT NOT NULL UNIQUE,
                service TEXT NOT NULL,
                ride_at TEXT NOT NULL,
                ride_key TEXT,
                subject TEXT DEFAULT '',
                amount REAL,
                ai_is_work {bool_t},
                ai_confidence REAL,
                user_is_work {bool_t},
                is_cancellation {bool_t},
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_rides_ride_key ON rides(ride_key)")
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS bank_accounts (
                id {serial} PRIMARY KEY,
                simplefin_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                org TEXT DEFAULT '',
                kind TEXT DEFAULT '',
                role TEXT NOT NULL DEFAULT 'unknown',
                active {bool_t} NOT NULL DEFAULT TRUE,
                last_synced_at TEXT
            )
        """)
        # Balances are deliberately absent — see the spec. Not needed for spending
        # analysis, and the most sensitive field is safest when never stored.
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS bank_transactions (
                id {serial} PRIMARY KEY,
                simplefin_id TEXT NOT NULL UNIQUE,
                account_id INTEGER NOT NULL,
                posted TEXT NOT NULL,
                transacted_at TEXT,
                amount REAL NOT NULL,
                description TEXT DEFAULT '',
                payee TEXT DEFAULT '',
                memo TEXT DEFAULT '',
                mcc TEXT,
                flow TEXT,
                user_flow TEXT,
                pair_id TEXT,
                ambiguous {bool_t} NOT NULL DEFAULT FALSE,
                user_note TEXT,
                suggested_flow TEXT,
                user_label TEXT,
                suggested_label TEXT,
                user_no_label {bool_t} NOT NULL DEFAULT FALSE,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_bank_txn_posted ON bank_transactions(posted)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_bank_txn_account ON bank_transactions(account_id)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_sessions_expires_at ON sessions(expires_at)")
        if USE_POSTGRES:
            c.execute("ALTER TABLE delivery_orders ADD COLUMN IF NOT EXISTS amount REAL")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(delivery_orders)").fetchall()]
            if "amount" not in cols:
                c.execute("ALTER TABLE delivery_orders ADD COLUMN amount REAL")

        if USE_POSTGRES:
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_title TEXT")
            c.execute(f"ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_is_social {bool_t}")
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'gcal'")
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS amount REAL")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(calendar_events)").fetchall()]
            for name, defn in (("user_title", "TEXT"), ("user_is_social", bool_t),
                               ("source", "TEXT DEFAULT 'gcal'"), ("amount", "REAL")):
                if name not in cols:
                    c.execute(f"ALTER TABLE calendar_events ADD COLUMN {name} {defn}")

        # user_note: nullable TEXT on bank_transactions. A user column — the sync
        # never reads or writes it (Override + Learning rule 3).
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS user_note TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_transactions)").fetchall()]
            if "user_note" not in cols:
                c.execute("ALTER TABLE bank_transactions ADD COLUMN user_note TEXT")

        # suggested_flow: nullable TEXT on bank_transactions. A DERIVED column the
        # sync owns exclusively (see set_bank_suggestions_bulk) — never read by any
        # aggregate (app/money.py must not reference it).
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS suggested_flow TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_transactions)").fetchall()]
            if "suggested_flow" not in cols:
                c.execute("ALTER TABLE bank_transactions ADD COLUMN suggested_flow TEXT")

        # user_label: nullable TEXT on bank_transactions. A user column — the
        # sync never reads or writes it (Override + Learning rule 3). Labels
        # double as categories; the vocabulary is SELECT DISTINCT, not a table.
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS user_label TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_transactions)").fetchall()]
            if "user_label" not in cols:
                c.execute("ALTER TABLE bank_transactions ADD COLUMN user_label TEXT")

        # suggested_label: nullable TEXT on bank_transactions. DERIVED — the
        # sync's label-suggestion pass owns it exclusively and recomputes it
        # for the whole table every run (unlike suggested_flow's write-once);
        # never written by a route, never touches user_label.
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS suggested_label TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_transactions)").fetchall()]
            if "suggested_label" not in cols:
                c.execute("ALTER TABLE bank_transactions ADD COLUMN suggested_label TEXT")

        # user_no_label: the user's durable "this row gets no label" verdict.
        # A USER column — the sync and suggestion pass never write it; without
        # it a rejected suggestion would come back on the next full recompute.
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS "
                      "user_no_label BOOLEAN NOT NULL DEFAULT FALSE")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_transactions)").fetchall()]
            if "user_no_label" not in cols:
                c.execute("ALTER TABLE bank_transactions ADD COLUMN "
                          "user_no_label INTEGER NOT NULL DEFAULT 0")

        # nickname: nullable TEXT on bank_accounts. A USER column — the sync's
        # upsert never touches it (same footing as role/active). Resolved via
        # COALESCE(NULLIF(nickname, ''), name) in exactly two places:
        # get_bank_accounts (display_name) and _BANK_TXN_SELECT (account_name).
        if USE_POSTGRES:
            c.execute("ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS nickname TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(bank_accounts)").fetchall()]
            if "nickname" not in cols:
                c.execute("ALTER TABLE bank_accounts ADD COLUMN nickname TEXT")

        # is_cancellation: nullable bool on rides. NULL means "not yet
        # determined" (pre-migration rows, or a row inserted before this
        # column existed) — jobs/scan_gmail.py backfills it the next time the
        # ride's message is seen in a scan (has_ride guard already refetches
        # it every run). Label-only: never changes count/spend aggregates.
        if USE_POSTGRES:
            c.execute("ALTER TABLE rides ADD COLUMN IF NOT EXISTS is_cancellation BOOLEAN")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(rides)").fetchall()]
            if "is_cancellation" not in cols:
                c.execute("ALTER TABLE rides ADD COLUMN is_cancellation INTEGER")

        # user_date: nullable TEXT ('YYYY-MM-DD') on delivery_orders and
        # rides — the user's ±1-day nudge when the automatic night cutoff
        # got it wrong. A USER column: the scan never reads or writes it
        # (Override + Learning rule 3). Resolved as
        # COALESCE(user_date, <effective date>) inside the range queries;
        # NULL means "trust the automatic day", which is why moving an item
        # back to its automatic day stores NULL rather than the date.
        if USE_POSTGRES:
            c.execute("ALTER TABLE delivery_orders ADD COLUMN IF NOT EXISTS user_date TEXT")
            c.execute("ALTER TABLE rides ADD COLUMN IF NOT EXISTS user_date TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(delivery_orders)").fetchall()]
            if "user_date" not in cols:
                c.execute("ALTER TABLE delivery_orders ADD COLUMN user_date TEXT")
            cols = [r["name"] for r in c.execute("PRAGMA table_info(rides)").fetchall()]
            if "user_date" not in cols:
                c.execute("ALTER TABLE rides ADD COLUMN user_date TEXT")

        # Date tracking + places (2026-07-30 spec). is_date/location are
        # GCAL-OWNED (the scan's upsert overwrites them, same footing as
        # title); user_is_date/user_location are USER columns the scan never
        # touches. Resolved in SQL: COALESCE(user_is_date, is_date) and
        # COALESCE(NULLIF(user_location, ''), location).
        if USE_POSTGRES:
            c.execute(f"ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS is_date {bool_t}")
            c.execute(f"ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_is_date {bool_t}")
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS location TEXT")
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_location TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(calendar_events)").fetchall()]
            for name, defn in (("is_date", bool_t), ("user_is_date", bool_t),
                               ("location", "TEXT"), ("user_location", "TEXT")):
                if name not in cols:
                    c.execute(f"ALTER TABLE calendar_events ADD COLUMN {name} {defn}")

        # user_removed: nullable boolean on calendar_events — "this occurrence
        # didn't happen" (user_removed), split out from "this event type
        # isn't social" (user_is_social). See spec
        # docs/superpowers/specs/2026-07-30-social-classification-granularity-design.md
        # for the incident this fixes: the "Didn't happen" button used to
        # write user_is_social=false, and get_classification_examples fed
        # every user_is_social override back to the classifier as a few-shot
        # example — one canceled kickball taught the model kickball isn't
        # social, which then misclassified real kickball events. A USER
        # column: the scan never touches it. `user_removed_col_existed` gates
        # the one-time data conversion below so it can never re-fire.
        if USE_POSTGRES:
            c.execute("""SELECT column_name FROM information_schema.columns
                         WHERE table_name = 'calendar_events' AND column_name = 'user_removed'""")
            user_removed_col_existed = c.fetchone() is not None
            if not user_removed_col_existed:
                c.execute("ALTER TABLE calendar_events ADD COLUMN user_removed BOOLEAN")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(calendar_events)").fetchall()]
            user_removed_col_existed = "user_removed" in cols
            if not user_removed_col_existed:
                c.execute("ALTER TABLE calendar_events ADD COLUMN user_removed INTEGER")

        if not user_removed_col_existed:
            # One-time conversion, only runs the run the column is first
            # added. Every pre-migration row with source='gcal' AND
            # user_is_social=false was necessarily a "Didn't happen" tap —
            # that was the ONLY way to set user_is_social=false on a
            # detected event before this migration existed (the edit form's
            # "Counts as social" checkbox could also do it, but a checkbox
            # uncheck is itself indistinguishable from "didn't happen" in
            # the old data, and in prod exactly one such row exists: the
            # canceled 2026-07-22 Volo kickball event, which was in fact a
            # cancellation, not a genuine classification correction).
            # Reclassifying it as user_removed=true (and clearing
            # user_is_social back to NULL, its "no verdict yet" state) is
            # the correct one-time read of that history.
            p = _p()
            c.execute(
                f"""UPDATE calendar_events SET user_removed = {p}, user_is_social = NULL
                    WHERE source = 'gcal' AND user_is_social = {p}""",
                (True, False),
            )

        # recurring_event_id: nullable TEXT on calendar_events — Google's
        # recurringEventId, passed through by services/calendar_service.py
        # and jobs/scan_calendar.py. Series membership is what makes it safe
        # to generalize a user's verdict to future occurrences (see
        # get_classification_examples below) — a scan-owned column (mirrors
        # Google's own data), so upsert_calendar_event overwrites it like
        # title/start_at/end_at, never a user column.
        if USE_POSTGRES:
            c.execute("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS recurring_event_id TEXT")
        else:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(calendar_events)").fetchall()]
            if "recurring_event_id" not in cols:
                c.execute("ALTER TABLE calendar_events ADD COLUMN recurring_event_id TEXT")


# ── Check-ins ─────────────────────────────────────────────────────────────────

def record_checkin(day, type_, level=None):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO checkins (date, type, level) VALUES ({p}, {p}, {p})
                ON CONFLICT(date, type) DO UPDATE SET level = excluded.level""",
            (day, type_, level),
        )


def delete_checkin(day, type_):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"DELETE FROM checkins WHERE date = {p} AND type = {p}", (day, type_))


def get_checkins_range(start, end):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT date, type, level FROM checkins WHERE date >= {p} AND date <= {p} ORDER BY date",
            (start, end),
        )
        return [dict(r) for r in c.fetchall()]


# ── Delivery orders ───────────────────────────────────────────────────────────

def has_delivery_order(gmail_message_id):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT 1 FROM delivery_orders WHERE gmail_message_id = {p}", (gmail_message_id,))
        return c.fetchone() is not None


def add_delivery_order(gmail_message_id, service, ordered_at, subject, amount=None):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO delivery_orders (gmail_message_id, service, ordered_at, subject, amount)
                VALUES ({p}, {p}, {p}, {p}, {p}) ON CONFLICT(gmail_message_id) DO NOTHING""",
            (gmail_message_id, service, ordered_at, subject, amount),
        )
        return c.rowcount > 0


def find_delivery_order(service, day, subject):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT id, amount FROM delivery_orders
                WHERE service = {p} AND substr(ordered_at, 1, 10) = {p} AND subject = {p}""",
            (service, day, subject),
        )
        row = c.fetchone()
        return dict(row) if row else None


def set_delivery_amount(order_id, amount):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE delivery_orders SET amount = {p} WHERE id = {p}", (amount, order_id))


def get_delivery_orders_range(start_day, end_day):
    """Buckets AND filters by the RESOLVED day: COALESCE(user_date, effective
    date of ordered_at) — the night cutoff (_effective_date_expr) plus the
    user's ±1-day nudge. Both the resolved `day` and the cutoff-only
    `auto_day` are exposed so callers (and the UI's nudge affordance) never
    re-derive the CASE logic. `ordered_at` itself is never rewritten."""
    p = _p()
    eff = _effective_date_expr("ordered_at")
    day_expr = f"COALESCE(user_date, {eff})"
    with _cursor() as c:
        c.execute(
            f"""SELECT id, gmail_message_id, service, subject, ordered_at, amount, user_date,
                       {eff} AS auto_day, {day_expr} AS day
                FROM delivery_orders
                WHERE {day_expr} >= {p} AND {day_expr} <= {p}
                ORDER BY ordered_at""",
            (start_day, end_day),
        )
        return [dict(r) for r in c.fetchall()]


def get_delivery_auto_day(order_id):
    """The order's automatic (cutoff-only) day, ignoring user_date — the
    anchor the ±1 nudge validation measures against. None = unknown id."""
    p = _p()
    eff = _effective_date_expr("ordered_at")
    with _cursor() as c:
        c.execute(f"SELECT {eff} AS auto_day FROM delivery_orders WHERE id = {p}", (order_id,))
        row = c.fetchone()
        return row["auto_day"] if row else None


def set_delivery_user_date(order_id, user_date):
    """user_date=None clears the override. Returns False for an unknown id
    so the route can 404."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE delivery_orders SET user_date = {p} WHERE id = {p}", (user_date, order_id))
        return c.rowcount > 0


# ── Calendar events ───────────────────────────────────────────────────────────

def upsert_calendar_event(gcal_event_id, title, start_at, end_at, recurring_event_id=None,
                          location=None, is_date=None):
    """`recurring_event_id` is Google's own data (like title/start_at/end_at),
    never a user column — a re-upsert overwrites it same as the others.
    `location`/`is_date` are Google's own data too — `is_date` is derived from
    the gcal title by the caller via metrics.title_is_date; a re-upsert
    overwrites both (user_is_date/user_location are the user's levers)."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO calendar_events
                    (gcal_event_id, title, start_at, end_at, recurring_event_id, location, is_date)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                ON CONFLICT(gcal_event_id) DO UPDATE
                SET title = excluded.title, start_at = excluded.start_at, end_at = excluded.end_at,
                    recurring_event_id = excluded.recurring_event_id,
                    location = excluded.location, is_date = excluded.is_date""",
            (gcal_event_id, title, start_at, end_at, recurring_event_id, location, is_date),
        )


def event_needs_classification(gcal_event_id):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT is_social FROM calendar_events WHERE gcal_event_id = {p}", (gcal_event_id,))
        row = c.fetchone()
        return row is not None and row["is_social"] is None


def set_event_classification(gcal_event_id, is_social, confidence):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""UPDATE calendar_events
                SET is_social = {p}, confidence = {p}, classified_at = CURRENT_TIMESTAMP
                WHERE gcal_event_id = {p}""",
            (is_social, confidence, gcal_event_id),
        )


def _social_true():
    return "TRUE" if USE_POSTGRES else "1"


def _social_false():
    return "FALSE" if USE_POSTGRES else "0"


def _social_rows(rows):
    """Cast the resolved is_social column to a real bool — SQLite returns 0/1 ints."""
    out = [dict(r) for r in rows]
    for r in out:
        r["is_social"] = bool(r["is_social"])
    return out


def _social_rows_with_uncertain(rows):
    """Same as _social_rows, plus casting the derived `uncertain` and
    `is_date` columns."""
    out = _social_rows(rows)
    for r in out:
        r["uncertain"] = bool(r["uncertain"])
        r["is_date"] = bool(r["is_date"])
    return out


def _resolved_date_expr():
    """Resolved date flag — user verdict wins over the title rule."""
    return "COALESCE(user_is_date, is_date)"


_RESOLVED_LOCATION = "COALESCE(NULLIF(user_location, ''), location)"


def get_social_events_range(start_day, end_day):
    """Resolved-social rows in [start_day, end_day] by end_at (the "has this
    occurred" boundary — see app/scorecard.py's _social_counts). Excludes any
    row the user has marked `user_removed` ("didn't happen") — see the
    calendar_events migration above. Excludes resolved DATES — the social
    floor means non-date social (2026-07-30 date-tracking spec, decision 4)."""
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT gcal_event_id, COALESCE(user_title, title) AS title,
                       COALESCE(user_is_social, is_social) AS is_social, start_at, end_at,
                       source, amount
                FROM calendar_events
                WHERE user_removed IS NOT TRUE
                  AND COALESCE(user_is_social, is_social) = {_social_true()}
                  AND {_resolved_date_expr()} IS NOT TRUE
                  AND substr(end_at, 1, 10) >= {p} AND substr(end_at, 1, 10) <= {p}
                ORDER BY start_at""",
            (start_day, end_day),
        )
        return _social_rows(c.fetchall())


def get_events_for_day(day):
    """Detected/manual events for the Today/Day screen. Three kinds of row
    qualify, all excluding `user_removed` ("didn't happen") events —
    resolved-social, uncertain, and resolved-DATE (dates are excluded from
    social *counting* but must still show on their day, with `is_date` and
    resolved `location` fields; 2026-07-30 date-tracking spec):

    1. Resolved-social rows (COALESCE(user_is_social, is_social) = true) —
       the pre-existing behavior.
    2. Unresolved AND low-confidence rows (`user_is_social IS NULL` and
       `confidence < AMBIGUOUS_CONFIDENCE`) — surfaced regardless of the
       AI's is_social lean, so the day screen can show the "social? Yes/No"
       ambiguity chip even for an event the model leans NOT-social on but
       isn't confident about. These carry `uncertain: true`; every other
       returned row carries `uncertain: false`. See
       docs/superpowers/specs/2026-07-30-social-classification-granularity-design.md
       ("ask when unsure", follows-the-lean counting is unaffected — this
       function's counting cousin, get_social_events_range, is untouched)."""
    p = _p()
    social_true, social_false = _social_true(), _social_false()
    with _cursor() as c:
        c.execute(
            f"""SELECT gcal_event_id, COALESCE(user_title, title) AS title,
                       COALESCE(user_is_social, is_social) AS is_social, start_at, end_at,
                       source, amount,
                       ({_resolved_date_expr()} IS TRUE) AS is_date,
                       {_RESOLVED_LOCATION} AS location,
                       CASE WHEN user_is_social IS NULL AND user_removed IS NOT TRUE
                                 AND confidence IS NOT NULL AND confidence < {p}
                            THEN {social_true} ELSE {social_false} END AS uncertain
                FROM calendar_events
                WHERE user_removed IS NOT TRUE
                  AND substr(start_at, 1, 10) = {p}
                  AND (
                        COALESCE(user_is_social, is_social) = {social_true}
                        OR (user_is_social IS NULL AND confidence IS NOT NULL AND confidence < {p})
                        OR {_resolved_date_expr()} IS TRUE
                      )
                ORDER BY start_at""",
            (AMBIGUOUS_CONFIDENCE, day, AMBIGUOUS_CONFIDENCE),
        )
        return _social_rows_with_uncertain(c.fetchall())


def get_date_events_range(start_day, end_day):
    """Resolved-date rows in [start_day, end_day] by end_at day — the same
    'has this occurred' boundary social counting uses — excluding
    user_removed. The dates series is unscored (not in METRICS); this feeds
    the Insights dates panel and date-kind spend items only."""
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT gcal_event_id, COALESCE(user_title, title) AS title,
                       start_at, end_at, source, amount,
                       {_RESOLVED_LOCATION} AS location
                FROM calendar_events
                WHERE user_removed IS NOT TRUE
                  AND {_resolved_date_expr()} IS TRUE
                  AND substr(end_at, 1, 10) >= {p} AND substr(end_at, 1, 10) <= {p}
                ORDER BY start_at""",
            (start_day, end_day),
        )
        return [dict(r) for r in c.fetchall()]


def add_manual_social_event(event_id, title, start_at, end_at, amount=None,
                            location=None, is_date=False):
    """Manual events are user assertions, so a date flag lands in
    user_is_date (the USER column), not the scan-owned is_date."""
    p = _p()
    user_is_date = True if is_date else None
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO calendar_events
                    (gcal_event_id, title, start_at, end_at, is_social, source, confidence,
                     classified_at, amount, location, user_is_date)
                VALUES ({p}, {p}, {p}, {p}, {_social_true()}, 'manual', 1.0,
                        CURRENT_TIMESTAMP, {p}, {p}, {p})""",
            (event_id, title, start_at, end_at, amount, location, user_is_date),
        )


def get_event(event_id):
    p = _p()
    with _cursor() as c:
        c.execute("SELECT * FROM calendar_events WHERE gcal_event_id = " + p, (event_id,))
        row = c.fetchone()
        return dict(row) if row else None


def set_event_overrides(event_id, updates: dict):
    """Partial UPDATE from a dict of column->value. Only mentioned columns are touched."""
    if not updates:
        return
    p = _p()
    allowed = {"user_title", "user_is_social", "amount", "user_removed",
               "user_is_date", "user_location"}
    cols = [k for k in updates if k in allowed]
    if not cols:
        return
    set_clause = ", ".join(f"{col} = {p}" for col in cols)
    values = [updates[col] for col in cols] + [event_id]
    with _cursor(write=True) as c:
        c.execute(f"UPDATE calendar_events SET {set_clause} WHERE gcal_event_id = {p}", values)


def delete_event(event_id):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"DELETE FROM calendar_events WHERE gcal_event_id = {p}", (event_id,))


def get_classification_examples(limit=10):
    """Few-shot examples for ai_metrics.classify_social_event — generalizing
    only from recurring event SERIES, not one-off corrections. See
    docs/superpowers/specs/2026-07-30-social-classification-granularity-design.md:
    a single "Didn't happen" tap on a one-off kickball event used to poison
    the classifier into thinking kickball in general isn't social, because
    every user_is_social override was fed back as an example regardless of
    whether it generalizes.

    A row qualifies only with a non-null user_is_social AND a non-null
    recurring_event_id — series membership is what makes generalizing safe.
    Rows are grouped by recurring_event_id; a series whose confirmed members
    carry CONTRADICTORY verdicts is excluded entirely (split opinion inside
    one series is not something to hand the model as ground truth); an
    agreeing series contributes exactly one example, its most recent
    verdict. One-off events (recurring_event_id IS NULL) never appear.
    Capped at `limit`, newest-verdict-first across series."""
    with _cursor() as c:
        c.execute(
            """SELECT id, COALESCE(user_title, title) AS title, user_is_social, recurring_event_id
               FROM calendar_events
               WHERE user_is_social IS NOT NULL AND recurring_event_id IS NOT NULL
               ORDER BY id DESC"""
        )
        rows = [dict(r) for r in c.fetchall()]

    by_series: dict = {}
    for r in rows:
        by_series.setdefault(r["recurring_event_id"], []).append(r)

    examples = []
    for members in by_series.values():
        verdicts = {bool(m["user_is_social"]) for m in members}
        if len(verdicts) > 1:
            continue  # contradictory verdicts inside one series — exclude entirely
        newest = members[0]  # rows arrived id DESC, so index 0 is the most recent
        examples.append((newest["id"], {"title": newest["title"], "user_is_social": bool(newest["user_is_social"])}))

    examples.sort(key=lambda t: t[0], reverse=True)
    return [ex for _, ex in examples[:limit]]


# ── Targets & settings ────────────────────────────────────────────────────────

def seed_default_targets():
    from metrics import METRICS
    p = _p()
    with _cursor(write=True) as c:
        for key, meta in METRICS.items():
            c.execute(
                f"""INSERT INTO targets (metric, direction, value) VALUES ({p}, {p}, {p})
                    ON CONFLICT(metric) DO NOTHING""",
                (key, meta["direction"], meta["default_target"]),
            )


def get_targets():
    with _cursor() as c:
        c.execute("SELECT metric, direction, value FROM targets")
        return {r["metric"]: {"direction": r["direction"], "value": r["value"]} for r in c.fetchall()}


def set_target(metric, value):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE targets SET value = {p} WHERE metric = {p}", (value, metric))


def get_setting(key, default=None):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT value FROM app_settings WHERE key = {p}", (key,))
        row = c.fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO app_settings (key, value) VALUES ({p}, {p})
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


# ── Weekly reflections ────────────────────────────────────────────────────────

def get_reflection(week_start):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT text FROM weekly_reflections WHERE week_start = {p}", (week_start,))
        row = c.fetchone()
        return row["text"] if row else None


def save_reflection(week_start, text):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO weekly_reflections (week_start, text) VALUES ({p}, {p})
                ON CONFLICT(week_start) DO UPDATE SET text = excluded.text""",
            (week_start, text),
        )


# ── Rides (Uber / Lyft) — tracking-only, not a METRICS member ────────────────

def has_ride(gmail_message_id):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT 1 FROM rides WHERE gmail_message_id = {p}", (gmail_message_id,))
        return c.fetchone() is not None


def add_ride(gmail_message_id, service, ride_at, ride_key, subject, amount=None, is_cancellation=None):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO rides (gmail_message_id, service, ride_at, ride_key, subject, amount, is_cancellation)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}) ON CONFLICT(gmail_message_id) DO NOTHING""",
            (gmail_message_id, service, ride_at, ride_key, subject, amount, is_cancellation),
        )
        return c.rowcount > 0


def get_ride_by_message_id(gmail_message_id):
    """Existence check that also surfaces `is_cancellation` so the scan can
    backfill it on an already-ingested ride (see jobs/scan_gmail.py) without a
    second round trip."""
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT id, is_cancellation FROM rides WHERE gmail_message_id = {p}",
            (gmail_message_id,),
        )
        row = c.fetchone()
        if row is None:
            return None
        out = dict(row)
        if out["is_cancellation"] is not None:
            out["is_cancellation"] = bool(out["is_cancellation"])
        return out


def set_ride_cancellation(ride_id, is_cancellation):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE rides SET is_cancellation = {p} WHERE id = {p}", (is_cancellation, ride_id))


def find_ride_by_key(service, ride_key):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"SELECT id, amount, ride_at FROM rides WHERE service = {p} AND ride_key = {p}",
            (service, ride_key),
        )
        row = c.fetchone()
        return dict(row) if row else None


def set_ride_amount(ride_id, amount):
    """Updates amount only. `ride_at` is immutable after insert — get_rides_range
    buckets rides by substr(ride_at, 1, 10), so mutating it could silently move a
    ride into a different day/week when a follow-up email for the same trip lands
    on the next calendar day."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE rides SET amount = {p} WHERE id = {p}", (amount, ride_id))


def set_ride_classification(ride_id, is_work, confidence):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"UPDATE rides SET ai_is_work = {p}, ai_confidence = {p} WHERE id = {p}",
            (is_work, confidence, ride_id),
        )


def set_ride_work_override(ride_id, is_work):
    """Sets the confirmed user verdict. Returns True iff a row was actually updated,
    so callers (the API route) can turn "unknown id" into a real 404."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE rides SET user_is_work = {p} WHERE id = {p}", (is_work, ride_id))
        return c.rowcount > 0


def _ride_bool_rows(rows):
    """Cast the nullable ai_is_work / user_is_work / is_cancellation columns to
    real bool-or-None — SQLite returns 0/1 ints, which would otherwise leak
    into the API as non-bool JSON."""
    out = [dict(r) for r in rows]
    for r in out:
        for col in ("ai_is_work", "user_is_work", "is_cancellation"):
            if r[col] is not None:
                r[col] = bool(r[col])
    return out


def get_rides_range(start_day, end_day):
    """Buckets AND filters by "effective date" (night cutoff), not raw
    `date(ride_at)` — see metrics.effective_date and _effective_date_expr
    above. The bucketing timestamp is the TRUE ride time when known
    (`ride_key`'s parsed trip time), falling back to `ride_at` (email arrival)
    only when `ride_key` isn't in the ISO shape (see _ride_true_ts_expr).
    `ride_at` itself is never rewritten — the row still carries it unchanged
    (see set_ride_amount) — but the resolved true timestamp is also exposed
    as `ride_time` so callers can display/group by it without recomputing the
    CASE logic in Python. `user_date`, the ±1-day nudge, wins over the
    computed effective date; `auto_day` reports the cutoff-only day."""
    p = _p()
    ts_expr = _ride_true_ts_expr()
    eff_date_expr = _effective_date_expr(ts_expr)
    day_expr = f"COALESCE(user_date, {eff_date_expr})"
    with _cursor() as c:
        c.execute(
            f"""SELECT id, service, ride_at, ride_key, subject, amount, ai_is_work, ai_confidence,
                       user_is_work, is_cancellation, user_date, {ts_expr} AS ride_time,
                       {eff_date_expr} AS auto_day, {day_expr} AS day
                FROM rides
                WHERE {day_expr} >= {p} AND {day_expr} <= {p}
                ORDER BY {ts_expr}""",
            (start_day, end_day),
        )
        return _ride_bool_rows(c.fetchall())


def get_ride_auto_day(ride_id):
    """The ride's automatic (cutoff-only) day from its TRUE timestamp,
    ignoring user_date — the anchor for ±1 nudge validation. None = unknown id."""
    p = _p()
    eff = _effective_date_expr(_ride_true_ts_expr())
    with _cursor() as c:
        c.execute(f"SELECT {eff} AS auto_day FROM rides WHERE id = {p}", (ride_id,))
        row = c.fetchone()
        return row["auto_day"] if row else None


def set_ride_user_date(ride_id, user_date):
    """user_date=None clears the override. Returns False for an unknown id
    so the route can 404."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE rides SET user_date = {p} WHERE id = {p}", (user_date, ride_id))
        return c.rowcount > 0


def get_ride_examples(limit=10):
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT subject, user_is_work FROM rides
                WHERE user_is_work IS NOT NULL
                ORDER BY id DESC LIMIT {p}""",
            (limit,),
        )
        rows = [dict(r) for r in c.fetchall()]
        # Cast to a real bool — SQLite returns 0/1 ints, same as _ride_bool_rows above.
        for r in rows:
            r["user_is_work"] = bool(r["user_is_work"])
        return rows


# ── Sessions ──────────────────────────────────────────────────────────────────
# Server-side session store. The token is random (secrets.token_urlsafe) and
# unrelated to APP_PASSWORD — a leaked password no longer lets an attacker
# compute a valid cookie offline. created_at/expires_at are ISO-8601 UTC
# strings generated by app/auth.py, not DB-side defaults, so expiry math never
# depends on the DB server's clock.

def create_session(token, created_at, expires_at):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"INSERT INTO sessions (token, created_at, expires_at) VALUES ({p}, {p}, {p})",
            (token, created_at, expires_at),
        )


def get_session(token):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT token, created_at, expires_at FROM sessions WHERE token = {p}", (token,))
        row = c.fetchone()
        return dict(row) if row else None


def update_session_expiry(token, expires_at):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE sessions SET expires_at = {p} WHERE token = {p}", (expires_at, token))


def delete_session(token):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"DELETE FROM sessions WHERE token = {p}", (token,))


def delete_expired_sessions(now_iso):
    """Delete every session whose expires_at is at or before `now_iso` (an ISO-8601
    UTC string, same format as create_session's). Called at startup and safe to
    call anytime — single-user app, so this is a cheap full-table scan."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"DELETE FROM sessions WHERE expires_at <= {p}", (now_iso,))


def delete_all_sessions():
    """Revoke every session on every device. Single-user app, so this is the
    "my cookie may have leaked" button — there is no other remediation, since
    sessions are deliberately independent of APP_PASSWORD and rotating the
    password therefore does not invalidate an issued cookie."""
    with _cursor(write=True) as c:
        c.execute("DELETE FROM sessions")


# ── Bank accounts & transactions ──────────────────────────────────────────────
# The sync job may overwrite everything SimpleFIN reports, but never `role`
# (user-set) or `user_flow` (user override). Same Override + Learning pattern as
# social events and rides: AI/derived verdict and user verdict live in separate
# columns, and resolution happens in SQL so every caller agrees.

BANK_ROLES = ("spending", "bills", "savings", "investment", "credit_card", "unknown")
BANK_FLOWS = ("spending", "transfer", "card_payment", "investment", "income", "inflow_unknown",
              "refund")
# "refund" is a user-only verdict — bank_flows.classify_flow must never produce it
# (see spec §1 and the guard test in tests/test_bank_flows.py). It arrives only
# via user_flow, through the same override writers as every other flow.


def upsert_bank_account(simplefin_id, name, org="", kind=""):
    """Insert or refresh an account. Never touches `role` or `active` — those are
    the user's, and a nightly sync must not reset them."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO bank_accounts (simplefin_id, name, org, kind)
                VALUES ({p}, {p}, {p}, {p})
                ON CONFLICT(simplefin_id) DO UPDATE SET
                    name = excluded.name, org = excluded.org, kind = excluded.kind""",
            (simplefin_id, name, org, kind),
        )


def _bank_account_rows(rows):
    out = [dict(r) for r in rows]
    for r in out:
        r["active"] = bool(r["active"])
    return out


def get_bank_accounts():
    with _cursor() as c:
        c.execute("""SELECT id, simplefin_id, name, org, kind, role, active, last_synced_at,
                            nickname,
                            COALESCE(NULLIF(nickname, ''), name) AS display_name
                     FROM bank_accounts ORDER BY id""")
        return _bank_account_rows(c.fetchall())


def set_bank_account_role(simplefin_id, role):
    """Returns True iff a row was updated, so a route can turn an unknown id into a 404."""
    if role not in BANK_ROLES:
        raise ValueError(f"unknown role: {role}")
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_accounts SET role = {p} WHERE simplefin_id = {p}",
                  (role, simplefin_id))
        return c.rowcount > 0


def set_bank_account_nickname(simplefin_id, nickname):
    """Returns True iff a row was updated (unknown id -> 404 at the route).
    Empty/whitespace clears to NULL so the bank's own name shows again.
    A user column — the sync never reads or writes it."""
    nickname = (nickname or "").strip() or None
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_accounts SET nickname = {p} WHERE simplefin_id = {p}",
                  (nickname, simplefin_id))
        return c.rowcount > 0


def touch_bank_account_sync(simplefin_id, when_iso):
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_accounts SET last_synced_at = {p} WHERE simplefin_id = {p}",
                  (when_iso, simplefin_id))


def upsert_bank_transaction(simplefin_id, account_id, posted, transacted_at, amount,
                            description="", payee="", memo="", mcc=None):
    """Insert or refresh. Pending transactions settle — amount, description and
    posted date all legitimately change — so those are overwritten. `flow`,
    `user_flow`, `pair_id` and `ambiguous` are never touched here: the first is
    recomputed by the classification pass, the second belongs to the user."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""INSERT INTO bank_transactions
                    (simplefin_id, account_id, posted, transacted_at, amount,
                     description, payee, memo, mcc)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
                ON CONFLICT(simplefin_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    posted = excluded.posted,
                    transacted_at = excluded.transacted_at,
                    amount = excluded.amount,
                    description = excluded.description,
                    payee = excluded.payee,
                    memo = excluded.memo,
                    mcc = excluded.mcc""",
            (simplefin_id, account_id, posted, transacted_at, amount,
             description, payee, memo, mcc),
        )


def set_bank_transaction_derived(simplefin_id, flow, pair_id, ambiguous):
    """Write the derived columns for a single row. Deliberately does NOT touch
    user_flow. Production uses the bulk writer below (set_bank_transactions_derived_bulk)
    for its atomicity guarantee — this single-row form exists for tests and any
    future one-off correction."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(
            f"""UPDATE bank_transactions
                SET flow = {p}, pair_id = {p}, ambiguous = {p}
                WHERE simplefin_id = {p}""",
            (flow, pair_id, bool(ambiguous), simplefin_id),
        )


def set_bank_transactions_derived_bulk(items):
    """Write the derived columns for many transactions in ONE database transaction.

    `items` is an iterable of (simplefin_id, flow, pair_id, ambiguous) tuples —
    exactly bank_flows.classify_all()'s output shape. Writing the whole
    classification pass atomically matters because a matched pair's two halves
    are two separate rows: if a per-row write were interrupted partway through
    (a crash, a deploy), one half would keep its pair_id while the other went
    free, and the free half could then mis-pair with something else on the next
    sync — it does not self-heal. Committing all-or-nothing closes that hole.
    Deliberately does NOT touch user_flow."""
    p = _p()
    with _cursor(write=True) as c:
        for simplefin_id, flow, pair_id, ambiguous in items:
            c.execute(
                f"""UPDATE bank_transactions
                    SET flow = {p}, pair_id = {p}, ambiguous = {p}
                    WHERE simplefin_id = {p}""",
                (flow, pair_id, bool(ambiguous), simplefin_id),
            )


_NOTE_UNSET = object()  # sentinel: distinguishes "note not passed" from note=None/""


def set_bank_flow_override(simplefin_id, user_flow, note=_NOTE_UNSET):
    """The confirmed user verdict. Returns True iff a row was updated.

    Wired up by POST /api/bank/transactions/{id}/flow (triage), so
    COALESCE(user_flow, flow) can resolve to the user's value anywhere
    resolved_flow is read.

    `note` defaults to the `_NOTE_UNSET` sentinel: omit it entirely and the
    stored `user_note` is left untouched (so put-back — flow=None — keeps the
    note, since it explains the transaction, not the answer). Pass a string
    and it is trimmed, with an empty/whitespace-only result stored as NULL —
    so `note=""` is how a caller explicitly clears it. Flow and note are
    written in the same UPDATE, atomically."""
    if user_flow is not None and user_flow not in BANK_FLOWS:
        raise ValueError(f"unknown flow: {user_flow}")
    p = _p()
    with _cursor(write=True) as c:
        if note is _NOTE_UNSET:
            c.execute(f"UPDATE bank_transactions SET user_flow = {p} WHERE simplefin_id = {p}",
                      (user_flow, simplefin_id))
        else:
            note = note.strip() or None if note is not None else None
            c.execute(
                f"""UPDATE bank_transactions SET user_flow = {p}, user_note = {p}
                    WHERE simplefin_id = {p}""",
                (user_flow, note, simplefin_id),
            )
        return c.rowcount > 0


def set_bank_label(simplefin_id, label):
    """User label on one transaction. None clears. Always resets
    user_no_label — a label verdict and a no-label verdict are mutually
    exclusive answers to the same question. Returns True iff a row updated."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_transactions SET user_label = {p}, user_no_label = {p} "
                  f"WHERE simplefin_id = {p}",
                  (label, False, simplefin_id))
        return c.rowcount > 0


def set_bank_no_label(simplefin_id):
    """The user's durable rejection: this row gets no label. Clears any
    user_label (mutual exclusivity) and survives the sync's full suggestion
    recompute — bank_flows.label_suggestions returns None for flagged rows.
    Returns True iff a row updated."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_transactions SET user_no_label = {p}, user_label = NULL "
                  f"WHERE simplefin_id = {p}",
                  (True, simplefin_id))
        return c.rowcount > 0


def get_bank_label_vocabulary():
    """Distinct labels, most-used first then alphabetical (case-insensitive)
    tiebreak — autocomplete order. The vocabulary IS this query: a label with
    zero rows disappears."""
    with _cursor() as c:
        c.execute("""SELECT user_label AS label FROM bank_transactions
                     WHERE user_label IS NOT NULL
                     GROUP BY user_label ORDER BY COUNT(*) DESC, LOWER(user_label)""")
        return [r["label"] for r in c.fetchall()]


def set_bank_label_suggestions_bulk(mapping):
    """Write `suggested_label` for many rows in ONE transaction. DERIVED —
    only the sync's label pass calls this (jobs/sync_bank.py), with values
    that are the user's own labels propagated by bank_flows.label_suggestions;
    never touches user_label. None retires a stale suggestion. Unknown ids
    skipped. Rows whose suggested_label already matches the new value are left
    unwritten (avoids dead-tuple churn on Postgres from rewriting unchanged
    rows every sync). Returns rows actually CHANGED."""
    p = _p()
    diff = "IS DISTINCT FROM" if USE_POSTGRES else "IS NOT"
    updated = 0
    with _cursor(write=True) as c:
        for simplefin_id, label in mapping.items():
            c.execute(f"UPDATE bank_transactions SET suggested_label = {p} "
                      f"WHERE simplefin_id = {p} AND suggested_label {diff} {p}",
                      (label, simplefin_id, label))
            updated += c.rowcount
    return updated


_VENDOR_KEY_SQL = "CASE WHEN payee != '' THEN payee ELSE description END"


def set_bank_labels_by_vendor(vendor, label):
    """Bulk USER apply: label every un-user-labeled row of one vendor.
    The route's bulk mode calls this after the user taps the explicit
    'apply to N more' offer — the tap is the confirmation. Never touches
    rows that already carry a user_label. Returns rows updated."""
    p = _p()
    with _cursor(write=True) as c:
        c.execute(f"UPDATE bank_transactions SET user_label = {p} "
                  f"WHERE {_VENDOR_KEY_SQL} = {p} AND user_label IS NULL AND NOT user_no_label",
                  (label, vendor))
        return c.rowcount


def count_bank_unlabeled_by_vendor(vendor):
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT COUNT(*) AS n FROM bank_transactions "
                  f"WHERE {_VENDOR_KEY_SQL} = {p} AND user_label IS NULL AND NOT user_no_label",
                  (vendor,))
        return c.fetchone()["n"]


def get_bank_transaction_vendor(simplefin_id):
    """The vendor key of one row, or None if the id is unknown."""
    p = _p()
    with _cursor() as c:
        c.execute(f"SELECT {_VENDOR_KEY_SQL} AS vendor FROM bank_transactions "
                  f"WHERE simplefin_id = {p}", (simplefin_id,))
        row = c.fetchone()
        return row["vendor"] if row else None


def get_bank_triage(limit):
    """The triage worklist: rows the classifier flagged as ambiguous, plus
    unexplained deposits, each capped at `limit` and newest-`posted` first
    (tie-broken by `simplefin_id` so the order is deterministic).

    The ambiguous bucket filters on `user_flow IS NULL`, not just
    `ambiguous`. `ambiguous` is a derived column recomputed from scratch on
    every sync — the classifier reads `flow`, never `resolved_flow` — so a
    row the user already ruled on gets `ambiguous = true` again on the very
    next sync. Without this predicate a confirmed row would reappear in the
    queue forever (spec §6.4). Do not "simplify" it away.
    """
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""{_BANK_TXN_SELECT}
                WHERE t.ambiguous AND t.user_flow IS NULL
                ORDER BY t.posted DESC, t.simplefin_id ASC LIMIT {p}""",
            (limit,),
        )
        ambiguous = _bank_txn_rows(c.fetchall())

        c.execute(
            f"""{_BANK_TXN_SELECT}
                WHERE COALESCE(t.user_flow, t.flow) = {p}
                ORDER BY t.posted DESC, t.simplefin_id ASC LIMIT {p}""",
            ("inflow_unknown", limit),
        )
        inflow_unknown = _bank_txn_rows(c.fetchall())

    return {"ambiguous": ambiguous, "inflow_unknown": inflow_unknown}


def get_bank_recently_sorted(limit):
    """Rows the user has already ruled on, most recently posted first, capped."""
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""{_BANK_TXN_SELECT}
                WHERE t.user_flow IS NOT NULL
                ORDER BY t.posted DESC, t.simplefin_id ASC LIMIT {p}""",
            (limit,),
        )
        return _bank_txn_rows(c.fetchall())


def set_bank_flow_overrides_bulk(simplefin_ids, user_flow):
    """Bulk version of set_bank_flow_override for the triage screen's
    multi-select actions. Validates `user_flow` against BANK_FLOWS (None
    clears) BEFORE any write — an unknown flow raises ValueError and nothing
    is touched. All updates happen inside ONE _cursor(write=True) block, same
    atomicity rationale as set_bank_transactions_derived_bulk. Unknown ids
    are skipped, not an error. Returns the count of rows actually updated."""
    if user_flow is not None and user_flow not in BANK_FLOWS:
        raise ValueError(f"unknown flow: {user_flow}")
    p = _p()
    updated = 0
    with _cursor(write=True) as c:
        for simplefin_id in simplefin_ids:
            c.execute(f"UPDATE bank_transactions SET user_flow = {p} WHERE simplefin_id = {p}",
                      (user_flow, simplefin_id))
            updated += c.rowcount
    return updated


# BANK_SUGGESTION_FLOWS is the union of both triage queues' button flows — the
# outflow queue's spending/transfer/card_payment/investment plus the inflow
# queue's income/refund. "inflow_unknown" is deliberately excluded: it is a
# queue-membership state, never something the model should suggest as an
# answer. Kept separate from BANK_FLOWS (the override union, which does
# include inflow_unknown) so a bad model output can't slip through under a
# validator meant for a different write path.
BANK_SUGGESTION_FLOWS = ("spending", "transfer", "card_payment", "investment", "income", "refund")


def get_bank_flow_examples(limit=20):
    """Few-shot examples for ai_metrics.suggest_bank_flows: the most recently
    answered rows, newest first. Mirrors get_classification_examples /
    get_ride_examples.

    Returns EXACTLY {payee, description, side, user_flow} per row — this is a
    security contract, not a convenience shape: it is the only bank data
    allowed to reach the model, so no user_note, amount, date, or account
    field may ever be added here (see spec §1, §3). `side` is computed in SQL
    from the sign of `amount` and is not itself selected."""
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""SELECT payee, description,
                       CASE WHEN amount > 0 THEN 'inflow' ELSE 'outflow' END AS side,
                       user_flow
                FROM bank_transactions
                WHERE user_flow IS NOT NULL
                ORDER BY posted DESC, simplefin_id ASC LIMIT {p}""",
            (limit,),
        )
        return [dict(r) for r in c.fetchall()]


def get_bank_unsuggested_triage(limit):
    """Queue rows still awaiting a suggestion: the same two predicates as
    get_bank_triage's buckets (ambiguous-and-unanswered, or resolved to
    inflow_unknown-and-unanswered), further restricted to
    `suggested_flow IS NULL` so the sync never re-spends a call on a row it
    already suggested for. Newest-posted first, tie-broken by simplefin_id,
    capped — same ordering as get_bank_triage."""
    p = _p()
    with _cursor() as c:
        c.execute(
            f"""{_BANK_TXN_SELECT}
                WHERE ((t.ambiguous AND t.user_flow IS NULL)
                       OR (COALESCE(t.user_flow, t.flow) = 'inflow_unknown' AND t.user_flow IS NULL))
                  AND t.suggested_flow IS NULL
                ORDER BY t.posted DESC, t.simplefin_id ASC LIMIT {p}""",
            (limit,),
        )
        return _bank_txn_rows(c.fetchall())


def set_bank_suggestions_bulk(mapping):
    """Write `suggested_flow` for many rows in ONE database transaction —
    same atomicity rationale as set_bank_transactions_derived_bulk and
    set_bank_flow_overrides_bulk.

    This is a DERIVED write the sync owns exclusively (jobs/sync_bank.py,
    after ai_metrics.suggest_bank_flows returns) — never something a route
    handler calls on the user's behalf, and it never touches `user_flow`.
    Every value in `mapping` is validated against BANK_SUGGESTION_FLOWS
    BEFORE any write: a single bad entry raises ValueError and leaves the
    table untouched. This validation is what keeps a malformed or
    hallucinated model output out of the database — the model's raw response
    must never be trusted onto a row unchecked. Unknown simplefin_ids are
    skipped, not an error. Returns the count of rows actually updated."""
    for flow in mapping.values():
        if flow not in BANK_SUGGESTION_FLOWS:
            raise ValueError(f"unknown flow: {flow}")
    p = _p()
    updated = 0
    with _cursor(write=True) as c:
        for simplefin_id, flow in mapping.items():
            c.execute(f"UPDATE bank_transactions SET suggested_flow = {p} WHERE simplefin_id = {p}",
                      (flow, simplefin_id))
            updated += c.rowcount
    return updated


_LABEL_SUGGESTION_WHERE = ("t.suggested_label IS NOT NULL AND t.user_label IS NULL "
                           "AND NOT t.user_no_label")


def get_bank_label_suggestion_rows(limit):
    """The audit queue: rows carrying an unconfirmed suggestion — no user
    verdict either way — newest first."""
    p = _p()
    with _cursor() as c:
        c.execute(f"{_BANK_TXN_SELECT} WHERE {_LABEL_SUGGESTION_WHERE} "
                  f"ORDER BY t.posted DESC, t.simplefin_id DESC LIMIT {p}", (limit,))
        return _bank_txn_rows(c.fetchall())


def count_bank_label_suggestions():
    """Table-wide audit-queue size, uncapped — the 'N more' figure."""
    with _cursor() as c:
        c.execute("SELECT COUNT(*) AS n FROM bank_transactions t "
                  f"WHERE {_LABEL_SUGGESTION_WHERE}")
        return c.fetchone()["n"]


_BANK_TXN_SELECT = """
    SELECT t.id, t.simplefin_id, t.account_id, t.posted, t.transacted_at, t.amount,
           t.description, t.payee, t.memo, t.mcc, t.flow, t.user_flow, t.pair_id,
           t.ambiguous, t.user_note, t.suggested_flow, t.user_label, t.suggested_label,
           t.user_no_label,
           a.role AS account_role,
           COALESCE(NULLIF(a.nickname, ''), a.name) AS account_name,
           COALESCE(t.user_flow, t.flow) AS resolved_flow,
           CASE WHEN t.user_no_label THEN NULL
                ELSE COALESCE(t.user_label, t.suggested_label) END AS resolved_label
    FROM bank_transactions t JOIN bank_accounts a ON a.id = t.account_id
"""


def _bank_txn_rows(rows):
    """Cast `ambiguous` and `user_no_label` to real bools — SQLite returns 0/1 ints,
    which would otherwise leak into the API as non-bool JSON (same reason as _ride_bool_rows)."""
    out = [dict(r) for r in rows]
    for r in out:
        r["ambiguous"] = bool(r["ambiguous"])
        r["user_no_label"] = bool(r["user_no_label"])
    return out


def get_bank_transactions_range(start_day, end_day):
    p = _p()
    with _cursor() as c:
        c.execute(f"{_BANK_TXN_SELECT} WHERE t.posted >= {p} AND t.posted <= {p} "
                  f"ORDER BY t.posted, t.simplefin_id", (start_day, end_day))
        return _bank_txn_rows(c.fetchall())


def get_all_bank_transactions():
    """Every transaction, unfiltered by date — for the matcher and classifier.
    Returns already-paired rows too — the matcher needs them to know what is
    taken. The sync job reclassifies the whole table on every run (see
    jobs/sync_bank.py) rather than a sliding lookback window, so a row whose
    account role was unknown at ingest time gets corrected once the user
    assigns a role, no matter how long ago it posted."""
    with _cursor() as c:
        c.execute(f"{_BANK_TXN_SELECT} ORDER BY t.posted, t.simplefin_id")
        return _bank_txn_rows(c.fetchall())


def dump_table(table):
    """Return every row of `table` as a list of dicts. Used only by
    scripts/drop_v1_archive.py to export the v1 archive before dropping it.

    `table` is interpolated into the SQL because table names cannot be
    parameterized. It is validated against a hardcoded allowlist first —
    never pass a caller-supplied value here."""
    if table not in _DUMPABLE_TABLES:
        raise ValueError(f"refusing to dump unknown table: {table}")
    with _cursor() as c:
        c.execute(f"SELECT * FROM {table}")
        # dict(r), not dict(zip(columns, row)): psycopg2's RealDictCursor
        # already returns dict-like rows, and iterating one yields its keys
        # (not values) since it's a dict subclass — zip(columns, row) would
        # silently pair every column name with itself on Postgres. dict(r)
        # is the pattern used everywhere else in this file and works for
        # both sqlite3.Row and RealDictRow.
        return [dict(r) for r in c.fetchall()]


def drop_table(table):
    """Permanently drop `table`. Used only by scripts/drop_v1_archive.py.

    Same allowlist guard as dump_table — this is the only DROP in the entire
    codebase and it must stay reachable from exactly one one-off script."""
    if table not in _DUMPABLE_TABLES:
        raise ValueError(f"refusing to drop unknown table: {table}")
    with _cursor(write=True) as c:
        c.execute(f"DROP TABLE IF EXISTS {table}")
