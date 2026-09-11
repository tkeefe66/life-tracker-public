"""Google Calendar service — fetches events using the shared OAuth2 credentials."""
import datetime
import logging

import pytz

from config import GOOGLE_CALENDAR_ID, TIMEZONE
from services import google_auth

logger = logging.getLogger(__name__)


def _get_service():
    return google_auth.build_service("calendar", "v3")


def is_configured() -> bool:
    return google_auth.is_configured()


def _fetch_items(time_min: str, time_max: str) -> list:
    service = _get_service()
    items = []
    page_token = None
    while True:
        result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
        ).execute()
        items.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return items


def _self_declined(item: dict) -> bool:
    for a in item.get("attendees", []) or []:
        if a.get("self") and a.get("responseStatus") == "declined":
            return True
    return False


def get_events_range(days_back: int) -> list:
    """Events in the window (now - days_back) → now.

    Excludes cancelled, declined, all-day, and birthday events.
    Returns dicts: event_id, title, start_datetime, end_datetime, description, location, attendees.
    """
    tz = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(tz)
    time_min = (now - datetime.timedelta(days=days_back)).isoformat()
    time_max = now.isoformat()

    events = []
    for item in _fetch_items(time_min, time_max):
        if item.get("status") == "cancelled":
            continue
        if item.get("eventType") == "birthday":
            continue
        title_lower = (item.get("summary") or "").lower()
        if any(kw in title_lower for kw in ("birthday", "bday", "holiday")):
            continue
        if _self_declined(item):
            continue
        start = item.get("start", {})
        end = item.get("end", {})
        if not start.get("dateTime") or not end.get("dateTime"):
            continue  # all-day events excluded — no end time to test "occurred"

        attendees = [
            (a.get("displayName") or a.get("email", "").split("@")[0])
            for a in (item.get("attendees", []) or [])
            if not a.get("self")
        ]
        events.append({
            "event_id": item["id"],
            "title": item.get("summary", "(No title)"),
            "start_datetime": start["dateTime"],
            "end_datetime": end["dateTime"],
            "description": item.get("description", ""),
            "location": item.get("location", ""),
            "attendees": attendees,
            # Present only on an occurrence of a recurring event; None for a
            # one-off. Series membership drives get_classification_examples'
            # generalization rule (database.py) — see the granularity spec.
            "recurring_event_id": item.get("recurringEventId"),
        })
    logger.info("Fetched %d calendar events (%d-day past window)", len(events), days_back)
    return events
