def test_format_scorecard_text():
    from jobs.weekly_push import format_scorecard_text
    card = {
        "week_start": "2026-07-13", "week_end": "2026-07-19",
        "metrics": {
            "delivery": {"label": "Delivery orders", "count": 2, "target": 1, "direction": "ceiling", "hit": False},
            "gym": {"label": "Gym sessions", "count": 3, "target": 3, "direction": "floor", "hit": True},
            "social": {"label": "Social events", "count": 2, "target": 2, "direction": "floor", "hit": True},
            "alcohol": {"label": "Alcohol days", "count": 1, "target": 2, "direction": "ceiling", "hit": True},
        },
    }
    text = format_scorecard_text(card)
    assert "2026-07-13" in text
    assert "✅ Gym sessions: 3 (target ≥3)" in text
    assert "❌ Delivery orders: 2 (target ≤1)" in text


def test_push_text_excludes_private_metrics():
    import datetime

    from jobs.weekly_push import format_scorecard_text
    import metrics as m
    card = m.build_scorecard(datetime.date(2026, 7, 13),
                              {"gym": 3, "substances": 2}, {})
    text = format_scorecard_text(card)
    assert "Gym" in text
    assert "Substances" not in text


def test_push_respects_toggle(temp_db_path, monkeypatch):
    import database as db
    from jobs import weekly_push
    db.seed_default_targets()
    sent = []
    monkeypatch.setattr(weekly_push, "notify", lambda text: sent.append(text) or True)

    weekly_push.run()          # toggle off (default)
    assert sent == []

    db.set_setting("telegram_push", "on")
    weekly_push.run()
    assert len(sent) == 1 and "On Track" in sent[0]
    assert db.get_setting("push_last_status") == "ok"


def test_scorecard_text_contains_no_date_lines(temp_db_path):
    # Dates are unscored (not in METRICS) — the Telegram text is built from
    # METRICS lines only, so a seeded date must leave no trace: no line, no
    # venue, no title. Regression lock on the by-construction rule
    # (2026-07-30 date-tracking spec). Counts/spend NUMBERS elsewhere in the
    # card are fine (rides set that precedent); content is what must not leak.
    from datetime import date
    import database as db
    from app.scorecard import scorecard_for_week
    from jobs.weekly_push import format_scorecard_text
    db.seed_default_targets()
    db.upsert_calendar_event("push-d", "Date night", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="Bar Dough", is_date=True)
    db.set_event_classification("push-d", True, 0.9)
    text = format_scorecard_text(scorecard_for_week(date(2026, 7, 13)))
    assert "Date night" not in text
    assert "Bar Dough" not in text
    assert "date" not in text.lower()
