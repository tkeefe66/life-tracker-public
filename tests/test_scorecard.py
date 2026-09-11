from datetime import date, datetime, timedelta

import pytz


def _seed_week(db):
    """Data inside week 2026-07-13 (Mon) … 2026-07-19 (Sun) — all in the past."""
    db.seed_default_targets()
    db.record_checkin("2026-07-13", "gym")
    db.record_checkin("2026-07-14", "gym")
    db.record_checkin("2026-07-14", "alcohol", level=2)
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "order")
    db.add_delivery_order("m2", "DoorDash", "2026-07-16T19:30:00-06:00", "order")
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)


def test_scorecard_counts(temp_db_path):
    import database as db
    from app.scorecard import scorecard_for_week
    _seed_week(db)
    card = scorecard_for_week(date(2026, 7, 13))
    m = card["metrics"]
    assert m["gym"]["count"] == 2
    assert m["alcohol"]["count"] == 1
    assert m["delivery"]["count"] == 2
    assert m["social"]["count"] == 1
    assert m["gym"]["hit"] is False      # 2 < floor 3
    assert m["delivery"]["hit"] is False  # 2 > ceiling 1
    assert m["alcohol"]["hit"] is True    # 1 <= ceiling 2


def test_social_excludes_future_events(temp_db_path):
    import database as db
    from app.scorecard import scorecard_for_week
    db.seed_default_targets()
    tz = pytz.timezone("America/Denver")
    future_start = datetime.now(tz) + timedelta(hours=2)
    future_end = future_start + timedelta(hours=2)
    db.upsert_calendar_event("evf", "Party tonight", future_start.isoformat(), future_end.isoformat())
    db.set_event_classification("evf", True, 0.9)
    card = scorecard_for_week(future_start.date())
    assert card["metrics"]["social"]["count"] == 0  # hasn't occurred yet


def test_manual_social_event_counts_before_it_would_have_occurred(temp_db_path):
    """Manual events are user-asserted facts, not calendar predictions — they must
    count toward the week and social_spend immediately, regardless of the synthetic
    12:00-13:00 span the API gives them or what time of day it currently is."""
    import database as db
    from app.scorecard import counts_for_week, scorecard_for_week, _local_today
    db.seed_default_targets()
    today = _local_today()
    day = today.isoformat()
    # end_at far in the future relative to "now" so an occurrence gate would exclude it.
    db.add_manual_social_event("manual:trivia", "Trivia night", f"{day}T23:58:00", f"{day}T23:59:00", amount=15.0)
    assert counts_for_week(today)["social"] == 1
    assert scorecard_for_week(today)["social_spend"] == 15.0


def test_history_excludes_current_week_and_computes_streaks(temp_db_path):
    import database as db
    from app.scorecard import history
    db.seed_default_targets()
    out = history(weeks=4)
    assert len(out["weeks"]) == 4
    assert set(out["streaks"].keys()) == {"delivery", "gym", "social", "alcohol", "substances"}
    import metrics
    from datetime import date as d
    today_week_start = metrics.week_bounds(d.today())[0].isoformat()
    assert all(w["week_start"] != today_week_start for w in out["weeks"])


def test_today_snapshot(temp_db_path, monkeypatch):
    import database as db
    from app import scorecard
    db.seed_default_targets()
    today = scorecard._local_today().isoformat()
    db.record_checkin(today, "gym")
    db.record_checkin(today, "alcohol", level=1)
    snap = scorecard.today_snapshot()
    assert snap["date"] == today
    assert snap["gym"] is True
    assert snap["alcohol_level"] == 1
    assert snap["deliveries"] == []
    assert snap["social_events"] == []


def test_scorecard_rides_count_and_spend_exclude_confirmed_work(temp_db_path):
    import database as db
    from app.scorecard import scorecard_for_week
    db.seed_default_targets()
    db.add_ride("r1", "Uber", "2026-07-15T08:00:00-06:00", "2026-07-15T08:00", "Personal", 12.0)
    db.add_ride("r2", "Uber", "2026-07-16T08:00:00-06:00", "2026-07-16T08:00", "AI-flagged", 30.0)
    db.add_ride("r3", "Lyft", "2026-07-17T08:00:00-06:00", "2026-07-17T08:00", "Confirmed work", 50.0)
    rides = db.get_rides_range("2026-07-13", "2026-07-19")
    by_subject = {r["subject"]: r["id"] for r in rides}
    db.set_ride_classification(by_subject["AI-flagged"], True, 0.8)  # flagged, still counts
    db.set_ride_work_override(by_subject["Confirmed work"], True)  # confirmed, excluded

    card = scorecard_for_week(date(2026, 7, 13))
    assert card["rides_count"] == 2
    assert card["rides_spend"] == 42.0
    import metrics
    assert "rides" not in metrics.METRICS


def test_rides_not_in_today_snapshot_metrics_ledger(temp_db_path):
    """Rides are tracking-only — no hit/miss ledger row, ever."""
    import database as db
    db.seed_default_targets()
    assert "rides" not in db.get_targets()


