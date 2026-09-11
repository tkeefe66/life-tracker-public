"""Protected API routes."""
import datetime
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import ai_metrics
import database as db
import metrics
from app import money
from app.auth import COOKIE_NAME, logout as auth_logout, require_auth
from app.scorecard import _local_today, history, insights, scorecard_for_week, spend, today_snapshot, week_days
from services import google_auth
from services.simplefin_service import SimpleFinError

router = APIRouter(dependencies=[Depends(require_auth)])

# Length caps for free-text user fields, shared across routes below. Kept
# near the top (rather than beside their first use) so a reader isn't left
# wondering whether a constant referenced early in the file is defined yet --
# module-level constants resolve at call time, so placement doesn't change
# behavior, only readability.
MAX_NOTE_LEN = 500
MAX_LABEL_LEN = 200


@router.post("/logout")
def post_logout(request: Request, response: Response):
    auth_logout(request.cookies.get(COOKIE_NAME, ""))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.post("/logout-all")
def post_logout_all(response: Response):
    """Revokes every session, including the caller's own. Sits behind the
    router-level require_auth like everything else, so only someone already
    holding a valid session can trigger it."""
    db.delete_all_sessions()
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


class CheckinBody(BaseModel):
    type: Literal["gym", "alcohol", "substances"]
    date: Optional[str] = None
    level: Optional[int] = Field(default=None, ge=1, le=3)


def _parse_date(value: str) -> datetime.date:
    try:
        d = datetime.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    if d > _local_today():
        raise HTTPException(status_code=400, detail="date cannot be in the future")
    return d


@router.get("/today")
def get_today(date: Optional[str] = None):
    day = _parse_date(date) if date else None
    return today_snapshot(day)


@router.post("/checkins")
def post_checkin(body: CheckinBody):
    if body.type == "alcohol" and body.level is None:
        raise HTTPException(status_code=400, detail="alcohol check-in requires level 1-3")
    day = (_parse_date(body.date) if body.date else _local_today()).isoformat()
    db.record_checkin(day, body.type, body.level)
    return {"ok": True}


@router.delete("/checkins/{type}")
def delete_checkin(type: Literal["gym", "alcohol", "substances"], date: Optional[str] = None):
    day = (_parse_date(date) if date else _local_today()).isoformat()
    db.delete_checkin(day, type)
    return {"ok": True}


@router.get("/scorecard")
def get_scorecard(week_start: Optional[str] = None):
    if week_start:
        try:
            start = datetime.date.fromisoformat(week_start)
        except ValueError:
            raise HTTPException(status_code=400, detail="week_start must be YYYY-MM-DD")
    else:
        start = _local_today()
    return scorecard_for_week(start)


@router.get("/week-days")
def get_week_days(week_start: Optional[str] = None):
    if week_start:
        try:
            start = datetime.date.fromisoformat(week_start)
        except ValueError:
            raise HTTPException(status_code=400, detail="week_start must be YYYY-MM-DD")
    else:
        start = _local_today()
    return week_days(start)


@router.get("/history")
def get_history(weeks: int = 8):
    return history(min(max(weeks, 1), 52))


@router.get("/insights")
def get_insights(weeks: int = 12):
    return insights(min(max(weeks, 1), 52))


@router.get("/spend")
def get_spend(weeks: int = 12):
    return spend(min(max(weeks, 1), 52))


@router.post("/reflection")
def get_reflection():
    week_start = metrics.week_bounds(_local_today())[0] - datetime.timedelta(weeks=1)
    ws = week_start.isoformat()
    cached = db.get_reflection(ws)
    if cached:
        return {"week_start": ws, "text": cached}
    card = scorecard_for_week(week_start)
    private_labels = [m["label"] for m in metrics.METRICS.values() if m.get("private")]
    card = {**card, "metrics": {k: v for k, v in card["metrics"].items()
                                if not metrics.METRICS.get(k, {}).get("private")}}
    notes = [n for n in insights(12)["noticings"]
             if not any(lbl in n for lbl in private_labels)]
    text = ai_metrics.weekly_reflection(card, notes)
    if not text:
        return Response(status_code=204)
    db.save_reflection(ws, text)
    return {"week_start": ws, "text": text}


