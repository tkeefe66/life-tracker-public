"""One-off: verify the most recent database backup is readable.

Read-only. Downloads the newest dump from the backup bucket, decrypts it if
BACKUP_ENCRYPTION_KEY is set, and runs `pg_restore --list` to prove the file
is a structurally valid custom-format dump whose table-of-contents parses.
Never writes to any database.

    python scripts/verify_backup.py

Run it after changing anything about the backup path, and periodically
otherwise -- an untested backup is not a backup.

WHAT THIS DOES NOT PROVE: that a restore into a live database succeeds. It
proves the file exists, decrypts, and is a well-formed dump. That is the
large majority of what goes wrong, and it is provable without risk.

── FULL RESTORE RUNBOOK (destructive — a human runs this, deliberately) ──

Deliberately not scripted: a restore script that exists is one that can be
run by accident, and this restores over live financial data.

WHERE THE VARS LIVE: BACKUP_S3_*, BACKUP_ENCRYPTION_KEY, and the PG*
connection vars are all Railway service variables on the "web" service --
that's where to look if this runbook is being run from a machine that
doesn't already have them exported.

IF BACKUP_ENCRYPTION_KEY IS EVER LOST, EVERY ENCRYPTED BACKUP BECOMES
PERMANENTLY UNREADABLE. Fernet has no recovery mechanism and no back door.
The key must therefore be stored somewhere OTHER than Railway (e.g. a
password manager) -- otherwise a wiped or compromised Railway project takes
every encrypted backup down with it, at the exact moment backups matter most.

1. Verify first:
       python scripts/verify_backup.py

2. Download and decrypt the dump you want. This script (and its --keep flag)
   only ever fetches the NEWEST key under on-track-backups/ -- there is no
   flag to reach an older one. If "the newest backup is the bad one" is why
   you're reading this runbook, list and fetch an older key manually first.
   Run this from the REPO ROOT -- the sys.path.insert(0, '.') below is what
   lets `from config import ...` resolve, the same way this script itself
   does at import time (see the sys.path.insert near the top of this file):

       python -c "
import sys; sys.path.insert(0, '.')
from config import BACKUP_S3_ENDPOINT, BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY, BACKUP_S3_BUCKET
import boto3
c = boto3.client('s3', endpoint_url=BACKUP_S3_ENDPOINT,
                  aws_access_key_id=BACKUP_S3_ACCESS_KEY,
                  aws_secret_access_key=BACKUP_S3_SECRET_KEY)
for o in c.list_objects_v2(Bucket=BACKUP_S3_BUCKET,
                            Prefix='on-track-backups/')['Contents']:
    print(o['Key'])
"
       # then, with the chosen key:
       #   c.download_file(BACKUP_S3_BUCKET, '<chosen-key>', '/tmp/restore.dump[.enc]')
       #   if the key ends in .enc, decrypt it the same way _decrypt() does --
       #   Fernet(BACKUP_ENCRYPTION_KEY.encode()).decrypt(token) -- before step 3.

   Otherwise, for the newest backup, this script does the download+decrypt:
       python scripts/verify_backup.py --keep /tmp/restore.dump

3. Restore into a SCRATCH database first and confirm it looks right. Never
   restore straight into production:
       createdb ontrack_restore_test
       pg_restore -d ontrack_restore_test --no-owner /tmp/restore.dump
       psql ontrack_restore_test -c "SELECT count(*) FROM bank_transactions;"

4. Only if step 3 looks correct, restore into production. Take a fresh dump
   of the current state first -- you are about to overwrite it:
       pg_dump --format=custom -h $PGHOST -U $PGUSER -d $PGDATABASE \\
           > /tmp/pre-restore-safety.dump
       pg_restore -d "$DATABASE_URL" --clean --if-exists --no-owner /tmp/restore.dump

5. Delete /tmp/restore.dump, /tmp/pre-restore-safety.dump, and the scratch
   database when done -- ALL THREE hold the full unencrypted contents of
   the database. The safety dump from step 4 is the easiest of the three to
   forget, since nothing after step 4 points back at it.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import BACKUP_ENCRYPTION_KEY, BACKUP_S3_BUCKET  # noqa: E402
from jobs.backup_db import BACKUP_PREFIX, _is_encryption_configured, _s3_client  # noqa: E402

# Matches exactly what jobs/backup_db.py writes: on-track-backups/<timestamp>.dump[.enc].
# Anything else under the prefix (a stray file someone drops in the bucket, a
# README, etc.) must never be mistaken for a backup just because its name
# happens to sort lexically last.
_BACKUP_KEY_RE = re.compile(r"^" + re.escape(BACKUP_PREFIX) + r"\d{8}T\d{6}\.dump(\.enc)?$")


def _newest_backup_key() -> str:
    client = _s3_client()
    # Unpaginated: fine today because RETENTION=30 (jobs/backup_db.py) keeps
    # this prefix far under list_objects_v2's 1000-object page limit. If
    # retention ever grows, or something else starts writing into this
    # prefix, this would need pagination to see everything.
    resp = client.list_objects_v2(Bucket=BACKUP_S3_BUCKET, Prefix=BACKUP_PREFIX)
    contents = resp.get("Contents", [])
    if not contents:
        raise SystemExit(f"No backups found under {BACKUP_PREFIX} — nothing to verify.")
    candidates = [obj["Key"] for obj in contents if _BACKUP_KEY_RE.match(obj["Key"])]
    if not candidates:
        raise SystemExit(
            f"Found {len(contents)} object(s) under {BACKUP_PREFIX}, but none match "
            f"the expected backup filename shape (YYYYmmddTHHMMSS.dump[.enc]) — "
            f"nothing to verify. Check for stray files in the bucket."
        )
    # Keys are timestamp-prefixed (YYYYmmddTHHMMSS), so lexical max is newest.
    return max(candidates)


def _download(key: str, path: str) -> None:
    _s3_client().download_file(BACKUP_S3_BUCKET, key, path)


def _decrypt(src_path: str, dst_path: str) -> None:
    from cryptography.fernet import Fernet, InvalidToken

    with open(src_path, "rb") as f:
        token = f.read()
    try:
        plaintext = Fernet(BACKUP_ENCRYPTION_KEY.encode()).decrypt(token)
    except InvalidToken:
        raise SystemExit(
            "Could not decrypt the backup: either BACKUP_ENCRYPTION_KEY is wrong, or "
            "the downloaded file is corrupted. Re-run to rule out a bad transfer "
            "before concluding the key itself is wrong."
        )
    with open(dst_path, "wb") as f:
        f.write(plaintext)


def _pg_restore_list(path: str) -> str:
    try:
        result = subprocess.run(
            ["pg_restore", "--list", path], capture_output=True, text=True
        )
    except FileNotFoundError:
        raise SystemExit(
            "pg_restore not found on PATH — install the PostgreSQL client tools "
            "(e.g. brew install libpq, or postgresql-client-18 as nixpacks.toml does)."
        )
    if result.returncode != 0:
        raise SystemExit(
            f"pg_restore could not read the dump (exit {result.returncode}).\n"
            f"{result.stderr[:2000]}"
        )
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        metavar="PATH",
        help="also write the decrypted dump here (for a deliberate restore)",
    )
    args = parser.parse_args()

    if not BACKUP_S3_BUCKET:
        raise SystemExit("BACKUP_S3_* not configured — nothing to verify.")

    key = _newest_backup_key()
    print(f"Newest backup: {key}")

    workdir = tempfile.mkdtemp(prefix="verify-backup-")
    downloaded = os.path.join(workdir, "downloaded")
    dump_path = downloaded

    try:
        _download(key, downloaded)
        print(f"Downloaded {os.path.getsize(downloaded)} bytes")

        if key.endswith(".enc"):
            if not _is_encryption_configured():
                raise SystemExit(
                    "Backup is encrypted but BACKUP_ENCRYPTION_KEY is not set. "
                    "Without the key this backup cannot be read — set it and retry."
                )
            dump_path = os.path.join(workdir, "decrypted.dump")
            _decrypt(downloaded, dump_path)
            print(f"Decrypted OK ({os.path.getsize(dump_path)} bytes)")
        elif _is_encryption_configured():
            print(
                "NOTE: newest backup is unencrypted but a key is configured — "
                "this dump predates encryption being enabled."
            )

        toc = _pg_restore_list(dump_path)
        tables = [line for line in toc.splitlines() if " TABLE DATA " in line]
        print(f"pg_restore read the dump: {len(tables)} tables with data")
        if not tables:
            raise SystemExit(
                "Dump parsed but contains NO table data — this backup is useless. "
                "Investigate before trusting it."
            )

        if args.keep:
            with open(dump_path, "rb") as src, open(args.keep, "wb") as dst:
                dst.write(src.read())
            print(f"Decrypted dump written to {args.keep}")
            print("It holds the full unencrypted database — delete it when done.")

        print("\nVERIFIED: the newest backup exists, decrypts, and is readable.")
    finally:
        for name in os.listdir(workdir):
            os.unlink(os.path.join(workdir, name))
        os.rmdir(workdir)


if __name__ == "__main__":
    main()
