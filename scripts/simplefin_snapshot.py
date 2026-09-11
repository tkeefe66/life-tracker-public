"""Archive the raw SimpleFIN payload to disk, before the sync job exists.

SimpleFIN keeps a rolling 90-day window. Anything older is gone for good, so
every week without a capture is a week of history permanently lost. This script
preserves the *raw, unmodified* JSON so that when `jobs/sync_bank.py` lands it
can be replayed as a backfill — schema decisions made later still get today's
data.

Deliberately does no parsing, classification, or filtering: the whole point is
to keep everything, including fields the current design doesn't yet use.

Unlike `simplefin_probe.py`, the output is NOT redacted and is NOT safe to
share. It is written outside the repository, mode 0600, so it cannot be
committed by accident.

Usage:
    SIMPLEFIN_ACCESS_URL='https://...' python scripts/simplefin_snapshot.py [DAYS]
    SIMPLEFIN_ACCESS_URL='https://...' python scripts/simplefin_snapshot.py --out DIR

DAYS defaults to 90 (SimpleFIN's maximum). The access URL is read from the
environment so it never appears in shell history or the process list, and no
exception carrying it is ever printed — see the redaction boundary in CLAUDE.md.
"""
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import httpx

DEFAULT_OUT = Path.home() / ".on-track" / "simplefin-snapshots"


def parse_args(argv: list[str]) -> tuple[int, Path]:
    days, out = 90, DEFAULT_OUT
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--out":
            if i + 1 >= len(argv):
                raise ValueError("--out needs a directory")
            out = Path(argv[i + 1]).expanduser()
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    if rest:
        days = int(rest[0])
        if days < 1:
            raise ValueError("DAYS must be positive")
    return days, out


def main() -> int:
    access_url = os.environ.get("SIMPLEFIN_ACCESS_URL", "").strip()
    if not access_url:
        print("Set SIMPLEFIN_ACCESS_URL first. See the docstring.", file=sys.stderr)
        return 1

    try:
        days, out_dir = parse_args(sys.argv[1:])
    except ValueError as e:
        print(f"Bad arguments: {e}", file=sys.stderr)
        return 1

    start = int(time.time()) - days * 86400

    try:
        resp = httpx.get(f"{access_url.rstrip('/')}/accounts",
                         params={"start-date": start}, timeout=180)
    except httpx.HTTPError:
        # Never print or re-raise: the exception embeds the credential-bearing URL.
        print("Network error contacting SimpleFIN.", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(f"SimpleFIN returned HTTP {resp.status_code}.", file=sys.stderr)
        return 1

    try:
        data = resp.json()
    except ValueError:
        print("SimpleFIN returned a non-JSON body.", file=sys.stderr)
        return 1

    accounts = data.get("accounts", []) or []
    txn_count = sum(len(a.get("transactions", []) or []) for a in accounts)
    if not accounts:
        print("Refusing to write: SimpleFIN returned no accounts.", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    path = out_dir / f"simplefin-{date.today().isoformat()}.json"

    envelope = {
        "captured_at": int(time.time()),
        "captured_date": date.today().isoformat(),
        "requested_days": days,
        "start_date": start,
        "payload": data,
    }

    # Open with 0600 from the outset — never a window where it is world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(envelope, f)

    for err in data.get("errors", []) or []:
        print("  ! SimpleFIN reported an error for one account (see the file)")
        break

    print(f"Wrote {path}")
    print(f"  {len(accounts)} accounts, {txn_count} transactions, last {days} days")
    print("  mode 0600, outside the repo — raw and unredacted, do not share")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
