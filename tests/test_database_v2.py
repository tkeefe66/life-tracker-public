"""v2 table helpers: checkins, delivery orders, calendar events, targets, settings."""


def _db(temp_db_path):
    import database
    return database


def test_checkin_upsert_and_range(temp_db_path):
    db = _db(temp_db_path)
    db.record_checkin("2026-07-14", "gym")
    db.record_checkin("2026-07-14", "alcohol", level=2)
    db.record_checkin("2026-07-14", "alcohol", level=3)  # upsert, not duplicate
    rows = db.get_checkins_range("2026-07-14", "2026-07-20")
    assert len(rows) == 2
    alcohol = next(r for r in rows if r["type"] == "alcohol")
    assert alcohol["level"] == 3


def test_delete_checkin(temp_db_path):
    db = _db(temp_db_path)
    db.record_checkin("2026-07-14", "gym")
    db.delete_checkin("2026-07-14", "gym")
    assert db.get_checkins_range("2026-07-14", "2026-07-14") == []


def test_delivery_order_dedupe(temp_db_path):
    db = _db(temp_db_path)
    assert db.add_delivery_order("msg1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order") is True
    assert db.add_delivery_order("msg1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order") is False
    assert db.has_delivery_order("msg1") is True
    assert db.has_delivery_order("msg2") is False
    rows = db.get_delivery_orders_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["service"] == "Uber Eats"