def test_today_snapshot_includes_rides_for_the_day(temp_db_path):
    import database as db
    from app import scorecard
    db.seed_default_targets()
    today = scorecard._local_today().isoformat()
    db.add_ride("r1", "Uber", f"{today}T08:00:00", f"{today}T08:00", "Your trip", 12.0)
    snap = scorecard.today_snapshot()
    assert len(snap["rides"]) == 1
    assert snap["rides"][0]["service"] == "Uber"


def test_spend_by_service_shape_sorting_and_exclusions(temp_db_path):
    """Two delivery services (one with a null-amount order that must not appear as
    its own zero row), a personal ride, a confirmed-work ride (excluded), and a
    social event with a cost — one row per service, sorted by amount descending,
    the work ride absent, no zero/None rows."""
    import database as db
    from app.scorecard import scorecard_for_week
    db.seed_default_targets()
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "order", 16.31)
    db.add_delivery_order("m2", "Uber Eats", "2026-07-16T19:30:00-06:00", "order", 10.0)
    db.add_delivery_order("m3", "DoorDash", "2026-07-16T19:30:00-06:00", "order", 8.0)
    db.add_delivery_order("m4", "DoorDash", "2026-07-17T19:30:00-06:00", "order")  # amount NULL
    db.add_ride("r1", "Uber", "2026-07-15T08:00:00-06:00", "2026-07-15T08:00", "Personal", 12.0)
    db.add_ride("r2", "Lyft", "2026-07-16T08:00:00-06:00", "2026-07-16T08:00", "Work trip", 50.0)
    rides = db.get_rides_range("2026-07-13", "2026-07-19")
    by_subject = {r["subject"]: r["id"] for r in rides}
    db.set_ride_work_override(by_subject["Work trip"], True)  # confirmed work — excluded
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"amount": 25.0})

    card = scorecard_for_week(date(2026, 7, 13))
    rows = card["spend_by_service"]

    assert rows == [
        {"kind": "delivery", "service": "Uber Eats", "amount": 26.31},
        {"kind": "social", "service": "Social", "amount": 25.0},
        {"kind": "ride", "service": "Uber", "amount": 12.0},
        {"kind": "delivery", "service": "DoorDash", "amount": 8.0},
    ]
    assert not any(r["service"] == "Lyft" for r in rows)  # confirmed-work ride excluded


def test_week_days_buckets_ride_by_effective_date_and_shows_true_ride_time(temp_db_path):
    """A ride placed at 2026-07-20T01:10 (Monday, raw ride_at) but whose TRUE
    ride time (ride_key) is 2026-07-19T23:00 (Sunday night, before the 4am
    cutoff doesn't even apply here — 23:00 is well after it) must land on
    Sunday, not Monday, and the item's displayed time must be the true ride
    time, not the email-arrival ride_at."""
    import database as db
    from app.scorecard import week_days
    db.seed_default_targets()
    db.add_ride("r1", "Uber", "2026-07-20T01:10:00-06:00", "2026-07-19T23:00", "Late Sunday ride", 9.0)

    out = week_days(date(2026, 7, 13))  # week of 2026-07-13 (Mon) .. 2026-07-19 (Sun)
    sunday = out["days"][6]
    assert sunday["date"] == "2026-07-19"
    assert len(sunday["items"]) == 1
    assert sunday["items"][0]["at"] == "2026-07-19T23:00"  # true ride time, not ride_at
    assert sunday["total"] == 9.0

    monday = out["days"][0]
    assert monday["items"] == []  # not double-counted on the Monday it arrived


def test_week_days_night_cutoff_moves_early_morning_ride_to_previous_day(temp_db_path):
    """A ride truly occurring at 2026-07-25T02:34 (before the 4am cutoff)
    belongs to 2026-07-24, even though its raw ride_at is on the 25th."""
    import database as db
    from app.scorecard import week_days
    db.seed_default_targets()
    db.add_ride("r1", "Uber", "2026-07-25T08:00:00-06:00", "2026-07-25T02:34", "Cutoff ride", 7.0)

    out = week_days(date(2026, 7, 20))  # week of 2026-07-20 (Mon) .. 2026-07-26 (Sun)
    friday = next(d for d in out["days"] if d["date"] == "2026-07-24")
    saturday = next(d for d in out["days"] if d["date"] == "2026-07-25")
    assert len(friday["items"]) == 1
    assert friday["items"][0]["label"] == "Cutoff ride"
    assert saturday["items"] == []


