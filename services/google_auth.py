"""Shared Google OAuth2 credentials for Calendar + Gmail (refresh-token flow)."""
import logging

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import (
    GOOGLE_CALENDAR_CLIENT_ID,
    GOOGLE_CALENDAR_CLIENT_SECRET,
    GOOGLE_CALENDAR_REFRESH_TOKEN,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def is_configured() -> bool:
    return all([GOOGLE_CALENDAR_CLIENT_ID, GOOGLE_CALENDAR_CLIENT_SECRET, GOOGLE_CALENDAR_REFRESH_TOKEN])


def get_credentials() -> Credentials:
    if not is_configured():
        raise RuntimeError("Google credentials not configured. Set GOOGLE_CALENDAR_CLIENT_ID, "
                           "GOOGLE_CALENDAR_CLIENT_SECRET, and GOOGLE_CALENDAR_REFRESH_TOKEN.")
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_CALENDAR_REFRESH_TOKEN,
        token_uri=TOKEN_URI,
        client_id=GOOGLE_CALENDAR_CLIENT_ID,
        client_secret=GOOGLE_CALENDAR_CLIENT_SECRET,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def build_service(api: str, version: str):
    return build(api, version, credentials=get_credentials(), cache_discovery=False)
