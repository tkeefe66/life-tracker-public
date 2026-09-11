"""Scheduled job: pull recent calendar events and classify social/not-social (daily)."""
import datetime
import logging

import pytz

import ai_metrics
import database as db
import metrics
from config import TIMEZONE
from services import calendar_service, google_auth
from services.safe_status import GOOGLE_NOT_CONFIGURED, safe_status

logger = logging.getLogger(__name__)

DAYS_BACK = 3  # overlap window so edited/late events get picked up


def _now_iso() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat()


def run():
    if not google_auth.is_configured():
        logger.warning("Calendar scan skipped: Google not configured")
        db.set_setting("calendar_last_status", GOOGLE_NOT_CONFIGURED)
        return
    try:
        events = calendar_service.get_events_range(days_back=DAYS_BACK)
        examples = db.get_classification_examples()
        classified = 0
        for ev in events:
            db.upsert_calendar_event(
                ev["event_id"], ev["title"], ev["start_datetime"], ev["end_datetime"],
                recurring_event_id=ev.get("recurring_event_id"),
                location=ev.get("location") or None,
                is_date=metrics.title_is_date(ev["title"]),
            )
            if db.event_needs_classification(ev["event_id"]):
                result = ai_metrics.classify_social_event(
                    ev["title"], ev["description"], ev["location"], ev["attendees"], examples=examples
                )
                db.set_event_classification(ev["event_id"], result["is_social"], result["confidence"])
                classified += 1
        db.set_setting("calendar_last_run", _now_iso())
        db.set_setting("calendar_last_status", "ok")
        logger.info("Calendar scan: %d events, %d newly classified", len(events), classified)
    except Exception as e:
        # See jobs/scan_gmail.py's matching except block for why this is
        # safe_status(e), never f"error: {e}".
        logger.exception("Calendar scan failed")
        db.set_setting("calendar_last_run", _now_iso())
        db.set_setting("calendar_last_status", safe_status(e))
