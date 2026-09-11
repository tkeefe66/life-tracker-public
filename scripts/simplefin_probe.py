"""One-off: report what a SimpleFIN connection actually exposes, redacted.

Answers the questions we need before designing ingestion:
  - which accounts exist, and of what kind
  - how far back transactions go, and how many there are
  - what FIELDS a transaction actually carries (payee? memo? category?)
  - how identifiable transfers are (the difference between "here's your
    spending" and "saving money looks like being broke")

The output is deliberately safe to share: no account numbers, no balances, no
exact amounts, descriptions truncated. Amounts appear only as magnitude buckets.

Usage:
    SIMPLEFIN_ACCESS_URL='https://...' python scripts/simplefin_probe.py [DAYS]

DAYS defaults to 90. The access URL is read from the environment so it never
appears in your shell history or the process list.
"""
import os
import re
import sys
import time
from collections import Counter

import httpx

BUCKETS = [(10, "<$10"), (50, "$10-50"), (100, "$50-100"), (500, "$100-500"),
           (2000, "$500-2k"), (float("inf"), ">$2k")]

TRANSFER_HINTS = re.compile(
    r"(transfer|xfer|payment thank you|autopay|ach|deposit|withdrawal|"
    r"to savings|from savings|brokerage|invest|contribution|robinhood|"
    r"fidelity|vanguard|schwab|coinbase|wealthfront|betterment)",
    re.IGNORECASE,
)


def bucket(amount: float) -> str:
    a = abs(amount)
    for ceiling, label in BUCKETS:
        if a < ceiling:
            return label
    return ">$2k"


def redact(text: str, limit: int = 44) -> str:
    """Truncate and strip long digit runs that could be account/card numbers."""
    cleaned = re.sub(r"\d{6,}", "<digits>", (text or "").strip())
    return cleaned[:limit]


def main() -> int:
    access_url = os.environ.get("SIMPLEFIN_ACCESS_URL", "").strip()
    if not access_url:
        print("Set SIMPLEFIN_ACCESS_URL first. See the docstring.", file=sys.stderr)
        return 1

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    start = int(time.time()) - days * 86400

    try:
        resp = httpx.get(f"{access_url.rstrip('/')}/accounts",
                         params={"start-date": start}, timeout=90)
    except httpx.HTTPError:
        # Never print the exception — it embeds the credential-bearing URL.
        print("Network error contacting SimpleFIN.", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(f"SimpleFIN returned HTTP {resp.status_code}.", file=sys.stderr)
        return 1

    data = resp.json()
    accounts = data.get("accounts", [])

    print(f"\n{'=' * 68}\nSIMPLEFIN PROBE — last {days} days\n{'=' * 68}")
    for err in data.get("errors", []) or []:
        print(f"  ! reported error: {redact(str(err), 100)}")

    print(f"\nACCOUNTS ({len(accounts)})\n")
    all_txns = []
    for acct in accounts:
        org = (acct.get("org") or {}).get("name") or (acct.get("org") or {}).get("domain") or "?"
        txns = acct.get("transactions", []) or []
        all_txns.extend((acct, t) for t in txns)
        dates = sorted(int(t.get("posted", 0)) for t in txns if t.get("posted"))
        span = ""
        if dates:
            fmt = "%Y-%m-%d"
            span = (f"  {time.strftime(fmt, time.localtime(dates[0]))} → "
                    f"{time.strftime(fmt, time.localtime(dates[-1]))}")
        has_balance = "balance" in acct
        print(f"  • {redact(acct.get('name', '?'), 34):36} org={redact(org, 20):22} "
              f"txns={len(txns):<5} balance={'yes' if has_balance else 'no'}")
        if span:
            print(f"    {span.strip()}")

    print("\nTRANSACTION FIELDS PRESENT\n")
    field_counts = Counter()
    for _, t in all_txns:
        for k in t.keys():
            field_counts[k] += 1
    total = len(all_txns) or 1
    for field, n in field_counts.most_common():
        print(f"  {field:20} present on {n}/{total} ({100 * n // total}%)")

    inflow = sum(1 for _, t in all_txns if float(t.get("amount", 0)) > 0)
    print(f"\nDIRECTION\n  inflow (positive): {inflow}\n  outflow (negative): {len(all_txns) - inflow}")

    print("\nPOSSIBLE TRANSFERS / NON-SPENDING "
          "(keyword match — this is the key question)\n")
    hits = [(a, t) for a, t in all_txns
            if TRANSFER_HINTS.search(f"{t.get('description', '')} {t.get('payee', '')}")]
    print(f"  {len(hits)} of {len(all_txns)} transactions match transfer-ish wording")
    for a, t in hits[:12]:
        print(f"    [{bucket(float(t.get('amount', 0))):9}] "
              f"{redact(t.get('payee') or t.get('description', ''))}")

    print("\nSAMPLE DESCRIPTIONS (redacted, newest first)\n")
    for a, t in sorted(all_txns, key=lambda x: -int(x[1].get("posted", 0)))[:25]:
        amt = float(t.get("amount", 0))
        sign = "+" if amt > 0 else "-"
        print(f"  {sign}[{bucket(amt):9}] {redact(t.get('payee') or t.get('description', '')):46}"
              f" | {redact(acct_name(a), 18)}")

    print(f"\n{'=' * 68}\nSafe to share: no account numbers, balances, or exact amounts.\n")
    return 0


def acct_name(acct: dict) -> str:
    return acct.get("name", "?")


if __name__ == "__main__":
    raise SystemExit(main())
