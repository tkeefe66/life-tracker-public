"""scan_gmail job: rules first, AI only on ambiguous, dedupe by message id."""


def _candidates():
    return [
        {"gmail_message_id": "m1", "sender": "a@uber.com", "subject": "Your order from Pete's",
         "ordered_at": "2026-07-15T19:30:00-06:00"},
        {"gmail_message_id": "m2", "sender": "a@uber.com", "subject": "Your Tuesday trip with Uber",
         "ordered_at": "2026-07-15T08:00:00-06:00"},
        {"gmail_message_id": "m3", "sender": "a@uber.com", "subject": "Update on your recent request",
         "ordered_at": "2026-07-16T12:00:00-06:00"},
    ]


def test_scan_stores_orders_and_uses_ai_for_ambiguous(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", _candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    scan_gmail.run()

    stored = db.get_delivery_orders_range("2026-07-13", "2026-07-19")
    assert {r["gmail_message_id"] for r in stored} == {"m1", "m3"}  # m2 is a ride
    assert ai_calls == ["Update on your recent request"]  # AI only for the ambiguous one
    assert db.get_setting("gmail_last_status") == "ok"
    # m2 lands in rides, not delivery_orders.
    rides = db.get_rides_range("2026-07-13", "2026-07-19")
    assert len(rides) == 1 and rides[0]["subject"] == "Your Tuesday trip with Uber"


def test_scan_skips_already_seen(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    db.add_delivery_order("m1", "Uber Eats", "2026-07-15T19:30:00-06:00", "Your order from Pete's")
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", _candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or False)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    scan_gmail.run()
    assert len(db.get_delivery_orders_range("2026-07-13", "2026-07-19")) == 1  # no dupes


def test_scan_records_error_status(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    def boom():
        raise RuntimeError("token expired")
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", boom)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)

    scan_gmail.run()  # must not raise
    assert db.get_setting("gmail_last_status").startswith("error:")


def test_scan_noop_when_unconfigured(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: False)
    scan_gmail.run()
    assert db.get_setting("gmail_last_status") == "error: Google not configured"


def test_query_uses_lookback_default():
    from services import gmail_service
    assert "newer_than:7d" in gmail_service._query()
    assert "from:(" in gmail_service._query()


def test_scan_writes_last_result(temp_db_path, mock_anthropic, monkeypatch):
    import database as db
    from jobs import scan_gmail

    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [
        {"gmail_message_id": "m1", "sender": "noreply@doordash.com",
         "subject": "Order Confirmation for Tom", "ordered_at": "2026-07-20T18:00:00"},
    ])

    scan_gmail.run()

    assert db.get_setting("gmail_last_status") == "ok"
    result = db.get_setting("gmail_last_result")
    assert result is not None
    assert result.startswith("1 candidates")
    assert "new orders" in result


def test_scan_skips_followup_emails(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    triplet = [
        {"gmail_message_id": "o1", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T21:01:00-06:00",
         "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's"},
        {"gmail_message_id": "o2", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T22:01:00-06:00",
         "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's"},
        {"gmail_message_id": "o3", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T22:30:00-06:00",
         "snippet": "Refunded Just a quick update, Tom We adjusted the total"},
    ]
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: triplet)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or True)

    scan_gmail.run()
    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["o1"]
    assert ai_calls == []  # follow-ups skipped by rules; o1's subject rule-matches "order"


def test_tip_only_order_creates_row(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    candidates = [
        {"gmail_message_id": "t1", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T22:01:00-06:00",
         "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's Total $16.31"},
    ]
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda s, subj, snip="": ai_calls.append(subj) or True)

    scan_gmail.run()
    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["t1"]
    assert stored[0]["amount"] == 16.31
    assert ai_calls == []  # tip-only order never goes through AI


def test_order_then_tip_updates_amount_to_tip_total(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    order = {"gmail_message_id": "o1", "sender": "noreply@uber.com",
              "subject": "Your Monday evening order with Uber Eats",
              "ordered_at": "2026-07-15T21:01:00-06:00",
              "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's Total $40.00"}
    tip = {"gmail_message_id": "t1", "sender": "noreply@uber.com",
           "subject": "Your Monday evening order with Uber Eats",
           "ordered_at": "2026-07-15T22:01:00-06:00",
           "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's Total $45.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt", lambda *a, **k: True)

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [order])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [tip])
    scan_gmail.run()

    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["o1"]  # tip does not create a second row
    assert stored[0]["amount"] == 45.00  # tip total wins over order total