@router.get("/targets")
def get_targets():
    return db.get_targets()


MAX_TARGET_VALUE = 100_000


@router.put("/targets")
def put_targets(body: dict):
    for metric, value in body.items():
        if metric not in metrics.METRICS:
            raise HTTPException(status_code=400, detail=f"unknown metric: {metric}")
        if type(value) is not int or value < 0 or value > MAX_TARGET_VALUE:
            raise HTTPException(status_code=400, detail=f"invalid value for {metric}")
    for metric, value in body.items():
        db.set_target(metric, value)
    return db.get_targets()


@router.get("/settings")
def get_settings():
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    return {
        "telegram_push": db.get_setting("telegram_push", "off"),
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "google_configured": google_auth.is_configured(),
        "gmail_last_run": db.get_setting("gmail_last_run"),
        "gmail_last_status": db.get_setting("gmail_last_status"),
        "gmail_last_result": db.get_setting("gmail_last_result"),
        "calendar_last_run": db.get_setting("calendar_last_run"),
        "calendar_last_status": db.get_setting("calendar_last_status"),
        "backup_last_run": db.get_setting("backup_last_run"),
        "backup_last_status": db.get_setting("backup_last_status"),
        "bank_last_run": db.get_setting("bank_last_run"),
        "bank_last_status": db.get_setting("bank_last_status"),
        "bank_last_result": db.get_setting("bank_last_result"),
    }


class SettingsBody(BaseModel):
    telegram_push: Literal["on", "off"]


@router.put("/settings")
def put_settings(body: SettingsBody):
    db.set_setting("telegram_push", body.telegram_push)
    return {"ok": True}


class SocialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    date: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)
    location: Optional[str] = Field(default=None, max_length=300)
    is_date: Optional[bool] = None


class SocialPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_social: Optional[bool] = None
    amount: Optional[float] = Field(default=None, ge=0)
    # "This occurrence didn't happen" — distinct from is_social ("this event
    # type isn't social"). See the granularity spec for why the two were
    # conflated before and why that conflation poisoned the classifier.
    removed: Optional[bool] = None
    # Date tag + place (2026-07-30 date-tracking spec) — user columns.
    is_date: Optional[bool] = None
    location: Optional[str] = Field(default=None, max_length=300)


@router.post("/social")
def post_social(body: SocialCreate):
    day = (_parse_date(body.date) if body.date else _local_today()).isoformat()
    event_id = "manual:" + uuid4().hex
    start_at, end_at = f"{day}T12:00:00", f"{day}T13:00:00"
    db.add_manual_social_event(event_id, body.name, start_at, end_at, body.amount,
                               location=body.location, is_date=bool(body.is_date))
    return {
        "gcal_event_id": event_id, "title": body.name,
        "start_at": start_at, "end_at": end_at,
        "source": "manual", "amount": body.amount,
        "location": body.location, "is_date": bool(body.is_date),
    }


@router.patch("/social/{event_id}")
def patch_social(event_id: str, body: SocialPatch):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404, detail="event not found")
    # Distinguish "field omitted" (leave untouched) from "field explicitly set to
    # null" (clear the override) — pydantic v2's model_fields_set tracks which keys
    # were actually present in the request body, regardless of their value.
    provided = body.model_fields_set
    updates = {}
    if "title" in provided:
        updates["user_title"] = body.title
    if "is_social" in provided:
        updates["user_is_social"] = body.is_social
    if "amount" in provided:
        updates["amount"] = body.amount
    if "removed" in provided:
        updates["user_removed"] = body.removed
    if "is_date" in provided:
        updates["user_is_date"] = body.is_date
    if "location" in provided:
        updates["user_location"] = body.location
    if updates:
        db.set_event_overrides(event_id, updates)
    return {"ok": True}


