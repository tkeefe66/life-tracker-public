"""Wipe all DB data. Prints the resolved target (engine, host, database name)
BEFORE prompting. Works on SQLite and Postgres — but on Postgres, a wrong-service
or wrong-environment run is fully destructive with no recovery, so the operator
must type the exact database host rather than a generic CONFIRM."""
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from config import DATABASE_PATH, DATABASE_URL

TABLES = [
    "checkins", "delivery_orders", "calendar_events", "rides", "weekly_reflections",
    "targets", "app_settings", "sessions",
    # bank_transactions references bank_accounts — delete the referencing table first.
    "bank_transactions", "bank_accounts",
    # v1 archive tables
    "life_log_entries", "life_log_people", "people", "activity_log", "categories",
    "habits", "habit_logs", "accomplishments", "weekly_focus", "later_items",
    "focus_summary_cache", "later_org_cache", "conversation_state",
]


def _target_description():
    """Returns (engine, description) for the ACTIVE target, read straight from
    config — never a default the operator might be assuming."""
    if db.USE_POSTGRES:
        parsed = urlparse(DATABASE_URL)
        host = parsed.hostname or "(unknown host)"
        database = (parsed.path or "").lstrip("/") or "(unknown database)"
        return "PostgreSQL", f"{host}/{database}"
    return "SQLite", DATABASE_PATH


def main():
    engine, target = _target_description()
    print(f"Target: {engine} — {target}")
    print("This deletes ALL data.")

    if db.USE_POSTGRES:
        host = urlparse(DATABASE_URL).hostname or ""
        answer = input(f"Type the database host ({host}) exactly to proceed: ")
        if answer.strip() != host:
            print("Aborted.")
            return
    else:
        answer = input("Type CONFIRM to proceed: ")
        if answer.strip() != "CONFIRM":
            print("Aborted.")
            return

    db.initialize_db()
    with db._cursor(write=True) as c:
        for table in TABLES:
            try:
                c.execute(f"DELETE FROM {table}")
            except Exception as e:
                print(f"  skip {table}: {e}")
    print("Done.")


if __name__ == "__main__":
    main()