def test_tip_then_order_no_double_count(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    tip = {"gmail_message_id": "t1", "sender": "noreply@uber.com",
           "subject": "Your Monday evening order with Uber Eats",
           "ordered_at": "2026-07-15T22:01:00-06:00",
           "snippet": "Tip Thanks for tipping, Tom Here's your receipt for Oblio's Total $45.00"}
    order = {"gmail_message_id": "o1", "sender": "noreply@uber.com",
              "subject": "Your Monday evening order with Uber Eats",
              "ordered_at": "2026-07-15T21:01:00-06:00",
              "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's Total $40.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt", lambda *a, **k: True)

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [tip])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [order])
    scan_gmail.run()

    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert len(stored) == 1
    assert stored[0]["gmail_message_id"] == "t1"
    assert stored[0]["amount"] == 45.00  # later order receipt is skipped, no overwrite


def test_refund_followup_updates_amount(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    order = {"gmail_message_id": "o1", "sender": "noreply@uber.com",
              "subject": "Your Monday evening order with Uber Eats",
              "ordered_at": "2026-07-15T21:01:00-06:00",
              "snippet": "Thanks for ordering, Tom Here's your receipt for Oblio's Total $40.00"}
    refund = {"gmail_message_id": "r1", "sender": "noreply@uber.com",
              "subject": "Your Monday evening order with Uber Eats",
              "ordered_at": "2026-07-15T22:30:00-06:00",
              "snippet": "Refunded Just a quick update, Tom We adjusted the total Total $32.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt", lambda *a, **k: True)

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [order])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [refund])
    scan_gmail.run()

    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert [r["gmail_message_id"] for r in stored] == ["o1"]
    assert stored[0]["amount"] == 32.00


def test_same_day_different_dayparts_two_rows(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    candidates = [
        {"gmail_message_id": "o1", "sender": "noreply@uber.com",
         "subject": "Your Monday morning order with Uber Eats",
         "ordered_at": "2026-07-15T09:01:00-06:00",
         "snippet": "Thanks for ordering, Tom Here's your receipt Total $12.00"},
        {"gmail_message_id": "o2", "sender": "noreply@uber.com",
         "subject": "Your Monday evening order with Uber Eats",
         "ordered_at": "2026-07-15T21:01:00-06:00",
         "snippet": "Thanks for ordering, Tom Here's your receipt Total $40.00"},
    ]
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt", lambda *a, **k: True)

    scan_gmail.run()
    stored = db.get_delivery_orders_range("2026-07-14", "2026-07-16")
    assert {r["gmail_message_id"] for r in stored} == {"o1", "o2"}


# ── Rides ─────────────────────────────────────────────────────────────────────

def test_ride_candidate_stored_as_ride_not_delivery_order(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    ride = {"gmail_message_id": "r1", "sender": "noreply@uber.com",
            "subject": "Your Tuesday morning trip with Uber",
            "ordered_at": "2026-07-14T08:05:00-06:00",
            "snippet": "Jul 14, 2026 8:03 AM Thanks for riding Total $18.50"}
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [ride])
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.1})
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_receipt",
                        lambda *a, **k: ai_calls.append(a) or False)

    scan_gmail.run()

    assert db.get_delivery_orders_range("2026-07-13", "2026-07-19") == []
    rides = db.get_rides_range("2026-07-13", "2026-07-19")
    assert len(rides) == 1
    assert rides[0]["service"] == "Uber"
    assert rides[0]["amount"] == 18.50
    assert bool(rides[0]["ai_is_work"]) is False
    assert ai_calls == []  # rule-classified ride never goes through the delivery AI path


def test_delivery_candidate_still_becomes_order_alongside_rides(temp_db_path, monkeypatch):
    """Delivery behavior must not regress once ride routing is added."""
    import database as db
    from jobs import scan_gmail

    candidates = [
        {"gmail_message_id": "d1", "sender": "noreply@doordash.com",
         "subject": "Order Confirmation for Tom", "ordered_at": "2026-07-14T18:00:00-06:00",
         "snippet": "Thanks for ordering Total $22.00"},
        {"gmail_message_id": "r1", "sender": "noreply@uber.com",
         "subject": "Your Tuesday morning trip with Uber", "ordered_at": "2026-07-14T08:05:00-06:00",
         "snippet": "Jul 14, 2026 8:03 AM Thanks for riding"},
    ]
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: candidates)
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    scan_gmail.run()

    orders = db.get_delivery_orders_range("2026-07-13", "2026-07-19")
    assert [o["gmail_message_id"] for o in orders] == ["d1"]
    assert orders[0]["amount"] == 22.00
    rides = db.get_rides_range("2026-07-13", "2026-07-19")
    assert len(rides) == 1