@router.delete("/social/{event_id}")
def delete_social(event_id: str):
    ev = db.get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="event not found")
    if ev.get("source") != "manual":
        raise HTTPException(status_code=400, detail="detected events can only be turned off, not deleted")
    db.delete_event(event_id)
    return {"ok": True}


@router.get("/deliveries")
def get_deliveries(days: int = 60):
    d = min(max(days, 1), 365)
    end = _local_today()
    start = end - datetime.timedelta(days=d)
    orders = db.get_delivery_orders_range(start.isoformat(), end.isoformat())
    orders.sort(key=lambda o: o["ordered_at"], reverse=True)
    return {"orders": [
        {"service": o["service"], "subject": o["subject"], "ordered_at": o["ordered_at"], "amount": o["amount"]}
        for o in orders
    ]}


def _validated_user_date(auto_day: str, requested_day: str) -> Optional[str]:
    """Shared PATCH-body validation for the ±1-day nudge: parse, then apply
    the pure rule (metrics.nudge_user_date). Raises HTTPException(400) with
    an actionable message."""
    try:
        requested = datetime.date.fromisoformat(requested_day)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    try:
        return metrics.nudge_user_date(datetime.date.fromisoformat(auto_day), requested, _local_today())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class DeliveryPatch(BaseModel):
    day: str


@router.patch("/deliveries/{order_id}")
def patch_delivery(order_id: int, body: DeliveryPatch):
    auto = db.get_delivery_auto_day(order_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="order not found")
    db.set_delivery_user_date(order_id, _validated_user_date(auto, body.day))
    return {"ok": True}


class RidePatch(BaseModel):
    is_work: Optional[bool] = None
    day: Optional[str] = None


@router.get("/rides")
def get_rides(days: int = 60):
    d = min(max(days, 1), 365)
    end = _local_today()
    start = end - datetime.timedelta(days=d)
    rides = db.get_rides_range(start.isoformat(), end.isoformat())
    rides.sort(key=lambda r: r["ride_at"], reverse=True)
    return {"rides": [
        {
            "id": r["id"], "service": r["service"], "ride_at": r["ride_at"],
            "subject": r["subject"], "amount": r["amount"],
            "ai_is_work": r["ai_is_work"], "user_is_work": r["user_is_work"],
            "is_work": bool(r["user_is_work"]),
        }
        for r in rides
    ]}


@router.patch("/rides/{ride_id}")
def patch_ride(ride_id: int, body: RidePatch):
    provided = body.model_fields_set
    if not provided:
        raise HTTPException(status_code=400, detail="nothing to update: send is_work and/or day")
    if "day" in provided:
        auto = db.get_ride_auto_day(ride_id)
        if auto is None:
            raise HTTPException(status_code=404, detail="ride not found")
        db.set_ride_user_date(ride_id, _validated_user_date(auto, body.day))
    if "is_work" in provided:
        if body.is_work is None:
            raise HTTPException(status_code=400, detail="is_work must be true or false")
        if not db.set_ride_work_override(ride_id, body.is_work):
            raise HTTPException(status_code=404, detail="ride not found")
    return {"ok": True}


@router.get("/bank/debug")
def bank_debug(start: str, end: str):
    """Read-only verification surface for this phase — there is no bank UI yet.

    Returns per-flow counts and totals so the arithmetic can be eyeballed against
    reality. Never returns the access URL (which lives only in config and is
    never read here) and never returns balances (which are never stored).
    """
    try:
        datetime.date.fromisoformat(start)
        datetime.date.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="start/end must be YYYY-MM-DD")
    rows = db.get_bank_transactions_range(start, end)
    counts, totals = {}, {}
    for r in rows:
        flow = r["resolved_flow"] or "unclassified"
        counts[flow] = counts.get(flow, 0) + 1
        totals[flow] = round(totals.get(flow, 0.0) + abs(r["amount"]), 2)
    return {
        "accounts": db.get_bank_accounts(),
        "counts": counts,
        "totals": totals,
        "ambiguous": [
            {"simplefin_id": r["simplefin_id"], "posted": r["posted"],
             "amount": r["amount"], "description": r["description"]}
            for r in rows if r["ambiguous"]
        ],
        "last_run": db.get_setting("bank_last_run"),
        "last_status": db.get_setting("bank_last_status"),
    }


