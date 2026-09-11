"""One-off: replay a saved SimpleFIN snapshot through the normal ingest path.

SimpleFIN keeps a rolling 90 days, so snapshots taken by
scripts/simplefin_snapshot.py are the only copy of anything older than the live
window. This feeds them to jobs.sync_bank.run(payload=...) — the same code path
a live sync uses, so backfilled rows are indistinguishable from synced ones and
the ingest logic can never drift between the two.

Safe to re-run: every upsert keys on simplefin_id, and classification is
recomputed deterministically.

Usage:
    python scripts/simplefin_backfill.py                      # newest snapshot
    python scripts/simplefin_backfill.py --all                # every snapshot, oldest first
    python scripts/simplefin_backfill.py path/to/snapshot.json
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SNAPSHOT_DIR = Path.home() / ".on-track" / "simplefin-snapshots"


def _load(path: Path) -> dict:
    envelope = json.loads(path.read_text())
    # Snapshots are wrapped in a capture envelope; older ad-hoc dumps may not be.
    return envelope.get("payload", envelope)


# Personal account hints are local configuration, never published source.
ROLE_SEEDS_PATH = Path.home() / ".on-track" / "role-seeds.json"
VALID_ROLES = {"spending", "bills", "savings", "credit_card", "investment"}


def load_role_seeds(path: Path) -> list[tuple[str, str, str]]:
    """Read optional private configuration without exposing values in errors."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError("Cannot read role seeds; check file access and JSON syntax.") from None
    if not isinstance(data, list):
        raise ValueError("Role seeds must be a JSON list of [institution, account hint, role] entries.")
    result = []
    for entry in data:
        if (not isinstance(entry, list) or len(entry) != 3
                or not all(isinstance(value, str) for value in entry)
                or not entry[0].strip() or entry[2] not in VALID_ROLES):
            raise ValueError("Invalid role seed; check institution, account hint, and supported role.")
        result.append((entry[0].strip().lower(), entry[1].strip().lower(), entry[2]))
    return result


def seed_roles(db, seeds) -> int:
    """Apply private seeds to accounts still marked `unknown`. Returns how many changed."""
    changed = 0
    for acct in db.get_bank_accounts():
        if acct["role"] != "unknown":
            continue
        haystack = f"{acct['org']} {acct['name']}".lower()
        for org_hint, id_hint, role in seeds:
            if org_hint in haystack and (not id_hint or id_hint in haystack):
                db.set_bank_account_role(acct["simplefin_id"], role)
                print(f"  role: {acct['name'][:34]:36} -> {role}")
                changed += 1
                break
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", nargs="?", type=Path)
    parser.add_argument("--all", action="store_true", help="Replay every saved snapshot")
    parser.add_argument("--role-seeds", type=Path, help="Optional private JSON account-role seeds")
    args = parser.parse_args()
    if args.all and args.snapshot:
        parser.error("Choose either a snapshot path or --all.")
    seed_path = args.role_seeds.expanduser() if args.role_seeds else ROLE_SEEDS_PATH
    if args.role_seeds and not seed_path.is_file():
        parser.error("Role seed file does not exist; check --role-seeds.")
    try:
        seeds = load_role_seeds(seed_path)
    except ValueError as exc:
        parser.error(str(exc))
    if args.all:
        paths = sorted(SNAPSHOT_DIR.glob("simplefin-*.json"))
    elif args.snapshot:
        paths = [args.snapshot.expanduser()]
    else:
        paths = sorted(SNAPSHOT_DIR.glob("simplefin-*.json"))[-1:]

    if not paths:
        print(f"No snapshots found in {SNAPSHOT_DIR}.", file=sys.stderr)
        print("Run scripts/simplefin_snapshot.py first.", file=sys.stderr)
        return 1

    import database as db
    from jobs.sync_bank import run as sync_bank

    db.initialize_db()

    for path in paths:
        if not path.exists():
            print(f"Missing: {path}", file=sys.stderr)
            return 1
        print(f"Replaying {path.name} …")
        sync_bank(payload=_load(path))
        print(f"  {db.get_setting('bank_last_result')}")

    seeded = seed_roles(db, seeds)
    if seeded:
        # Roles drive pair matching, so the first pass classified against
        # `unknown` everywhere. Re-run to let card payments and transfers resolve.
        print(f"\nSeeded {seeded} account role(s); re-classifying …")
        for path in paths:
            sync_bank(payload=_load(path))
        print(f"  {db.get_setting('bank_last_result')}")

    unknown = [a for a in db.get_bank_accounts() if a["role"] == "unknown"]
    if unknown:
        print(f"\n{len(unknown)} account(s) still need a role — classification "
              f"treats them as unknown, so their transfers will not pair correctly:")
        for a in unknown:
            print(f"  {a['simplefin_id']:24} {a['name'][:32]:34} ({a['org']})")
        print("\nSet each with: POST /api/bank/accounts/<simplefin_id>/role "
              '{"role": "spending|bills|savings|investment|credit_card"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