def test_ride_cluster_dedupe_by_ride_time_not_subject(temp_db_path, monkeypatch):
    """Critical correctness: Uber sends a 'charge summary' and a 'Thanks for riding'
    receipt for the SAME trip with an identical subject — they must dedupe to one
    ride keyed on the parsed ride timestamp, not the subject, with amount updated."""
    import database as db
    from jobs import scan_gmail

    receipt = {"gmail_message_id": "ride-receipt", "sender": "noreply@uber.com",
               "subject": "Your Sunday morning trip with Uber",
               "ordered_at": "2026-07-19T04:10:00-06:00",
               "snippet": "Jul 19, 2026 4:03 AM Thanks for riding with Uber Total $14.00"}
    charge_summary = {"gmail_message_id": "ride-charge", "sender": "noreply@uber.com",
                       "subject": "Your Sunday morning trip with Uber",
                       "ordered_at": "2026-07-19T04:15:00-06:00",
                       "snippet": "Jul 19, 2026 4:03 AM charge summary Total $16.50"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [receipt])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [charge_summary])
    scan_gmail.run()

    rides = db.get_rides_range("2026-07-18", "2026-07-20")
    assert len(rides) == 1  # one ride, not two, despite the identical subject
    assert rides[0]["amount"] == 16.50  # later email's total wins


def test_two_distinct_rides_same_morning_identical_subject_two_rows(temp_db_path, monkeypatch):
    """Critical correctness: two SEPARATE trips the same morning can share an
    identical subject line — they must NOT collapse into one ride. The ride
    timestamp parsed from each snippet is the only thing that distinguishes them."""
    import database as db
    from jobs import scan_gmail

    trip1 = {"gmail_message_id": "trip1", "sender": "noreply@uber.com",
             "subject": "Your Sunday morning trip with Uber",
             "ordered_at": "2026-07-19T07:05:00-06:00",
             "snippet": "Jul 19, 2026 7:02 AM Thanks for riding Total $9.00"}
    trip2 = {"gmail_message_id": "trip2", "sender": "noreply@uber.com",
             "subject": "Your Sunday morning trip with Uber",
             "ordered_at": "2026-07-19T09:20:00-06:00",
             "snippet": "Jul 19, 2026 9:17 AM Thanks for riding Total $11.00"}
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [trip1, trip2])
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    scan_gmail.run()

    rides = db.get_rides_range("2026-07-18", "2026-07-20")
    assert len(rides) == 2
    assert {r["amount"] for r in rides} == {9.00, 11.00}


def test_ride_amount_later_email_wins_within_single_run(temp_db_path, monkeypatch):
    """Gmail's messages.list returns newest-first, so the genuinely later (by
    timestamp) email must win the amount even when it's processed FIRST in the run."""
    import database as db
    from jobs import scan_gmail

    receipt = {"gmail_message_id": "ride-receipt", "sender": "noreply@uber.com",
               "subject": "Your Sunday morning trip with Uber",
               "ordered_at": "2026-07-19T04:10:00-06:00",
               "snippet": "Jul 19, 2026 4:03 AM Thanks for riding with Uber Total $14.00"}
    charge_summary = {"gmail_message_id": "ride-charge", "sender": "noreply@uber.com",
                       "subject": "Your Sunday morning trip with Uber",
                       "ordered_at": "2026-07-19T04:15:00-06:00",
                       "snippet": "Jul 19, 2026 4:03 AM charge summary Total $16.50"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})
    # Newest-first: charge_summary (04:15) is listed before receipt (04:10), as
    # Gmail's messages.list actually returns them.
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [charge_summary, receipt])

    scan_gmail.run()

    rides = db.get_rides_range("2026-07-18", "2026-07-20")
    assert len(rides) == 1
    assert rides[0]["amount"] == 16.50  # genuinely later email wins, not processing order


