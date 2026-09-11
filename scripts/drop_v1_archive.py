"""One-off: export and then drop the unused v1 archive tables.

These tables are read by no v2 code path (see CLAUDE.md, "Archive tables").
They hold real personal data -- `people` and `life_log_entries` in particular
contain named individuals, relationship types, and freeform notes -- so they
are pure blast radius: they add nothing to the product and everything to what
a database compromise would expose.

DESTRUCTIVE AND IRREVERSIBLE. Exports every table to a timestamped JSON file
first and refuses to drop anything if the export fails.

    python scripts/drop_v1_archive.py --export-only          # safe, do this first
    python scripts/drop_v1_archive.py --export-and-drop      # destructive

The export lands OUTSIDE the repo (~/.on-track/v1-archive/) with mode 0600,
matching how scripts/simplefin_snapshot.py handles its archive -- it holds
the same data you are dropping, so it must not be committed or world-readable.
"""
import argparse
import datetime
import json
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database as db  # noqa: E402
from config import DATABASE_PATH, DATABASE_URL  # noqa: E402

V1_TABLES = [
    "life_log_entries",
    "people",
    "life_log_people",
    "activity_log",
    "habits",
    "habit_logs",
    "categories",
    "conversation_state",
    "accomplishments",
    "weekly_focus",
    "later_items",
]

EXPORT_DIR = os.path.expanduser("~/.on-track/v1-archive")


def _target_description():
    """Returns (engine, description) for the ACTIVE target, read straight from
    config -- never a default the operator might be assuming. Mirrors
    cleardb.py's helper of the same name: DATABASE_URL is environment-driven,
    so an operator whose shell points somewhere unexpected must see exactly
    where these DROPs are about to land before the confirmation prompt."""
    if db.USE_POSTGRES:
        parsed = urlparse(DATABASE_URL)
        host = parsed.hostname or "(unknown host)"
        database = (parsed.path or "").lstrip("/") or "(unknown database)"
        return "PostgreSQL", f"{host}/{database}"
    return "SQLite", DATABASE_PATH


def _export() -> str:
    os.makedirs(EXPORT_DIR, mode=0o700, exist_ok=True)
    # exist_ok=True skips the mode on an already-existing directory (mkdir's
    # mode arg is only applied on creation), so chmod explicitly every run —
    # same defensive pattern as scripts/simplefin_snapshot.py.
    os.chmod(EXPORT_DIR, 0o700)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = os.path.join(EXPORT_DIR, f"v1-archive-{stamp}.json")

    payload = {}
    for table in V1_TABLES:
        try:
            payload[table] = db.dump_table(table)
        except Exception as e:
            raise SystemExit(f"Export of {table} failed ({type(e).__name__}) — nothing dropped.")

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    total = sum(len(rows) for rows in payload.values())
    print(f"Exported {total} rows across {len(V1_TABLES)} tables to:\n  {path}")
    for table, rows in payload.items():
        print(f"  {table}: {len(rows)} rows")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export-only", action="store_true")
    group.add_argument("--export-and-drop", action="store_true")
    args = parser.parse_args()

    path = _export()

    if args.export_only:
        print("\nExport only — nothing dropped. Open that file and confirm it")
        print("looks complete before running with --export-and-drop.")
        return

    engine, target = _target_description()
    print(f"\nTarget: {engine} — {target}")
    print("About to PERMANENTLY DROP these tables from the live database above.")
    print("This cannot be undone except by restoring the export above.")
    confirm = input("Type DROP to proceed: ")
    if confirm != "DROP":
        print("Aborted — nothing dropped.")
        return

    for table in V1_TABLES:
        db.drop_table(table)
        print(f"Dropped {table}")
    print(f"\nDone. The export at {path} is now the only copy of that data.")


if __name__ == "__main__":
    main()
