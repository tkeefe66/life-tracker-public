"""scan_calendar job: upsert + classify-once semantics."""


def _events():
    return [
        {"event_id": "ev1", "title": "Dinner w/ Sam", "start_datetime": "2026-07-15T19:00:00-06:00",
         "end_datetime": "2026-07-15T21:00:00-06:00", "description": "", "location": "Bar Dough",
         "attendees": ["Sam"]},
        {"event_id": "ev2", "title": "Dentist", "start_datetime": "2026-07-16T09:00:00-06:00",
         "end_datetime": "2026-07-16T10:00:00-06:00", "description": "", "location": "",
         "attendees": []},
    ]


def test_scan_classifies_new_events(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_calendar

    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", lambda days_back: _events())
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(
        scan_calendar.ai_metrics, "classify_social_event",
        lambda title, desc, loc, att, examples=None: {"is_social": title.startswith("Dinner"), "confidence": 0.9},
    )

    scan_calendar.run()

    social = db.get_social_events_range("2026-07-13", "2026-07-19")
    assert [e["gcal_event_id"] for e in social] == ["ev1"]
    assert db.get_setting("calendar_last_status") == "ok"


def test_scan_does_not_reclassify(temp_db_path, monkeypatch):
    from jobs import scan_calendar

    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", lambda days_back: _events())
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)
    calls = []
    monkeypatch.setattr(
        scan_calendar.ai_metrics, "classify_social_event",
        lambda title, desc, loc, att, examples=None: calls.append(title) or {"is_social": True, "confidence": 0.9},
    )

    scan_calendar.run()
    scan_calendar.run()  # second run: events already classified
    assert len(calls) == 2  # ev1 + ev2, once each


def test_scan_records_error(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_calendar

    def boom(days_back):
        raise RuntimeError("auth expired")
    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", boom)
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)

    scan_calendar.run()  # must not raise
    assert db.get_setting("calendar_last_status").startswith("error:")


def test_scan_passes_classification_examples_to_each_call(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_calendar

    # A prior user override on a recurring event becomes an example for future
    # classification calls — series membership is what makes it safe to
    # generalize (see get_classification_examples / the granularity spec).
    db.upsert_calendar_event(
        "evold", "Taco Tuesday", "2026-07-01T19:00:00-06:00", "2026-07-01T21:00:00-06:00",
        recurring_event_id="taco-tuesday-series",
    )
    db.set_event_classification("evold", False, 0.5)
    db.set_event_overrides("evold", {"user_is_social": True})

    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", lambda days_back: _events())
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)
    received = []

    def fake_classify(title, desc, loc, att, examples=None):
        received.append(examples)
        return {"is_social": True, "confidence": 0.9}

    monkeypatch.setattr(scan_calendar.ai_metrics, "classify_social_event", fake_classify)
    scan_calendar.run()

    assert len(received) == 2  # ev1 + ev2
    for examples in received:
        assert examples is not None
        assert any(e["title"] == "Taco Tuesday" for e in examples)


def test_scan_stores_location_and_date_flag(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_calendar

    events = [
        {"event_id": "evd1", "title": "Date night", "start_datetime": "2026-07-15T19:00:00-06:00",
         "end_datetime": "2026-07-15T21:00:00-06:00", "description": "", "location": "Bar Dough",
         "attendees": []},
        {"event_id": "evd2", "title": "Trivia", "start_datetime": "2026-07-16T19:00:00-06:00",
         "end_datetime": "2026-07-16T21:00:00-06:00", "description": "", "location": "",
         "attendees": []},
    ]
    monkeypatch.setattr(scan_calendar.calendar_service, "get_events_range", lambda days_back: events)
    monkeypatch.setattr(scan_calendar.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(
        scan_calendar.ai_metrics, "classify_social_event",
        lambda title, desc, loc, att, examples=None: {"is_social": True, "confidence": 0.9},
    )

    scan_calendar.run()

    ev1 = db.get_event("evd1")
    assert bool(ev1["is_date"]) is True and ev1["location"] == "Bar Dough"
    ev2 = db.get_event("evd2")
    assert not ev2["is_date"]