def test_ride_amount_stable_across_repeated_scans(temp_db_path, monkeypatch):
    """The losing (older) candidate's message id is never recorded as its own ride
    row, so it re-enters the ride branch on every scan within the lookback window.
    It must not re-pin its (wrong) amount on a repeat scan."""
    import database as db
    from jobs import scan_gmail

    receipt = {"gmail_message_id": "ride-receipt", "sender": "noreply@uber.com",
               "subject": "Your Sunday morning trip with Uber",
               "ordered_at": "2026-07-19T04:10:00-06:00",
               "snippet": "Jul 19, 2026 4:03 AM Thanks for riding with Uber Total $14.00"}
    charge_summary = {"gmail_message_id": "ride-charge", "sender": "noreply@uber.com",
                       "subject": "Your Sunday morning trip with Uber",
                       "ordered_at": "2026-07-19T04:15:00-06:00",
                       "snippet": "Jul 19, 2026 4:03 AM charge summary Total $16.50"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [charge_summary, receipt])

    scan_gmail.run()
    first = db.get_rides_range("2026-07-18", "2026-07-20")[0]["amount"]
    scan_gmail.run()  # both messages are still in the lookback window on a repeat scan
    second = db.get_rides_range("2026-07-18", "2026-07-20")[0]["amount"]

    assert first == second == 16.50


def test_ride_fallback_key_dedupes_without_amount_in_key(temp_db_path, monkeypatch):
    """When the ride timestamp can't be parsed from the snippet, the fallback key
    must not include amount — two duplicate emails for the same trip carrying
    different totals (receipt vs adjusted charge summary) must dedupe to ONE ride."""
    import database as db
    from jobs import scan_gmail

    receipt = {"gmail_message_id": "ride-receipt", "sender": "noreply@uber.com",
               "subject": "Your Sunday morning trip with Uber",
               "ordered_at": "2026-07-19T04:10:00-06:00",
               "snippet": "Thanks for riding with Uber Total $14.00"}  # no parseable ride time
    charge_summary = {"gmail_message_id": "ride-charge", "sender": "noreply@uber.com",
                       "subject": "Your Sunday morning trip with Uber",
                       "ordered_at": "2026-07-19T04:15:00-06:00",
                       "snippet": "charge summary Total $16.50"}  # no parseable ride time
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [charge_summary, receipt])

    scan_gmail.run()

    rides = db.get_rides_range("2026-07-18", "2026-07-20")
    assert len(rides) == 1  # dedupes on day|subject, not day|subject|amount
    assert rides[0]["amount"] == 16.50  # genuinely later email still wins


def test_scan_writes_last_result_includes_ride_count(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [
        {"gmail_message_id": "r1", "sender": "noreply@lyft.com",
         "subject": "Your ride with Lyft", "ordered_at": "2026-07-20T18:00:00",
         "snippet": "Jul 20, 2026 6:00 PM Thanks for riding"},
    ])

    scan_gmail.run()

    result = db.get_setting("gmail_last_result")
    assert result is not None
    assert "1 new rides" in result


def test_ride_dedupe_skip_when_already_stored(temp_db_path, monkeypatch):
    """has_ride guard prevents re-processing an already-ingested ride message."""
    import database as db
    from jobs import scan_gmail

    ride = {"gmail_message_id": "r1", "sender": "noreply@uber.com",
            "subject": "Your Tuesday morning trip with Uber",
            "ordered_at": "2026-07-14T08:05:00-06:00",
            "snippet": "Jul 14, 2026 8:03 AM Thanks for riding Total $18.50"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: ai_calls.append(1) or {"is_work": False, "confidence": 0.0})
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [ride])

    scan_gmail.run()
    scan_gmail.run()

    assert len(db.get_rides_range("2026-07-13", "2026-07-19")) == 1
    assert len(ai_calls) == 1  # classification only happens once, on first ingestion


def test_ride_three_email_chain_single_run_latest_wins(temp_db_path, monkeypatch):
    """Charge summary, receipt, and a later tip adjustment for one trip arrive in
    a single scan, newest-first (as Gmail's messages.list actually returns them).
    Exactly one ride must be stored, carrying the chronologically latest amount."""
    import database as db
    from jobs import scan_gmail

    receipt = {"gmail_message_id": "ride-receipt", "sender": "noreply@uber.com",
               "subject": "Your Sunday morning trip with Uber",
               "ordered_at": "2026-07-19T04:10:00-06:00",
               "snippet": "Jul 19, 2026 4:03 AM Thanks for riding with Uber Total $14.00"}
    charge_summary = {"gmail_message_id": "ride-charge", "sender": "noreply@uber.com",
                       "subject": "Your Sunday morning trip with Uber",
                       "ordered_at": "2026-07-19T04:15:00-06:00",
                       "snippet": "Jul 19, 2026 4:03 AM charge summary Total $16.50"}
    tip_adjustment = {"gmail_message_id": "ride-tip", "sender": "noreply@uber.com",
                       "subject": "Your Sunday morning trip with Uber",
                       "ordered_at": "2026-07-19T04:20:00-06:00",
                       "snippet": "Jul 19, 2026 4:03 AM Tip adjustment Total $18.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})
    # Newest-first, exactly as Gmail's messages.list returns them.
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates",
                        lambda: [tip_adjustment, charge_summary, receipt])

    scan_gmail.run()

    rides = db.get_rides_range("2026-07-18", "2026-07-20")
    assert len(rides) == 1
    assert rides[0]["amount"] == 18.00  # chronologically latest email's amount wins


