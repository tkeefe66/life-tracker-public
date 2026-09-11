"""Assemble weekly scorecards from DB counts. All week logic is Monday–Sunday local time."""
import datetime
from datetime import date, timedelta
from typing import Optional

import pytz

import database as db
import metrics
from config import TIMEZONE


def _tz():
    return pytz.timezone(TIMEZONE)


def _local_today() -> date:
    return datetime.datetime.now(_tz()).date()


def _occurred(end_at_iso: str) -> bool:
    try:
        end = datetime.datetime.fromisoformat(end_at_iso)
    except ValueError:
        return False
    if end.tzinfo is None:
        end = _tz().localize(end)
    return end <= datetime.datetime.now(_tz())


def _social_counts(e: dict) -> bool:
    """Manual events are user-asserted facts, not calendar predictions — they count
    immediately, unlike detected gcal events which wait for _occurred(end_at)."""
    return e.get("source") == "manual" or _occurred(e["end_at"])


def counts_for_week(week_start: date) -> dict:
    ws, we = metrics.week_bounds(week_start)
    start, end = ws.isoformat(), we.isoformat()
    checkins = db.get_checkins_range(start, end)
    # Deliberate split: scorecard counts social events by end_at (event has occurred),
    # while Today displays by start_at (see db.get_events_for_day) — do not "unify" these.
    social = [e for e in db.get_social_events_range(start, end) if _social_counts(e)]
    return {
        "gym": sum(1 for c in checkins if c["type"] == "gym"),
        "alcohol": sum(1 for c in checkins if c["type"] == "alcohol"),
        "substances": sum(1 for c in checkins if c["type"] == "substances"),
        "delivery": len(db.get_delivery_orders_range(start, end)),
        "social": len(social),
    }


def _personal_rides(rides: list) -> list:
    """Resolved work flag = COALESCE(user_is_work, false) — only a CONFIRMED user
    verdict excludes a ride. An AI flag alone never excludes it (flag but still count)."""
    return [r for r in rides if not r.get("user_is_work")]


def _spend_by_service(orders: list, rides: list, social_spend: float, dates_spend: float = 0.0) -> list:
    """Per-service spend rows (kind: delivery | ride | social | date),
    zero/None amounts dropped, each rounded to 2dp, sorted by amount
    descending."""
    by_service: dict = {}
    for o in orders:
        by_service[("delivery", o["service"])] = by_service.get(("delivery", o["service"]), 0) + (o["amount"] or 0)
    for r in rides:
        by_service[("ride", r["service"])] = by_service.get(("ride", r["service"]), 0) + (r["amount"] or 0)
    if social_spend:
        by_service[("social", "Social")] = social_spend
    if dates_spend:
        by_service[("date", "Dates")] = dates_spend

    rows = [
        {"kind": kind, "service": service, "amount": round(amount, 2)}
        for (kind, service), amount in by_service.items()
        if amount
    ]
    rows.sort(key=lambda r: r["amount"], reverse=True)
    return rows


def scorecard_for_week(week_start: date) -> dict:
    ws, we = metrics.week_bounds(week_start)
    card = metrics.build_scorecard(week_start, counts_for_week(week_start), db.get_targets())
    orders = db.get_delivery_orders_range(ws.isoformat(), we.isoformat())
    card["delivery_spend"] = round(sum(o["amount"] or 0 for o in orders), 2)
    social = [e for e in db.get_social_events_range(ws.isoformat(), we.isoformat()) if _social_counts(e)]
    card["social_spend"] = round(sum(e["amount"] or 0 for e in social), 2)
    dates = [e for e in db.get_date_events_range(ws.isoformat(), we.isoformat()) if _social_counts(e)]
    card["dates_count"] = len(dates)
    card["dates_spend"] = round(sum(e["amount"] or 0 for e in dates), 2)
    rides = _personal_rides(db.get_rides_range(ws.isoformat(), we.isoformat()))
    card["rides_count"] = len(rides)
    card["rides_spend"] = round(sum(r["amount"] or 0 for r in rides), 2)
    card["spend_by_service"] = _spend_by_service(orders, rides, card["social_spend"], card["dates_spend"])
    return card