@router.post("/bank/accounts/{simplefin_id}/role")
def set_bank_account_role(simplefin_id: str, body: dict):
    """Set an account's role. Does not reclassify transactions immediately —
    flows are recomputed on the next sync (up to SIMPLEFIN_SYNC_INTERVAL_HOURS
    later), which is why scripts/simplefin_backfill.py runs the sync twice."""
    role = (body or {}).get("role")
    if role not in db.BANK_ROLES:
        raise HTTPException(status_code=400, detail="unknown role")
    if not db.set_bank_account_role(simplefin_id, role):
        raise HTTPException(status_code=404, detail="unknown account")
    return {"ok": True, "simplefin_id": simplefin_id, "role": role}


@router.post("/bank/accounts/{simplefin_id}/nickname")
def set_bank_account_nickname(simplefin_id: str, body: dict):
    """Set or clear an account's display nickname. Empty string clears back to
    the bank's own name. Unlike roles, takes effect immediately — resolution
    is COALESCE in SQL, no sync involved."""
    nickname = (body or {}).get("nickname")
    if nickname is not None and not isinstance(nickname, str):
        raise HTTPException(status_code=400, detail="nickname must be a string")
    if nickname is not None and len(nickname) > MAX_LABEL_LEN:
        raise HTTPException(status_code=400, detail=f"nickname too long (max {MAX_LABEL_LEN} chars)")
    if not db.set_bank_account_nickname(simplefin_id, nickname):
        raise HTTPException(status_code=404, detail="unknown account")
    return {"ok": True, "simplefin_id": simplefin_id,
            "nickname": (nickname or "").strip() or None}


@router.get("/bank/summary")
def get_bank_summary(weeks: int = 12):
    return money.summary(weeks)


@router.get("/bank/breakdown")
def get_bank_breakdown(weeks: int = 12, by: str = "payee",
                       account_id: Optional[int] = None):
    if by not in ("payee", "label"):
        raise HTTPException(status_code=400, detail="by must be 'payee' or 'label'")
    return money.breakdown(weeks, account_id=account_id, by=by)


@router.get("/bank/breakdown/rows")
def get_bank_breakdown_rows(weeks: int, payee: Optional[str] = None,
                            label: Optional[str] = None,
                            account_id: Optional[int] = None, limit: int = 100):
    if (payee is None) == (label is None):
        raise HTTPException(status_code=400,
                            detail="pass exactly one of payee or label")
    return money.breakdown_rows(weeks, vendor=payee, label=label,
                                account_id=account_id, limit=limit)


@router.get("/bank/triage")
def get_bank_triage(limit: int = 50):
    return money.triage(limit)


@router.get("/bank/label-suggestions")
def get_bank_label_suggestions(limit: int = 50):
    return money.label_suggestions(limit)


@router.get("/bank/investments")
def get_bank_investments():
    """Live holdings — never persisted. The 503 detail is the closed-set
    status string and nothing else (redaction boundary: no exception text
    crosses; the service already logged the real detail server-side)."""
    try:
        return money.investments()
    except SimpleFinError as e:
        raise HTTPException(status_code=503, detail=e.status)


MAX_BULK_FLOW_IDS = 200