def test_spend_windows_ride_into_previous_week_across_night_cutoff(temp_db_path, monkeypatch):
    """Net effect on weekly spend: a Monday 01:00 ride belongs to the previous
    week (Sunday), not the week it technically starts — exercised through
    app.scorecard.spend()'s per-week regrouping."""
    import datetime as dt
    import database as db
    from app import scorecard
    db.seed_default_targets()
    monkeypatch.setattr(scorecard, "_local_today", lambda: dt.date(2026, 7, 22))  # a Wednesday
    # ride_at (raw) lands on Monday 2026-07-20; the TRUE ride time (ride_key)
    # is Sunday night 2026-07-19T23:00, well after the 4am cutoff even applies —
    # it's simply a different calendar day than the raw email-arrival time.
    db.add_ride("r1", "Uber", "2026-07-20T01:10:00-06:00", "2026-07-19T23:00", "Late Sunday ride", 9.0)

    out = scorecard.spend(weeks=2)
    by_week = {w["week_start"]: w["rides"] for w in out["weeks"]}
    assert by_week["2026-07-13"] == 9.0  # week containing the 19th (Sunday)
    assert by_week["2026-07-20"] == 0.0  # week containing the 20th (Monday) — not double-counted
    assert out["items"][0]["at"] == "2026-07-19T23:00"  # true ride time, not raw ride_at


def test_monday_1am_delivery_counts_previous_week(temp_db_path):
    # A Monday 01:00 order counts in the PREVIOUS week (as its Sunday), not
    # the week starting that Monday — regression lock on the night cutoff
    # flowing through counts_for_week's ranged query.
    import database as db
    from app.scorecard import counts_for_week
    db.seed_default_targets()
    db.add_delivery_order("wk-1", "Uber Eats", "2026-07-27T01:00:00-06:00", "Order", 20.0)
    assert counts_for_week(date(2026, 7, 20))["delivery"] == 1
    assert counts_for_week(date(2026, 7, 27))["delivery"] == 0


def test_week_days_groups_delivery_by_resolved_day(temp_db_path):
    import database as db
    from app.scorecard import week_days
    db.seed_default_targets()
    db.add_delivery_order("wk-2", "Uber Eats", "2026-07-30T00:49:00-06:00", "Order", 28.21)
    out = week_days(date(2026, 7, 27))
    by_date = {d["date"]: d for d in out["days"]}
    assert any(i["kind"] == "delivery" for i in by_date["2026-07-29"]["items"])
    assert not any(i["kind"] == "delivery" for i in by_date["2026-07-30"]["items"])


def _seed_date(db, ev_id, day, amount, location):
    db.upsert_calendar_event(ev_id, "Date night", f"{day}T19:00:00-06:00",
                             f"{day}T21:00:00-06:00", location=location, is_date=True)
    db.set_event_classification(ev_id, True, 0.9)
    if amount is not None:
        db.set_event_overrides(ev_id, {"amount": amount})


def test_scorecard_date_spend_separated_from_social(temp_db_path):
    import database as db
    from app.scorecard import scorecard_for_week
    db.seed_default_targets()
    _seed_date(db, "sd1", "2026-07-15", 60.0, "Bar Dough")
    db.upsert_calendar_event("sp1", "Trivia", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00")
    db.set_event_classification("sp1", True, 0.9)
    db.set_event_overrides("sp1", {"amount": 20.0})
    card = scorecard_for_week(date(2026, 7, 13))
    assert card["metrics"]["social"]["count"] == 1          # the date doesn't count
    assert card["social_spend"] == 20.0                     # or spend
    assert card["dates_spend"] == 60.0
    rows = {(r["kind"], r["service"]): r["amount"] for r in card["spend_by_service"]}
    assert rows[("date", "Dates")] == 60.0
    assert rows[("social", "Social")] == 20.0


def test_week_days_date_items(temp_db_path):
    import database as db
    from app.scorecard import week_days
    db.seed_default_targets()
    _seed_date(db, "sd2", "2026-07-15", 60.0, "Bar Dough")
    out = week_days(date(2026, 7, 13))
    day = next(d for d in out["days"] if d["date"] == "2026-07-15")
    kinds = [i["kind"] for i in day["items"]]
    assert kinds == ["date"]
    assert day["total"] == 60.0


def test_insights_dates_block(temp_db_path):
    import datetime as dt
    import metrics
    import database as db
    from app.scorecard import insights
    db.seed_default_targets()
    # A day inside a completed recent week, so the 8-week insights window
    # is guaranteed to contain all three seeds.
    monday = metrics.week_bounds(dt.date.today())[0] - timedelta(weeks=2)
    _seed_date(db, "si1", monday.isoformat(), 60.0, "Bar Dough")
    _seed_date(db, "si2", (monday + timedelta(days=2)).isoformat(), 30.0, "Bar Dough")
    _seed_date(db, "si3", (monday + timedelta(days=3)).isoformat(), None, "Museum")
    out = insights(12)["dates"]
    assert out["count"] == 3
    assert out["total_spend"] == 90.0
    assert out["avg_spend"] == 45.0                          # 90 / 2 dates WITH amounts
    assert out["top_places"][0] == {"place": "Bar Dough", "count": 2, "spend": 90.0}
    assert out["top_places"][1] == {"place": "Museum", "count": 1, "spend": 0}
    assert sum(w["count"] for w in out["weekly"]) == 3