def test_delivery_range_excludes_outside_week(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("m1", "DoorDash", "2026-07-13T12:00:00-06:00", "s")  # Sunday before
    db.add_delivery_order("m2", "DoorDash", "2026-07-14T12:00:00-06:00", "s")  # Monday
    assert len(db.get_delivery_orders_range("2026-07-14", "2026-07-20")) == 1


def test_delivery_order_roundtrip_with_amount(temp_db_path):
    db = _db(temp_db_path)
    assert db.add_delivery_order(
        "m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order", 16.31
    ) is True
    rows = db.get_delivery_orders_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["amount"] == 16.31 and "id" in rows[0]


def test_delivery_order_amount_defaults_to_none(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order")
    rows = db.get_delivery_orders_range("2026-07-14", "2026-07-20")
    assert rows[0]["amount"] is None


def test_find_delivery_order_hit_and_miss(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order(
        "m1", "Uber Eats", "2026-07-15T19:30:00-06:00",
        "Your Monday evening order with Uber Eats", 16.31,
    )
    found = db.find_delivery_order("Uber Eats", "2026-07-15", "Your Monday evening order with Uber Eats")
    assert found is not None
    assert found["amount"] == 16.31
    assert "id" in found
    assert db.find_delivery_order("Uber Eats", "2026-07-16", "Your Monday evening order with Uber Eats") is None
    assert db.find_delivery_order("DoorDash", "2026-07-15", "Your Monday evening order with Uber Eats") is None
    assert db.find_delivery_order("Uber Eats", "2026-07-15", "Some other subject") is None


def test_set_delivery_amount_updates(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order")
    order = db.find_delivery_order("Uber Eats", "2026-07-15", "Your order")
    db.set_delivery_amount(order["id"], 22.5)
    updated = db.find_delivery_order("Uber Eats", "2026-07-15", "Your order")
    assert updated["amount"] == 22.5


def test_delivery_migration_is_idempotent(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order", 10.0)
    db.initialize_db()
    db.initialize_db()
    rows = db.get_delivery_orders_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["amount"] == 10.0


def test_calendar_event_classification_flow(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner w/ Sam", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    assert db.event_needs_classification("ev1") is True
    db.set_event_classification("ev1", True, 0.9)
    assert db.event_needs_classification("ev1") is False
    # upsert again (calendar re-fetch) must NOT wipe classification
    db.upsert_calendar_event("ev1", "Dinner w/ Sam (edited)", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    assert db.event_needs_classification("ev1") is False
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["title"] == "Dinner w/ Sam (edited)"


def test_social_range_excludes_non_social(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dentist", "2026-07-15T09:00:00-06:00", "2026-07-15T10:00:00-06:00")
    db.set_event_classification("ev1", False, 0.95)
    assert db.get_social_events_range("2026-07-14", "2026-07-20") == []


def test_events_for_day(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    assert len(db.get_events_for_day("2026-07-15")) == 1
    assert db.get_events_for_day("2026-07-16") == []


def test_events_for_day_and_range_expose_resolved_is_social(temp_db_path):
    """The editor needs the checkbox's true initial state, not a hardcoded guess."""
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    day_row = db.get_events_for_day("2026-07-15")[0]
    assert day_row["is_social"] is True
    range_row = db.get_social_events_range("2026-07-14", "2026-07-20")[0]
    assert range_row["is_social"] is True
    # User override flips the resolved verdict too.
    db.set_event_overrides("ev1", {"user_is_social": True})
    day_row = db.get_events_for_day("2026-07-15")[0]
    assert day_row["is_social"] is True


def test_targets_seed_and_update(temp_db_path):
    db = _db(temp_db_path)
    db.seed_default_targets()
    t = db.get_targets()
    assert t["delivery"] == {"direction": "ceiling", "value": 1}
    assert t["gym"] == {"direction": "floor", "value": 3}
    db.set_target("gym", 4)
    db.seed_default_targets()  # idempotent — must not reset user value
    assert db.get_targets()["gym"]["value"] == 4


def test_settings_roundtrip(temp_db_path):
    db = _db(temp_db_path)
    assert db.get_setting("telegram_push", "off") == "off"
    db.set_setting("telegram_push", "on")
    db.set_setting("telegram_push", "on")  # upsert
    assert db.get_setting("telegram_push") == "on"


def test_reflection_roundtrip(temp_db_path):
    import database as db
    assert db.get_reflection("2026-07-13") is None
    db.save_reflection("2026-07-13", "A solid week.")
    assert db.get_reflection("2026-07-13") == "A solid week."
    db.save_reflection("2026-07-13", "Revised.")
    assert db.get_reflection("2026-07-13") == "Revised."


# ── Social overrides + manual events (calendar_events migration) ────────────

def test_calendar_events_migration_is_idempotent(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.initialize_db()
    db.initialize_db()
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["gcal_event_id"] == "ev1"


def test_add_manual_social_event_appears_in_range(temp_db_path):
    db = _db(temp_db_path)
    db.add_manual_social_event(
        "manual:abc", "Trivia night", "2026-07-15T12:00:00", "2026-07-15T13:00:00", amount=20.0
    )
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Trivia night"
    assert row["source"] == "manual"
    assert row["amount"] == 20.0
    assert row["gcal_event_id"] == "manual:abc"


def test_add_manual_social_event_amount_optional(temp_db_path):
    db = _db(temp_db_path)
    db.add_manual_social_event("manual:xyz", "Coffee", "2026-07-15T12:00:00", "2026-07-15T13:00:00")
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert rows[0]["amount"] is None


def test_get_event_returns_row_or_none(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    row = db.get_event("ev1")
    assert row is not None and row["title"] == "Dinner"
    assert db.get_event("nope") is None


def test_user_rename_survives_calendar_scan_upsert(temp_db_path):
    """Critical correctness point: user_title must survive a scan re-upsert of title."""
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner w/ Sam", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"user_title": "Sam's birthday dinner"})

    # Nightly scan re-fetches and overwrites `title` from Google.
    db.upsert_calendar_event("ev1", "Dinner w/ Sam (Google edit)", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")

    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1
    assert rows[0]["title"] == "Sam's birthday dinner"  # user rename wins

    row = db.get_event("ev1")
    assert row["title"] == "Dinner w/ Sam (Google edit)"  # raw calendar title still updated
    assert row["user_title"] == "Sam's birthday dinner"


def test_user_is_social_false_removes_detected_event(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner w/ Sam", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    assert len(db.get_social_events_range("2026-07-14", "2026-07-20")) == 1
    db.set_event_overrides("ev1", {"user_is_social": False})
    assert db.get_social_events_range("2026-07-14", "2026-07-20") == []


def test_user_is_social_true_adds_non_social_event(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dentist", "2026-07-15T09:00:00-06:00", "2026-07-15T10:00:00-06:00")
    db.set_event_classification("ev1", False, 0.95)
    assert db.get_social_events_range("2026-07-14", "2026-07-20") == []
    db.set_event_overrides("ev1", {"user_is_social": True})
    rows = db.get_social_events_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["gcal_event_id"] == "ev1"


def test_set_event_overrides_amount_and_partial_update(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"amount": 45.5})
    row = db.get_event("ev1")
    assert row["amount"] == 45.5
    assert row["user_title"] is None  # untouched columns stay untouched
    db.set_event_overrides("ev1", {"user_title": "New name"})
    row = db.get_event("ev1")
    assert row["amount"] == 45.5  # still there — not clobbered by the second partial update
    assert row["user_title"] == "New name"


def test_delete_event(temp_db_path):
    db = _db(temp_db_path)
    db.add_manual_social_event("manual:del", "Cancelled plan", "2026-07-15T12:00:00", "2026-07-15T13:00:00")
    assert db.get_event("manual:del") is not None
    db.delete_event("manual:del")
    assert db.get_event("manual:del") is None


def test_get_classification_examples_only_overridden_newest_first_capped(temp_db_path):
    """Series-aware learning (2026-07-30 granularity spec): only recurring
    events (non-null recurring_event_id) with a confirmed, non-contradictory
    user verdict become examples."""
    db = _db(temp_db_path)
    for i in range(3):
        eid = f"ev{i}"
        db.upsert_calendar_event(
            eid, f"Event {i}", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00",
            recurring_event_id=f"series-{i}",
        )
        db.set_event_classification(eid, True, 0.9)
    # Only override some — others should never appear as examples.
    db.set_event_overrides("ev0", {"user_is_social": False})
    db.set_event_overrides("ev1", {"user_is_social": True})

    examples = db.get_classification_examples(limit=10)
    titles = [e["title"] for e in examples]
    assert "Event 2" not in titles  # never overridden
    assert examples[0]["title"] == "Event 1"  # newest override first
    assert examples[1]["title"] == "Event 0"
    # SQLite stores booleans as 0/1 — assert truthiness, not Python identity.
    assert bool(examples[0]["user_is_social"]) is True
    assert bool(examples[1]["user_is_social"]) is False


def test_get_classification_examples_respects_limit(temp_db_path):
    db = _db(temp_db_path)
    for i in range(5):
        eid = f"ev{i}"
        db.upsert_calendar_event(
            eid, f"Event {i}", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00",
            recurring_event_id=f"series-{i}",
        )
        db.set_event_classification(eid, True, 0.9)
        db.set_event_overrides(eid, {"user_is_social": True})
    examples = db.get_classification_examples(limit=2)
    assert len(examples) == 2


def test_get_classification_examples_excludes_one_off_events(temp_db_path):
    """A one-off (recurring_event_id IS NULL) override — exactly the
    "Didn't happen" poisoning scenario the spec fixes — must never appear as
    an example, no matter how it was answered."""
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Canceled Kickball", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"user_is_social": False})

    assert db.get_classification_examples(limit=10) == []


def test_get_classification_examples_excludes_contradictory_series(temp_db_path):
    """Two occurrences of the same series with disagreeing user verdicts —
    the whole series is excluded, not just the older answer."""
    db = _db(temp_db_path)
    db.upsert_calendar_event(
        "ev1", "Kickball week 1", "2026-07-08T19:00:00-06:00", "2026-07-08T21:00:00-06:00",
        recurring_event_id="kickball-series",
    )
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"user_is_social": True})

    db.upsert_calendar_event(
        "ev2", "Kickball week 2 (canceled)", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00",
        recurring_event_id="kickball-series",
    )
    db.set_event_classification("ev2", True, 0.9)
    db.set_event_overrides("ev2", {"user_is_social": False})

    assert db.get_classification_examples(limit=10) == []

    # A separate, agreeing series still contributes normally.
    db.upsert_calendar_event(
        "ev3", "Trivia night", "2026-07-16T19:00:00-06:00", "2026-07-16T21:00:00-06:00",
        recurring_event_id="trivia-series",
    )
    db.set_event_classification("ev3", True, 0.9)
    db.set_event_overrides("ev3", {"user_is_social": True})

    examples = db.get_classification_examples(limit=10)
    assert [e["title"] for e in examples] == ["Trivia night"]


def test_get_classification_examples_one_example_per_series_most_recent(temp_db_path):
    """An agreeing series with several confirmed occurrences contributes
    exactly one example: the most recent verdict."""
    db = _db(temp_db_path)
    for i, eid in enumerate(["ev1", "ev2", "ev3"]):
        db.upsert_calendar_event(
            eid, f"Book club {i}", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00",
            recurring_event_id="book-club-series",
        )
        db.set_event_classification(eid, True, 0.9)
        db.set_event_overrides(eid, {"user_is_social": True})

    examples = db.get_classification_examples(limit=10)
    assert len(examples) == 1
    assert examples[0]["title"] == "Book club 2"  # most recently inserted/overridden


# ── user_removed ("didn't happen" vs "not social") ──────────────────────────

def test_user_removed_excludes_event_from_day_and_range(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Kickball", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    assert len(db.get_events_for_day("2026-07-15")) == 1
    assert len(db.get_social_events_range("2026-07-14", "2026-07-20")) == 1

    db.set_event_overrides("ev1", {"user_removed": True})
    assert db.get_events_for_day("2026-07-15") == []
    assert db.get_social_events_range("2026-07-14", "2026-07-20") == []


def test_user_removed_does_not_flip_user_is_social(temp_db_path):
    """user_removed and user_is_social are separate columns — marking an
    occurrence removed must not touch (or fabricate) a classification
    verdict, unlike the old is_social=false 'Didn't happen' path."""
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Kickball", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"user_removed": True})
    row = db.get_event("ev1")
    assert row["user_is_social"] is None
    # Undo (removed: false) restores it to the day/range queries.
    db.set_event_overrides("ev1", {"user_removed": False})
    assert len(db.get_events_for_day("2026-07-15")) == 1


def test_calendar_events_migration_converts_prior_didnt_happen_rows(tmp_path, monkeypatch):
    """One-time data conversion, exercised against a genuinely pre-migration
    database (built by hand, missing user_removed/recurring_event_id) rather
    than temp_db_path, whose fixture already runs the current schema —
    otherwise there is nothing to "first add" and the guard's one-shot
    behavior can't actually be observed.

    A pre-migration row with source='gcal' AND user_is_social=false was
    necessarily a "Didn't happen" tap under the old mechanism (that was the
    only way to produce it) — the first initialize_db() that adds the
    column must reclassify it as user_removed=true, user_is_social=NULL."""
    import importlib
    import sqlite3

    db_path = str(tmp_path / "premigration.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("DATABASE_URL", "")
    import config
    importlib.reload(config)
    import database as db
    importlib.reload(db)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gcal_event_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            is_social INTEGER,
            confidence REAL,
            classified_at TIMESTAMP,
            user_title TEXT,
            user_is_social INTEGER,
            source TEXT DEFAULT 'gcal',
            amount REAL
        )
    """)
    conn.execute(
        "INSERT INTO calendar_events (gcal_event_id, title, start_at, end_at, is_social, "
        "user_is_social, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ev1", "Volo Kickball", "2026-07-22T19:00:00-06:00", "2026-07-22T21:00:00-06:00", 1, 0, "gcal"),
    )
    # A second gcal row with user_is_social=false in the same pre-migration
    # snapshot — the conversion sweep is blind to whether a given row was
    # "really" didn't-happen vs. a genuine not-social correction (the spec
    # accepts this: in prod exactly one such row existed, the canceled
    # 2026-07-22 Volo event, so the sweep's imprecision costs nothing there).
    # Both rows convert the same way; this asserts that unconditional
    # behavior rather than something narrower.
    conn.execute(
        "INSERT INTO calendar_events (gcal_event_id, title, start_at, end_at, is_social, "
        "user_is_social, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ev2", "Dentist", "2026-07-16T09:00:00-06:00", "2026-07-16T10:00:00-06:00", 0, 0, "gcal"),
    )
    conn.commit()
    conn.close()

    db.initialize_db()  # first call: user_removed didn't exist -> add + convert

    ev1 = db.get_event("ev1")
    assert bool(ev1["user_removed"]) is True
    assert ev1["user_is_social"] is None

    ev2 = db.get_event("ev2")
    assert bool(ev2["user_removed"]) is True
    assert ev2["user_is_social"] is None


def test_calendar_events_migration_data_conversion_is_one_shot(temp_db_path):
    """Once the column exists (temp_db_path's fixture already ran
    initialize_db once), a later initialize_db() must never re-run the
    conversion — a genuine post-migration 'not social' correction
    (user_removed explicitly False, user_is_social explicitly False) must
    survive untouched."""
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Book club", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"user_removed": False, "user_is_social": False})

    db.initialize_db()  # must be a no-op for this row — guard already tripped
    db.initialize_db()

    row = db.get_event("ev1")
    assert bool(row["user_is_social"]) is False
    assert bool(row["user_removed"]) is False


# ── Uncertain (ambiguity chip) ────────────────────────────────────────────────

def test_get_events_for_day_uncertain_below_threshold(temp_db_path):
    from metrics import AMBIGUOUS_CONFIDENCE
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Movie night", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", False, AMBIGUOUS_CONFIDENCE - 0.1)  # unresolved, low confidence, NOT-social lean
    rows = db.get_events_for_day("2026-07-15")
    assert len(rows) == 1  # surfaced despite the not-social lean
    assert rows[0]["uncertain"] is True
    assert rows[0]["is_social"] is False  # the lean is preserved, not flipped


def test_get_events_for_day_confident_at_threshold_not_uncertain(temp_db_path):
    from metrics import AMBIGUOUS_CONFIDENCE
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dentist", "2026-07-15T09:00:00-06:00", "2026-07-15T10:00:00-06:00")
    db.set_event_classification("ev1", False, AMBIGUOUS_CONFIDENCE)  # AT threshold: not "< threshold", so confident
    rows = db.get_events_for_day("2026-07-15")
    assert rows == []  # not-social lean, not uncertain -> excluded like before


def test_get_events_for_day_confident_above_threshold_not_uncertain(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.95)
    rows = db.get_events_for_day("2026-07-15")
    assert len(rows) == 1
    assert rows[0]["uncertain"] is False


def test_get_events_for_day_uncertain_flag_false_once_user_resolves(temp_db_path):
    from metrics import AMBIGUOUS_CONFIDENCE
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Movie night", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", False, AMBIGUOUS_CONFIDENCE - 0.1)
    db.set_event_overrides("ev1", {"user_is_social": True})  # user answers the chip
    rows = db.get_events_for_day("2026-07-15")
    assert len(rows) == 1
    assert rows[0]["uncertain"] is False
    assert rows[0]["is_social"] is True


def test_get_events_for_day_uncertain_excludes_removed(temp_db_path):
    """A removed occurrence never surfaces as uncertain even if it was
    low-confidence — user_removed wins over the ambiguity chip."""
    from metrics import AMBIGUOUS_CONFIDENCE
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Movie night", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, AMBIGUOUS_CONFIDENCE - 0.1)
    db.set_event_overrides("ev1", {"user_removed": True})
    assert db.get_events_for_day("2026-07-15") == []


def test_get_events_for_day_all_rows_carry_uncertain_key(temp_db_path):
    """Resolved/confident rows still carry uncertain: false, not an absent key."""
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev1", "Dinner", "2026-07-15T19:00:00-06:00", "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev1", True, 0.9)
    db.set_event_overrides("ev1", {"user_is_social": True})
    rows = db.get_events_for_day("2026-07-15")
    assert rows[0]["uncertain"] is False


# ── Rides ─────────────────────────────────────────────────────────────────────

def test_ride_add_and_fetch_by_range(temp_db_path):
    db = _db(temp_db_path)
    assert db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip", 14.50) is True
    rows = db.get_rides_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1
    row = rows[0]
    assert row["service"] == "Uber"
    assert row["ride_at"] == "2026-07-15T08:03:00-06:00"
    assert row["subject"] == "Your trip"
    assert row["amount"] == 14.50
    assert row["ai_is_work"] is None
    assert row["user_is_work"] is None
    assert "id" in row


def test_ride_range_excludes_outside_week(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-13T08:00:00-06:00", "2026-07-13T08:00", "s")  # Sunday before
    db.add_ride("r2", "Uber", "2026-07-14T08:00:00-06:00", "2026-07-14T08:00", "s")  # Monday
    assert len(db.get_rides_range("2026-07-14", "2026-07-20")) == 1


def test_has_ride_dedupes_by_message_id(temp_db_path):
    db = _db(temp_db_path)
    assert db.has_ride("r1") is False
    assert db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip") is True
    assert db.has_ride("r1") is True
    # Same message id again refuses to insert (truthy iff actually inserted).
    assert db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip") is False
    assert len(db.get_rides_range("2026-07-14", "2026-07-20")) == 1


def test_find_ride_by_key_hit_and_miss(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip", 14.50)
    found = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    assert found is not None
    assert found["amount"] == 14.50
    assert "id" in found
    assert db.find_ride_by_key("Uber", "2026-07-15T09:00") is None  # different key, miss
    assert db.find_ride_by_key("Lyft", "2026-07-15T08:03") is None  # different service, miss


def test_set_ride_amount_updates(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip")
    ride = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    db.set_ride_amount(ride["id"], 22.5)
    updated = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    assert updated["amount"] == 22.5


def test_set_ride_classification_sets_ai_fields(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip")
    ride = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    db.set_ride_classification(ride["id"], True, 0.85)
    row = db.get_rides_range("2026-07-14", "2026-07-20")[0]
    assert bool(row["ai_is_work"]) is True
    assert row["ai_confidence"] == 0.85
    assert row["user_is_work"] is None  # AI verdict never sets the user override


def test_set_ride_work_override_sets_user_verdict_and_reports_hit(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip")
    ride = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    assert db.set_ride_work_override(ride["id"], True) is True
    row = db.get_rides_range("2026-07-14", "2026-07-20")[0]
    assert bool(row["user_is_work"]) is True
    # 404-style "not found" — updating an unknown id reports no row touched.
    assert db.set_ride_work_override(999999, True) is False


def test_get_ride_examples_only_overridden_newest_first_capped(temp_db_path):
    db = _db(temp_db_path)
    for i in range(3):
        rid = f"r{i}"
        db.add_ride(rid, "Uber", f"2026-07-15T0{i}:00:00-06:00", f"2026-07-15T0{i}:00", f"Trip {i}")
    rides = db.get_rides_range("2026-07-14", "2026-07-20")
    by_subject = {r["subject"]: r["id"] for r in rides}
    # Only override some — Trip 2 must never appear as an example.
    db.set_ride_work_override(by_subject["Trip 0"], False)
    db.set_ride_work_override(by_subject["Trip 1"], True)

    examples = db.get_ride_examples(limit=10)
    subjects = [e["subject"] for e in examples]
    assert "Trip 2" not in subjects
    assert examples[0]["subject"] == "Trip 1"  # newest override first
    assert examples[1]["subject"] == "Trip 0"
    assert bool(examples[0]["user_is_work"]) is True
    assert bool(examples[1]["user_is_work"]) is False


def test_get_ride_examples_normalizes_bool_type(temp_db_path):
    """SQLite stores booleans as 0/1 ints — get_ride_examples must normalize like
    _ride_bool_rows does elsewhere in this layer, not leak raw ints to callers."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Trip")
    ride = db.find_ride_by_key("Uber", "2026-07-15T08:03")
    db.set_ride_work_override(ride["id"], True)
    examples = db.get_ride_examples()
    assert examples[0]["user_is_work"] is True  # real bool, not SQLite's 0/1 int


def test_get_ride_examples_respects_limit(temp_db_path):
    db = _db(temp_db_path)
    for i in range(5):
        rid = f"r{i}"
        db.add_ride(rid, "Uber", f"2026-07-15T0{i}:00:00-06:00", f"2026-07-15T0{i}:00", f"Trip {i}")
        ride = db.find_ride_by_key("Uber", f"2026-07-15T0{i}:00")
        db.set_ride_work_override(ride["id"], True)
    examples = db.get_ride_examples(limit=2)
    assert len(examples) == 2


def test_rides_migration_is_idempotent(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-15T08:03:00-06:00", "2026-07-15T08:03", "Your trip", 14.50)
    db.initialize_db()
    db.initialize_db()
    rows = db.get_rides_range("2026-07-14", "2026-07-20")
    assert len(rows) == 1 and rows[0]["amount"] == 14.50


# ── Effective date (night cutoff) — rides only ────────────────────────────────

def test_get_rides_range_buckets_late_night_ride_into_previous_day(temp_db_path):
    """A ride at 2026-07-25T02:34 belongs to 2026-07-24 — the SQL bucketing/
    filtering must agree with metrics.effective_date."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-25T02:40:00-06:00", "2026-07-25T02:34", "Late ride", 9.0)

    # The 25th's range must NOT include it...
    assert db.get_rides_range("2026-07-25", "2026-07-25") == []
    # ...the 24th's range must.
    rows = db.get_rides_range("2026-07-24", "2026-07-24")
    assert len(rows) == 1
    assert rows[0]["subject"] == "Late ride"


def test_get_rides_range_monday_early_morning_buckets_into_sunday_previous_week(temp_db_path):
    """Net effect on weekly bucketing: a Monday 01:00 ride belongs to Sunday —
    the previous week's range must include it, the Monday-starting week must not."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-20T01:10:00-06:00", "2026-07-20T01:00", "Monday early ride", 12.0)

    # Week of 2026-07-20 (Mon) .. 2026-07-26 (Sun) must NOT include it.
    assert db.get_rides_range("2026-07-20", "2026-07-26") == []
    # Week of 2026-07-13 (Mon) .. 2026-07-19 (Sun) must.
    rows = db.get_rides_range("2026-07-13", "2026-07-19")
    assert len(rows) == 1


def test_get_rides_range_exposes_resolved_ride_time(temp_db_path):
    """`ride_time` resolves to the TRUE ride time (parsed ride_key) rather than
    the raw email-arrival ride_at, so the frontend can display it."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-25T08:10:00-06:00", "2026-07-25T02:34", "Late ride", 9.0)
    row = db.get_rides_range("2026-07-24", "2026-07-24")[0]
    assert row["ride_time"] == "2026-07-25T02:34"
    assert row["ride_at"] == "2026-07-25T08:10:00-06:00"  # raw column unchanged


def test_get_rides_range_falls_back_to_ride_at_when_ride_key_not_iso(temp_db_path):
    """When ride_key parsing failed, it holds the "{day}|{subject}" fallback
    shape — ride_time must fall back to ride_at rather than exposing that
    non-timestamp string."""
    db = _db(temp_db_path)
    db.add_ride("r1", "Uber", "2026-07-25T08:10:00-06:00", "2026-07-25|Your trip", "Your trip", 9.0)
    row = db.get_rides_range("2026-07-25", "2026-07-25")[0]
    assert row["ride_time"] == "2026-07-25T08:10:00-06:00"


# ── Sessions ──────────────────────────────────────────────────────────────────

def test_session_create_get_delete_roundtrip(temp_db_path):
    db = _db(temp_db_path)
    assert db.get_session("tok1") is None
    db.create_session("tok1", "2026-07-01T00:00:00", "2026-07-15T00:00:00")
    row = db.get_session("tok1")
    assert row["created_at"] == "2026-07-01T00:00:00"
    assert row["expires_at"] == "2026-07-15T00:00:00"
    db.delete_session("tok1")
    assert db.get_session("tok1") is None


def test_delete_session_is_idempotent_for_unknown_token(temp_db_path):
    db = _db(temp_db_path)
    db.delete_session("never-existed")  # must not raise


def test_update_session_expiry(temp_db_path):
    db = _db(temp_db_path)
    db.create_session("tok1", "2026-07-01T00:00:00", "2026-07-15T00:00:00")
    db.update_session_expiry("tok1", "2026-07-29T00:00:00")
    assert db.get_session("tok1")["expires_at"] == "2026-07-29T00:00:00"


def test_delete_expired_sessions_only_removes_past_ones(temp_db_path):
    db = _db(temp_db_path)
    db.create_session("expired", "2026-06-01T00:00:00", "2026-06-15T00:00:00")
    db.create_session("still-valid", "2026-07-01T00:00:00", "2026-07-30T00:00:00")
    db.delete_expired_sessions("2026-07-10T00:00:00")
    assert db.get_session("expired") is None
    assert db.get_session("still-valid") is not None


# ── user_date (±1-day nudge) ──────────────────────────────────────────────────


def test_user_date_columns_exist(temp_db_path):
    # Migration adds a nullable user_date to both tables; a plain insert
    # leaves it NULL and the range queries tolerate it.
    db = _db(temp_db_path)
    db.add_delivery_order("ud-col-1", "Uber Eats", "2026-07-15T12:00:00-06:00", "Order", 10.0)
    with db._cursor() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(delivery_orders)").fetchall()]
        assert "user_date" in cols
        cols = [r["name"] for r in c.execute("PRAGMA table_info(rides)").fetchall()]
        assert "user_date" in cols


def test_delivery_night_cutoff_buckets_previous_day(temp_db_path):
    # 12:49 AM belongs to the previous day; the trailing -06:00 offset is
    # wall-clock metadata, never a conversion instruction.
    db = _db(temp_db_path)
    db.add_delivery_order("cut-1", "Uber Eats", "2026-07-30T00:49:00-06:00", "Your order", 28.21)
    rows = db.get_delivery_orders_range("2026-07-29", "2026-07-29")
    assert len(rows) == 1
    assert rows[0]["day"] == "2026-07-29"
    assert rows[0]["auto_day"] == "2026-07-29"
    assert db.get_delivery_orders_range("2026-07-30", "2026-07-30") == []


def test_delivery_at_cutoff_stays_on_its_day(temp_db_path):
    # 04:00 exactly is NOT before the cutoff.
    db = _db(temp_db_path)
    db.add_delivery_order("cut-2", "DoorDash", "2026-07-30T04:00:00-06:00", "Order", 12.0)
    assert db.get_delivery_orders_range("2026-07-30", "2026-07-30")[0]["day"] == "2026-07-30"


def test_delivery_user_date_wins_over_cutoff(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("cut-3", "Uber Eats", "2026-07-30T01:00:00-06:00", "Order", 15.0)
    row = db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]
    assert db.set_delivery_user_date(row["id"], "2026-07-30") is True
    assert db.get_delivery_orders_range("2026-07-29", "2026-07-29") == []
    got = db.get_delivery_orders_range("2026-07-30", "2026-07-30")[0]
    assert got["day"] == "2026-07-30"
    assert got["auto_day"] == "2026-07-29"   # automatic day still reported
    # Clearing restores the automatic day.
    db.set_delivery_user_date(row["id"], None)
    assert db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]["day"] == "2026-07-29"


def test_delivery_auto_day_helper(temp_db_path):
    db = _db(temp_db_path)
    db.add_delivery_order("cut-4", "Uber Eats", "2026-07-30T02:00:00-06:00", "Order", 9.0)
    row_id = db.get_delivery_orders_range("2026-07-29", "2026-07-29")[0]["id"]
    assert db.get_delivery_auto_day(row_id) == "2026-07-29"
    assert db.get_delivery_auto_day(999999) is None
    assert db.set_delivery_user_date(999999, "2026-07-29") is False


def test_ride_user_date_wins_over_cutoff(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("rud-1", "Uber", "2026-07-30T02:34:00-06:00",
                "2026-07-30T02:34:00", "Your trip", 27.82)
    row = db.get_rides_range("2026-07-29", "2026-07-29")[0]
    assert row["day"] == "2026-07-29" and row["auto_day"] == "2026-07-29"
    assert db.set_ride_user_date(row["id"], "2026-07-30") is True
    assert db.get_rides_range("2026-07-29", "2026-07-29") == []
    got = db.get_rides_range("2026-07-30", "2026-07-30")[0]
    assert got["day"] == "2026-07-30" and got["auto_day"] == "2026-07-29"


def test_ride_auto_day_helper(temp_db_path):
    db = _db(temp_db_path)
    db.add_ride("rud-2", "Uber", "2026-07-30T02:00:00-06:00",
                "2026-07-30T02:00:00", "Your trip", 10.0)
    ride_id = db.get_rides_range("2026-07-29", "2026-07-29")[0]["id"]
    assert db.get_ride_auto_day(ride_id) == "2026-07-29"
    assert db.get_ride_auto_day(999999) is None
    assert db.set_ride_user_date(999999, "2026-07-29") is False


# ── Date tracking + places (2026-07-30 spec) ─────────────────────────────────


def test_calendar_event_date_and_location_columns(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev-d1", "Date night", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="Bar Dough", is_date=True)
    row = db.get_event("ev-d1")
    assert bool(row["is_date"]) is True
    assert row["location"] == "Bar Dough"
    assert row["user_is_date"] is None
    assert row["user_location"] is None


def test_upsert_overwrites_gcal_columns_never_user_columns(temp_db_path):
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev-d2", "Dinner", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="Old Place", is_date=False)
    db.set_event_overrides("ev-d2", {"user_is_date": True, "user_location": "Corrected Venue"})
    # Re-upsert (a later scan) refreshes gcal-owned columns…
    db.upsert_calendar_event("ev-d2", "Dinner", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00", location="New Place", is_date=False)
    row = db.get_event("ev-d2")
    assert row["location"] == "New Place"
    # …but never the user's.
    assert bool(row["user_is_date"]) is True
    assert row["user_location"] == "Corrected Venue"


def test_manual_event_with_date_and_location(temp_db_path):
    db = _db(temp_db_path)
    db.add_manual_social_event("manual:d1", "Drinks", "2026-07-15T12:00:00",
                               "2026-07-15T13:00:00", 40.0, location="Wine Bar", is_date=True)
    row = db.get_event("manual:d1")
    assert bool(row["user_is_date"]) is True   # user-asserted, in the USER column
    assert row["is_date"] is None
    assert row["location"] == "Wine Bar"


def _seed_date_event(db, ev_id="ev-date", title="Date night", day="2026-07-15",
                     amount=60.0, location="Bar Dough"):
    db.upsert_calendar_event(ev_id, title, f"{day}T19:00:00-06:00",
                             f"{day}T21:00:00-06:00", location=location, is_date=True)
    db.set_event_classification(ev_id, True, 0.9)  # AI also thought it social
    if amount is not None:
        db.set_event_overrides(ev_id, {"amount": amount})


def test_social_range_excludes_resolved_dates(temp_db_path):
    db = _db(temp_db_path)
    _seed_date_event(db)
    db.upsert_calendar_event("ev-plain", "Trivia", "2026-07-15T19:00:00-06:00",
                             "2026-07-15T21:00:00-06:00")
    db.set_event_classification("ev-plain", True, 0.9)
    titles = [e["title"] for e in db.get_social_events_range("2026-07-14", "2026-07-20")]
    assert titles == ["Trivia"]
    # user_is_date=False un-dates a rule-flagged event — back into social.
    db.set_event_overrides("ev-date", {"user_is_date": False})
    titles = [e["title"] for e in db.get_social_events_range("2026-07-14", "2026-07-20")]
    assert sorted(titles) == ["Date night", "Trivia"]


def test_events_for_day_includes_dates_with_fields(temp_db_path):
    db = _db(temp_db_path)
    _seed_date_event(db)
    rows = db.get_events_for_day("2026-07-15")
    assert len(rows) == 1
    assert rows[0]["is_date"] is True
    assert rows[0]["location"] == "Bar Dough"
    # user_location wins over the gcal location.
    db.set_event_overrides("ev-date", {"user_location": "Actually Sunken City"})
    assert db.get_events_for_day("2026-07-15")[0]["location"] == "Actually Sunken City"


def test_events_for_day_includes_nonsocial_date(temp_db_path):
    # A date is shown on its day even if the AI said not-social with high
    # confidence — resolved date is an independent inclusion reason.
    db = _db(temp_db_path)
    db.upsert_calendar_event("ev-ns", "Date — museum", "2026-07-15T14:00:00-06:00",
                             "2026-07-15T16:00:00-06:00", is_date=True)
    db.set_event_classification("ev-ns", False, 0.95)
    rows = db.get_events_for_day("2026-07-15")
    assert [r["gcal_event_id"] for r in rows] == ["ev-ns"]
    assert rows[0]["is_date"] is True


def test_get_date_events_range(temp_db_path):
    db = _db(temp_db_path)
    _seed_date_event(db)                                       # in range, then removed
    _seed_date_event(db, ev_id="ev-date2", day="2026-07-25")   # outside range
    db.add_manual_social_event("manual:d2", "Drinks", "2026-07-16T12:00:00",
                               "2026-07-16T13:00:00", 40.0, location="Wine Bar", is_date=True)
    db.set_event_overrides("ev-date", {"user_removed": True})  # didn't happen
    rows = db.get_date_events_range("2026-07-14", "2026-07-20")
    assert [r["gcal_event_id"] for r in rows] == ["manual:d2"]
    assert rows[0]["location"] == "Wine Bar" and rows[0]["amount"] == 40.0


def test_dump_table_refuses_a_table_outside_the_allowlist(temp_db_path):
    import database as db
    import pytest

    with pytest.raises(ValueError):
        db.dump_table("bank_transactions")


def test_drop_table_refuses_a_table_outside_the_allowlist(temp_db_path):
    import database as db
    import pytest

    with pytest.raises(ValueError):
        db.drop_table("bank_transactions")


def test_dump_table_returns_rows_for_an_allowlisted_table(temp_db_path):
    import database as db

    assert db.dump_table("people") == []  # empty but readable