def test_ride_later_scan_with_newer_email_overwrites_amount(temp_db_path, monkeypatch):
    """A genuinely newer email for an already-stored ride, arriving in a later
    scan, must overwrite the stored amount."""
    import database as db
    from jobs import scan_gmail

    receipt = {"gmail_message_id": "ride-receipt", "sender": "noreply@uber.com",
               "subject": "Your Sunday morning trip with Uber",
               "ordered_at": "2026-07-19T04:10:00-06:00",
               "snippet": "Jul 19, 2026 4:03 AM Thanks for riding with Uber Total $14.00"}
    tip_adjustment = {"gmail_message_id": "ride-tip", "sender": "noreply@uber.com",
                       "subject": "Your Sunday morning trip with Uber",
                       "ordered_at": "2026-07-19T04:20:00-06:00",
                       "snippet": "Jul 19, 2026 4:03 AM Tip adjustment Total $18.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [receipt])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [tip_adjustment])
    scan_gmail.run()

    rides = db.get_rides_range("2026-07-18", "2026-07-20")
    assert len(rides) == 1
    assert rides[0]["amount"] == 18.00  # newer email's amount overwrote the stored one


def test_ride_at_immutable_when_followup_lands_on_next_calendar_day(temp_db_path, monkeypatch):
    """Regression guard: a follow-up email for the same ride that happens to be
    timestamped the next calendar day must still win the amount comparison, but
    must NOT move the stored ride_at — otherwise the ride silently re-buckets
    into a different day/week in get_rides_range."""
    import database as db
    from jobs import scan_gmail

    receipt = {"gmail_message_id": "ride-receipt", "sender": "noreply@uber.com",
               "subject": "Your Sunday night trip with Uber",
               "ordered_at": "2026-07-19T23:50:00-06:00",
               "snippet": "Jul 19, 2026 11:47 PM Thanks for riding with Uber Total $14.00"}
    # Same trip's tip adjustment lands 15 minutes later, past local midnight.
    tip_adjustment = {"gmail_message_id": "ride-tip", "sender": "noreply@uber.com",
                       "subject": "Your Sunday night trip with Uber",
                       "ordered_at": "2026-07-20T00:05:00-06:00",
                       "snippet": "Jul 19, 2026 11:47 PM Tip adjustment Total $18.00"}
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [receipt])
    scan_gmail.run()
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [tip_adjustment])
    scan_gmail.run()

    rides = db.get_rides_range("2026-07-19", "2026-07-19")
    assert len(rides) == 1  # ride still buckets into its original day
    assert rides[0]["amount"] == 18.00  # tip adjustment's amount still won
    assert rides[0]["ride_at"].startswith("2026-07-19")  # ride_at never moved to the 20th


# ── Cancellation-fee detection (label only) ──────────────────────────────────

_CANCELLATION_SNIPPET = (
    "Jul 25, 2026 1:52 AM Canceled Jul 25, 2026 , 1:52 AM We'll connect "
    "another time, Tom Here's the receipt for your canceled trip. "
    "Total $5.65 A fee is charged if there is a cancelation after the"
)


def test_cancellation_ride_flagged_at_insert_time(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    ride = {"gmail_message_id": "r1", "sender": "noreply@uber.com",
            "subject": "Your Saturday night trip with Uber",
            "ordered_at": "2026-07-25T01:55:00-06:00",
            "snippet": _CANCELLATION_SNIPPET}
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [ride])
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    scan_gmail.run()

    rides = db.get_rides_range("2026-07-24", "2026-07-25")
    assert len(rides) == 1
    assert bool(rides[0]["is_cancellation"]) is True
    # Label only: still a ride, still counts, still carries its amount/spend.
    assert rides[0]["amount"] == 5.65


