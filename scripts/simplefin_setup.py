"""One-off: exchange a SimpleFIN setup token for a permanent access URL.

The setup token is SINGLE USE. Running this consumes it — if it fails after the
exchange succeeds, you cannot re-run it with the same token; generate a new one
from SimpleFIN.

The token is read from a hidden prompt (never a command-line argument, so it does
not land in shell history or the process list). The resulting access URL is
printed once, to your terminal only — it is never written to disk or logged.

Usage:
    python scripts/simplefin_setup.py

Then paste the printed URL into Railway as SIMPLEFIN_ACCESS_URL.
"""
import base64
import getpass
import sys

import httpx


def main() -> int:
    print(__doc__.split("Usage:")[0].strip())
    print()
    token = getpass.getpass("Paste your SimpleFIN setup token (input hidden): ").strip()
    if not token:
        print("No token entered. Nothing done.", file=sys.stderr)
        return 1

    try:
        claim_url = base64.b64decode(token, validate=True).decode("utf-8").strip()
    except Exception:
        print(
            "That doesn't look like a SimpleFIN setup token — it should be a long "
            "base64 string that decodes to a https://…/claim/… URL. Nothing was sent.",
            file=sys.stderr,
        )
        return 1

    if not claim_url.startswith("https://"):
        print("Decoded to something that isn't an https URL. Nothing was sent.", file=sys.stderr)
        return 1

    # Show only the host so the user can sanity-check where the token is going,
    # without echoing the credential-bearing path.
    host = claim_url.split("/")[2]
    print(f"\nClaiming against: {host}")
    print("This consumes the token. Continue? [y/N] ", end="")
    if input().strip().lower() != "y":
        print("Aborted. Token not used.")
        return 1

    try:
        resp = httpx.post(claim_url, timeout=30)
    except httpx.HTTPError:
        # Deliberately not printing the exception: httpx puts the full URL
        # (which is credential-bearing) into its messages.
        print("\nNetwork error contacting SimpleFIN. Token was probably NOT consumed; "
              "check your connection and try again.", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(f"\nSimpleFIN returned HTTP {resp.status_code}. If this is 403, the token "
              "was already used — generate a new one.", file=sys.stderr)
        return 1

    access_url = resp.text.strip()
    if not access_url.startswith("https://"):
        print("\nUnexpected response shape — expected an https access URL.", file=sys.stderr)
        return 1

    print("\n" + "=" * 70)
    print("SUCCESS. Your access URL is below.")
    print()
    print("  This URL contains your credentials. It is the read key to your bank")
    print("  data. Treat it exactly like a password:")
    print("    - paste it into Railway as SIMPLEFIN_ACCESS_URL (and your local .env)")
    print("    - do not commit it, paste it into chat, or save it to a file")
    print("    - clear your terminal afterwards (Cmd-K)")
    print("=" * 70 + "\n")
    print(access_url)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