class FlowPatch(BaseModel):
    flow: Optional[str] = None
    # Omitted, or explicit JSON null, both mean "leave the stored note
    # untouched" -- there is no wire-level difference between the two, so
    # the route checks `model_fields_set` rather than the value itself.
    # Only a provided string (including "") is forwarded to the DB layer,
    # where "" clears the note to NULL. This is what makes put-back
    # ({flow: null}) preserve a note: the note explains the transaction,
    # not the answer.
    note: Optional[str] = None


class LabelPatch(BaseModel):
    simplefin_id: Optional[str] = None
    payee: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=MAX_LABEL_LEN)
    no_label: Optional[bool] = None


class BulkFlowPatch(BaseModel):
    simplefin_ids: list[str]
    flow: Optional[str] = None
    # Bulk apply never takes a note -- one sentence shared across many rows
    # explains nothing. Present only so a caller that includes it gets a
    # loud 400 instead of the field being silently dropped.
    note: Optional[str] = None


@router.post("/bank/transactions/{simplefin_id}/flow")
def set_bank_transaction_flow(simplefin_id: str, body: FlowPatch):
    if body.flow is not None and body.flow not in db.BANK_FLOWS:
        raise HTTPException(status_code=400, detail="unknown flow")
    note_provided = "note" in body.model_fields_set and body.note is not None
    if note_provided and len(body.note) > MAX_NOTE_LEN:
        raise HTTPException(status_code=400, detail=f"note too long (max {MAX_NOTE_LEN} chars)")
    kwargs = {"note": body.note} if note_provided else {}
    if not db.set_bank_flow_override(simplefin_id, body.flow, **kwargs):
        raise HTTPException(status_code=404, detail="unknown transaction")
    return {"ok": True, "simplefin_id": simplefin_id, "flow": body.flow}


@router.post("/bank/transactions/flow")
def set_bank_transactions_flow_bulk(body: BulkFlowPatch):
    if body.note is not None:
        raise HTTPException(status_code=400, detail="bulk apply does not take a note")
    if len(body.simplefin_ids) > MAX_BULK_FLOW_IDS:
        raise HTTPException(status_code=400, detail=f"too many ids (max {MAX_BULK_FLOW_IDS})")
    try:
        updated = db.set_bank_flow_overrides_bulk(body.simplefin_ids, body.flow)
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown flow")
    return {"updated": updated}


@router.post("/bank/label")
def set_bank_label(body: LabelPatch):
    if (body.simplefin_id is None) == (body.payee is None):
        raise HTTPException(status_code=400,
                            detail="pass exactly one of simplefin_id or payee")
    if body.no_label:
        if body.payee is not None or (body.label or "").strip():
            raise HTTPException(status_code=400,
                                detail="no_label is a single-row verdict and takes no label")
        row_vendor = db.get_bank_transaction_vendor(body.simplefin_id)
        if row_vendor is None or not db.set_bank_no_label(body.simplefin_id):
            raise HTTPException(status_code=404, detail="unknown transaction")
        return {"ok": True, "label": None, "siblings": 0, "vendor": row_vendor}
    label = (body.label or "").strip() or None
    if body.payee is not None:
        # Bulk mode: the user tapped an explicit "apply to N more" offer.
        if not body.payee.strip():
            raise HTTPException(status_code=400, detail="payee must be non-empty")
        if label is None:
            raise HTTPException(status_code=400, detail="bulk apply needs a label")
        applied = db.set_bank_labels_by_vendor(body.payee, label)
        return {"ok": True, "label": label, "applied": applied}
    row_vendor = db.get_bank_transaction_vendor(body.simplefin_id)
    if row_vendor is None or not db.set_bank_label(body.simplefin_id, label):
        raise HTTPException(status_code=404, detail="unknown transaction")
    siblings = db.count_bank_unlabeled_by_vendor(row_vendor) if label else 0
    return {"ok": True, "label": label, "siblings": siblings, "vendor": row_vendor}


@router.get("/bank/accounts")
def get_bank_accounts():
    return db.get_bank_accounts()
