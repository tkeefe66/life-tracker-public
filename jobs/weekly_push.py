"""Scheduled job: Monday-morning Telegram scorecard for the completed week (opt-in)."""
import datetime
import logging
from datetime import timedelta

import pytz

import database as db
import metrics
from app.scorecard import _local_today, scorecard_for_week
from config import TIMEZONE
from services.safe_status import safe_status
from services.telegram_notify import notify

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat()


def format_scorecard_text(card: dict) -> str:
    lines = [f"On Track — week of {card['week_start']}"]
    for key, m in card["metrics"].items():
        if metrics.METRICS.get(key, {}).get("private"):
            continue
        mark = "✅" if m["hit"] else "❌"
        sign = "≤" if m["direction"] == "ceiling" else "≥"
        lines.append(f"{mark} {m['label']}: {m['count']} (target {sign}{m['target']})")
    return "\n".join(lines)


def run():
    if db.get_setting("telegram_push", "off") != "on":
        logger.info("Weekly push skipped: toggle off")
        return
    try:
        last_monday = metrics.week_bounds(_local_today())[0] - timedelta(weeks=1)
        card = scorecard_for_week(last_monday)
        notify(format_scorecard_text(card))
        db.set_setting("push_last_run", _now_iso())
        db.set_setting("push_last_status", "ok")
    except Exception as e:
        # See jobs/scan_gmail.py's matching except block for why this is
        # safe_status(e), never f"error: {e}".
        logger.exception("Weekly push failed")
        db.set_setting("push_last_status", safe_status(e))