def history(weeks: int) -> dict:
    """Completed weeks only, oldest-first, plus streaks."""
    this_monday = metrics.week_bounds(_local_today())[0]
    cards = []
    for i in range(weeks, 0, -1):
        cards.append(scorecard_for_week(this_monday - timedelta(weeks=i)))
    return {"weeks": cards, "streaks": metrics.streaks(cards)}


PATTERN_WEEKS = 8


def _date_lists(start: date, end: date) -> dict:
    """Per-metric ISO day lists for events inside [start, end]."""
    s, e = start.isoformat(), end.isoformat()
    checkins = db.get_checkins_range(s, e)
    social = [ev for ev in db.get_social_events_range(s, e) if _social_counts(ev)]
    return {
        "gym": [c["date"] for c in checkins if c["type"] == "gym"],
        "alcohol": [c["date"] for c in checkins if c["type"] == "alcohol"],
        "substances": [c["date"] for c in checkins if c["type"] == "substances"],
        "delivery": [o["day"] for o in db.get_delivery_orders_range(s, e)],
        "social": [ev["end_at"][:10] for ev in social],
    }


def _dates_summary(weeks: int) -> dict:
    """Unscored dates series for the Insights panel (2026-07-30 spec):
    weekly counts (completed weeks + current, oldest-first), totals, and
    top places by resolved location. avg_spend divides by dates that HAVE
    an amount — a date with no recorded cost shouldn't drag the average."""
    this_monday = metrics.week_bounds(_local_today())[0]
    week_starts = [this_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    window_start = week_starts[0].isoformat()
    window_end = metrics.week_bounds(week_starts[-1])[1].isoformat()
    dates = [e for e in db.get_date_events_range(window_start, window_end) if _social_counts(e)]

    weekly = []
    for ws in week_starts:
        we = metrics.week_bounds(ws)[1]
        n = sum(1 for e in dates if ws.isoformat() <= e["end_at"][:10] <= we.isoformat())
        weekly.append({"week_start": ws.isoformat(), "count": n})

    with_amount = [e for e in dates if e["amount"]]
    total = round(sum(e["amount"] for e in with_amount), 2)
    places: dict = {}
    for e in dates:
        place = (e["location"] or "").strip()
        if not place:
            continue
        c, s = places.get(place, (0, 0.0))
        places[place] = (c + 1, s + (e["amount"] or 0))
    top_places = [{"place": p, "count": c, "spend": round(s, 2)}
                  for p, (c, s) in places.items()]
    top_places.sort(key=lambda r: (r["count"], r["spend"]), reverse=True)

    return {
        "weekly": weekly,
        "count": len(dates),
        "total_spend": total,
        "avg_spend": round(total / len(with_amount), 2) if with_amount else 0,
        "top_places": top_places[:5],
    }


def insights(weeks: int) -> dict:
    hist = history(weeks)
    series = {k: [w["metrics"][k]["count"] for w in hist["weeks"]] for k in metrics.METRICS}
    this_monday = metrics.week_bounds(_local_today())[0]
    dates = _date_lists(this_monday - timedelta(weeks=PATTERN_WEEKS), this_monday - timedelta(days=1))
    return {
        "weeks": hist["weeks"],
        "streaks": hist["streaks"],
        "weekday_counts": {k: metrics.weekday_counts(v) for k, v in dates.items()},
        "noticings": metrics.noticings(dates, series),
        "dates": _dates_summary(PATTERN_WEEKS),
    }


SPEND_ITEMS_CAP = 100


def spend(weeks: int) -> dict:
    """Windowed money view for the Insights tab: a dense per-week category
    series (oldest-first, zero weeks included), the same weeks aggregated into
    per-service totals, and a capped, newest-first itemized list.

    Unlike history() — which excludes the in-progress current week because a
    partial week corrupts streaks — that rationale doesn't apply to money, so
    the window here is the current week plus the preceding weeks-1, with the
    current week last. An order placed an hour ago must show up in Money the
    same as it already does in Today's "Spent today" and Week's "Spent this
    week"."""
    this_monday = metrics.week_bounds(_local_today())[0]
    week_starts = [this_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    window_start = week_starts[0].isoformat()
    window_end = metrics.week_bounds(week_starts[-1])[1].isoformat()

    orders = db.get_delivery_orders_range(window_start, window_end)
    rides = _personal_rides(db.get_rides_range(window_start, window_end))
    social = [e for e in db.get_social_events_range(window_start, window_end) if _social_counts(e)]
    dates = [e for e in db.get_date_events_range(window_start, window_end) if _social_counts(e)]

    weeks_out = []
    by_service: dict = {}
    for ws in week_starts:
        we = metrics.week_bounds(ws)[1]
        ws_iso, we_iso = ws.isoformat(), we.isoformat()
        # Per-week split on the SQL-resolved day (night cutoff + user nudge) —
        # must agree with the range queries' outer filter, or an item near a
        # week boundary would land in the wrong week's total.
        week_orders = [o for o in orders if ws_iso <= o["day"] <= we_iso]
        week_rides = [r for r in rides if ws_iso <= r["day"] <= we_iso]
        week_social = [e for e in social if ws_iso <= e["end_at"][:10] <= we_iso]
        week_dates = [e for e in dates if ws_iso <= e["end_at"][:10] <= we_iso]
        delivery_total = round(sum(o["amount"] or 0 for o in week_orders), 2)
        rides_total = round(sum(r["amount"] or 0 for r in week_rides), 2)
        social_raw = sum(e["amount"] or 0 for e in week_social)
        dates_raw = sum(e["amount"] or 0 for e in week_dates)
        weeks_out.append({
            "week_start": ws_iso, "delivery": delivery_total,
            "rides": rides_total, "social": round(social_raw, 2),
            "dates": round(dates_raw, 2),
        })
        for o in week_orders:
            key = ("delivery", o["service"])
            by_service[key] = by_service.get(key, 0) + (o["amount"] or 0)
        for r in week_rides:
            key = ("ride", r["service"])
            by_service[key] = by_service.get(key, 0) + (r["amount"] or 0)
        if social_raw:
            key = ("social", "Social")
            # Accumulate the raw amount here, not the already-rounded weekly
            # total above — delivery/rides do the same, and rounding twice
            # can drift the by_service Total a cent from the hero total.
            by_service[key] = by_service.get(key, 0) + social_raw
        if dates_raw:
            key = ("date", "Dates")
            by_service[key] = by_service.get(key, 0) + dates_raw

    by_service_out = [
        {"kind": kind, "service": service, "amount": round(amount, 2)}
        for (kind, service), amount in by_service.items() if amount
    ]
    by_service_out.sort(key=lambda r: r["amount"], reverse=True)

    items = []
    for o in orders:
        if o["amount"]:
            items.append({"kind": "delivery", "service": o["service"],
                          "label": o["subject"], "at": o["ordered_at"], "amount": round(o["amount"], 2)})
    for r in rides:
        if r["amount"]:
            # "at" is the resolved TRUE ride time (ride_time), not the raw
            # email-arrival ride_at — see database.get_rides_range.
            items.append({"kind": "ride", "service": r["service"],
                          "label": r["subject"], "at": r["ride_time"], "amount": round(r["amount"], 2)})
    for e in social:
        if e["amount"]:
            items.append({"kind": "social", "service": "Social",
                          "label": e["title"], "at": e["end_at"], "amount": round(e["amount"], 2)})
    for e in dates:
        if e["amount"]:
            items.append({"kind": "date", "service": "Dates",
                          "label": e["title"], "at": e["end_at"], "amount": round(e["amount"], 2)})
    items.sort(key=lambda i: i["at"], reverse=True)

    return {"weeks": weeks_out, "by_service": by_service_out, "items": items[:SPEND_ITEMS_CAP]}


def week_days(week_start: date) -> dict:
    """Day-by-day view for the Week tab: always exactly 7 entries, Monday-first,
    including empty days. Built from one ranged query per source, grouped by day
    in Python — never seven per-day round trips. Work rides (confirmed via
    user_is_work) appear in items labeled is_work but are excluded from both the
    day total and the week total, same rule as every other spend figure."""
    ws, we = metrics.week_bounds(week_start)
    start, end = ws.isoformat(), we.isoformat()
    checkins = db.get_checkins_range(start, end)
    orders = db.get_delivery_orders_range(start, end)
    rides = db.get_rides_range(start, end)
    social = [e for e in db.get_social_events_range(start, end) if _social_counts(e)]
    dates = [e for e in db.get_date_events_range(start, end) if _social_counts(e)]
    personal_ride_ids = {r["id"] for r in _personal_rides(rides)}

    days = []
    week_total = 0.0
    for i in range(7):
        d_iso = (ws + timedelta(days=i)).isoformat()

        day_checkins = [c for c in checkins if c["date"] == d_iso]
        alcohol = next((c for c in day_checkins if c["type"] == "alcohol"), None)

        items = []
        day_total = 0.0
        for o in orders:
            if o["day"] == d_iso:
                amount = o["amount"] or 0
                items.append({"kind": "delivery", "service": o["service"], "label": o["subject"],
                              "at": o["ordered_at"], "amount": round(amount, 2), "is_work": False})
                day_total += amount
        for r in rides:
            # Grouped by the SQL-resolved day — cutoff + user nudge, see
            # database.get_rides_range.
            if r["day"] == d_iso:
                amount = r["amount"] or 0
                is_work = r["id"] not in personal_ride_ids
                items.append({"kind": "ride", "service": r["service"], "label": r["subject"],
                              "at": r["ride_time"], "amount": round(amount, 2), "is_work": is_work})
                if not is_work:
                    day_total += amount
        for e in social:
            if e["end_at"][:10] == d_iso:
                amount = e["amount"] or 0
                items.append({"kind": "social", "service": "Social", "label": e["title"],
                              "at": e["end_at"], "amount": round(amount, 2), "is_work": False})
                day_total += amount
        for e in dates:
            if e["end_at"][:10] == d_iso:
                amount = e["amount"] or 0
                items.append({"kind": "date", "service": "Dates", "label": e["title"],
                              "at": e["end_at"], "amount": round(amount, 2), "is_work": False})
                day_total += amount
        items.sort(key=lambda i: i["at"])

        day_total = round(day_total, 2)
        week_total += day_total
        days.append({
            "date": d_iso,
            "gym": any(c["type"] == "gym" for c in day_checkins),
            "alcohol_level": alcohol["level"] if alcohol else None,
            "substances": any(c["type"] == "substances" for c in day_checkins),
            "total": day_total,
            "items": items,
        })

    return {"week_start": start, "week_end": end, "week_total": round(week_total, 2), "days": days}


def today_snapshot(day: Optional[date] = None) -> dict:
    d = (day or _local_today()).isoformat()
    checkins = db.get_checkins_range(d, d)
    alcohol = next((c for c in checkins if c["type"] == "alcohol"), None)
    return {
        "date": d,
        "gym": any(c["type"] == "gym" for c in checkins),
        "alcohol_level": alcohol["level"] if alcohol else None,
        "substances": any(c["type"] == "substances" for c in checkins),
        "deliveries": db.get_delivery_orders_range(d, d),
        "social_events": db.get_events_for_day(d),
        "rides": [{**r, "is_work": bool(r["user_is_work"])} for r in db.get_rides_range(d, d)],
    }