def test_completed_ride_not_flagged_as_cancellation(temp_db_path, monkeypatch):
    import database as db
    from jobs import scan_gmail

    ride = {"gmail_message_id": "r1", "sender": "noreply@uber.com",
            "subject": "Your Saturday night trip with Uber",
            "ordered_at": "2026-07-25T02:40:00-06:00",
            "snippet": "Jul 25, 2026 2:34 AM This is your charge summary "
                       "This document acknowledges your trip completion. Total $27.82"}
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [ride])
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    scan_gmail.run()

    rides = db.get_rides_range("2026-07-24", "2026-07-25")
    assert len(rides) == 1
    assert bool(rides[0]["is_cancellation"]) is False


def test_already_seen_ride_backfills_null_cancellation_flag(temp_db_path, monkeypatch):
    """The scan currently `continue`s once has_ride is true — this must not
    stay a dead end for rows that predate the is_cancellation column: on the
    next sighting of the same message, a NULL flag gets derived and set."""
    import database as db
    from jobs import scan_gmail

    db.add_ride("r1", "Uber", "2026-07-25T01:55:00-06:00", "2026-07-25T01:52",
                "Your Saturday night trip with Uber", 5.65)  # is_cancellation left NULL
    ride = {"gmail_message_id": "r1", "sender": "noreply@uber.com",
            "subject": "Your Saturday night trip with Uber",
            "ordered_at": "2026-07-25T01:55:00-06:00",
            "snippet": _CANCELLATION_SNIPPET}
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [ride])
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    ai_calls = []
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: ai_calls.append(1) or {"is_work": False, "confidence": 0.0})

    scan_gmail.run()

    rides = db.get_rides_range("2026-07-24", "2026-07-25")
    assert len(rides) == 1  # no duplicate row created
    assert bool(rides[0]["is_cancellation"]) is True
    assert ai_calls == []  # backfill path never re-runs work classification


def test_already_flagged_ride_not_reclassified_on_backfill(temp_db_path, monkeypatch):
    """Once is_cancellation is set (not NULL), the backfill must leave it alone
    even if scanned again — only a NULL flag triggers the derive-and-set path."""
    import database as db
    from jobs import scan_gmail

    db.add_ride("r1", "Uber", "2026-07-25T01:55:00-06:00", "2026-07-25T01:52",
                "Your Saturday night trip with Uber", 5.65, is_cancellation=False)
    ride = {"gmail_message_id": "r1", "sender": "noreply@uber.com",
            "subject": "Your Saturday night trip with Uber",
            "ordered_at": "2026-07-25T01:55:00-06:00",
            "snippet": _CANCELLATION_SNIPPET}  # would derive True if re-evaluated
    monkeypatch.setattr(scan_gmail, "fetch_delivery_candidates", lambda: [ride])
    monkeypatch.setattr(scan_gmail.google_auth, "is_configured", lambda: True)
    monkeypatch.setattr(scan_gmail.ai_metrics, "classify_work_ride",
                        lambda *a, **k: {"is_work": False, "confidence": 0.0})

    scan_gmail.run()

    rides = db.get_rides_range("2026-07-24", "2026-07-25")
    assert bool(rides[0]["is_cancellation"]) is False  # untouched, stayed False


def test_query_uses_union_of_delivery_and_ride_domains():
    from services import gmail_service
    q = gmail_service._query()
    assert "uber.com" in q
    assert "lyft.com" in q
    assert "doordash.com" in q


def test_fetch_includes_trash_and_snippet(monkeypatch):
    from services import gmail_service

    captured = {}

    class FakeReq:
        def __init__(self, result):
            self._r = result

        def execute(self):
            return self._r

    class FakeMessages:
        def list(self, **kw):
            captured.update(kw)
            return FakeReq({"messages": [{"id": "x1"}]})

        def get(self, **kw):
            return FakeReq({
                "payload": {"headers": [
                    {"name": "From", "value": "noreply@uber.com"},
                    {"name": "Subject", "value": "Your order"},
                ]},
                "internalDate": "1753200000000",
                "snippet": "Thanks for ordering",
            })

    class FakeUsers:
        def messages(self):
            return FakeMessages()

    class FakeService:
        def users(self):
            return FakeUsers()

    monkeypatch.setattr(gmail_service, "_get_service", lambda: FakeService())
    out = gmail_service.fetch_delivery_candidates()
    assert captured["includeSpamTrash"] is True
    assert out[0]["snippet"] == "Thanks for ordering"
