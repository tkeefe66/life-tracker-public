"""Scheduled job: scan Gmail for food-delivery receipts (every GMAIL_SCAN_INTERVAL_HOURS)."""
import datetime
import logging

import pytz

import ai_metrics
import database as db
import receipts
from config import TIMEZONE
from services import google_auth
from services.gmail_service import fetch_delivery_candidates
from services.safe_status import GOOGLE_NOT_CONFIGURED, safe_status

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat()


def run():
    if not google_auth.is_configured():
        logger.warning("Gmail scan skipped: Google not configured")
        db.set_setting("gmail_last_status", GOOGLE_NOT_CONFIGURED)
        return
    try:
        candidates = fetch_delivery_candidates()
        ride_examples = db.get_ride_examples()
        added = ai_checked = rides_added = 0
        for cand in candidates:
            if db.has_delivery_order(cand["gmail_message_id"]):
                continue
            snippet = cand.get("snippet", "")

            existing_ride = db.get_ride_by_message_id(cand["gmail_message_id"])
            if existing_ride:
                # Backfill path: self-heals rides ingested before is_cancellation
                # existed (or any other row that somehow never got classified) —
                # the 7-day lookback window still covers recently-seen rides.
                if existing_ride["is_cancellation"] is None:
                    db.set_ride_cancellation(existing_ride["id"], receipts.is_cancellation_fee(snippet))
                continue

            amount = receipts.extract_amount(snippet)
            day = cand["ordered_at"][:10]

            ride_verdict, ride_service = receipts.classify_ride(cand["sender"], cand["subject"])
            if ride_verdict == "ride":
                # No amount in the fallback key: two duplicate emails for one trip
                # (receipt vs adjusted charge summary) usually carry different totals,
                # and including amount here would defeat dedupe exactly when it matters.
                key = receipts.extract_ride_time(snippet) or f"{day}|{cand['subject']}"
                existing = db.find_ride_by_key(ride_service, key)
                if existing:
                    # Gmail's messages.list returns newest-first, so within one scan
                    # the OLDER email is processed last. Only overwrite when this
                    # candidate is genuinely newer than what produced the stored
                    # amount (ties keep the stored value) — otherwise "later email
                    # wins" silently inverts into "whichever is processed last wins",
                    # and since the losing candidate's message id is never recorded,
                    # it would keep re-pinning the wrong amount on every future scan.
                    # ride_at itself is never rewritten (see set_ride_amount) — the
                    # comparison basis stays fixed across repeated scans, and a ride
                    # never re-buckets into a different day/week after insert.
                    if amount is not None and cand["ordered_at"] > existing["ride_at"]:
                        db.set_ride_amount(existing["id"], amount)
                    continue
                if db.add_ride(cand["gmail_message_id"], ride_service, cand["ordered_at"],
                               key, cand["subject"], amount,
                               is_cancellation=receipts.is_cancellation_fee(snippet)):
                    rides_added += 1
                    stored = db.find_ride_by_key(ride_service, key)
                    verdict = ai_metrics.classify_work_ride(
                        ride_service, cand["subject"], snippet, ride_examples)
                    db.set_ride_classification(stored["id"], verdict["is_work"], verdict["confidence"])
                continue

            if receipts.is_followup(snippet):
                _, service = receipts.classify_candidate(cand["sender"], cand["subject"])
                if not service:
                    continue
                existing = db.find_delivery_order(service, day, cand["subject"])
                if existing:
                    if amount is not None:
                        db.set_delivery_amount(existing["id"], amount)
                elif receipts.is_tip_receipt(snippet):
                    if db.add_delivery_order(cand["gmail_message_id"], service,
                                             cand["ordered_at"], cand["subject"], amount):
                        added += 1
                continue
            verdict, service = receipts.classify_candidate(cand["sender"], cand["subject"])
            if verdict == "ambiguous":
                ai_checked += 1
                verdict = "order" if ai_metrics.classify_receipt(
                    cand["sender"], cand["subject"], snippet) else "not_order"
            if verdict == "order":
                if db.find_delivery_order(service, day, cand["subject"]):
                    continue
                if db.add_delivery_order(cand["gmail_message_id"], service,
                                         cand["ordered_at"], cand["subject"], amount):
                    added += 1
        db.set_setting("gmail_last_run", _now_iso())
        db.set_setting("gmail_last_status", "ok")
        db.set_setting(
            "gmail_last_result",
            f"{len(candidates)} candidates · {ai_checked} AI-checked · {added} new orders · "
            f"{rides_added} new rides",
        )
        logger.info(
            "Gmail scan: %d candidates, %d AI-checked, %d new orders, %d new rides",
            len(candidates), ai_checked, added, rides_added,
        )
    except Exception as e:
        # Full detail server-side only (Railway logs are not user-facing). The DB
        # value must come from the closed set — never str(e) — because a future
        # SimpleFIN URL carries its bank credentials inside the URL itself, and
        # HTTP libraries routinely put that URL into the exception message.
        logger.exception("Gmail scan failed")
        db.set_setting("gmail_last_run", _now_iso())
        db.set_setting("gmail_last_status", safe_status(e))
