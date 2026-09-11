"""
One-time OAuth2 setup script — run locally to generate a refresh token.

Usage:
    python scripts/calendar_auth.py

Prerequisites:
    1. Go to console.cloud.google.com → your project → APIs & Services → Credentials
    2. Create an OAuth 2.0 Client ID (type: Desktop app)
    3. Download the JSON → save as client_secret.json in the project root
    4. Enable the Google Calendar API AND the Gmail API in the project. Re-run this
       script after the scope change and update GOOGLE_CALENDAR_REFRESH_TOKEN in
       Railway — the old token lacks the Gmail scope.

After running, copy the printed GOOGLE_CALENDAR_REFRESH_TOKEN value into Railway env vars.
"""

import json
import os
import sys

# Allow running from project root or scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_auth_oauthlib.flow import InstalledAppFlow

from services.google_auth import SCOPES  # calendar.readonly + gmail.readonly

CLIENT_SECRET_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "client_secret.json")


def main():
    if not os.path.exists(CLIENT_SECRET_FILE):
        print(f"ERROR: {CLIENT_SECRET_FILE} not found.")
        print("Download your OAuth 2.0 client secret JSON from Google Cloud Console")
        print("and save it as client_secret.json in the project root.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    with open(CLIENT_SECRET_FILE) as f:
        client_info = json.load(f)
    client_data = client_info.get("installed") or client_info.get("web", {})

    print("\n" + "=" * 60)
    print("SUCCESS — add these to Railway environment variables:")
    print("=" * 60)
    print(f"GOOGLE_CALENDAR_CLIENT_ID     = {client_data.get('client_id', '')}")
    print(f"GOOGLE_CALENDAR_CLIENT_SECRET = {client_data.get('client_secret', '')}")
    print(f"GOOGLE_CALENDAR_REFRESH_TOKEN = {creds.refresh_token}")
    print("GOOGLE_CALENDAR_ID            = primary")
    print("=" * 60)


if __name__ == "__main__":
    main()
