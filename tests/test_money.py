"""app/money.py: the summary(weeks) aggregate over resolved bank flows.

Follows tests/test_database_bank.py's seeding conventions (upsert_bank_account /
upsert_bank_transaction / set_bank_transaction_derived / set_bank_flow_override) and
tests/test_scorecard.py's dynamic-today convention for anything that depends on
"this week" — money.summary() windows off app.scorecard._local_today(), so tests that
care about week boundaries compute dates relative to that, never hardcoded absolute
dates.
"""
from datetime import timedelta

import pytest


def _account(db, sfid="acct-1", role="spending"):
    db.upsert_bank_account(sfid, "Everyday Checking", "Wells Fargo", "checking")
    if role:
        db.set_bank_account_role(sfid, role)
    return next(a for a in db.get_bank_accounts() if a["simplefin_id"] == sfid)


def _txn(db, sfid, account_id, posted, amount, flow, pair_id=None, ambiguous=False, user_flow=None):
    db.upsert_bank_transaction(sfid, account_id, posted, posted, amount, "DESC", "", "", None)
    db.set_bank_transaction_derived(sfid, flow, pair_id, ambiguous)
    if user_flow is not None:
        db.set_bank_flow_override(sfid, user_flow)


def _this_monday(scorecard, metrics):
    return metrics.week_bounds(scorecard._local_today())[0]


# ── Double-count guard: a matched pair sums to one side, not both ──────────────

@pytest.mark.parametrize("flow", ["transfer", "card_payment", "investment"])
def test_matched_pair_reports_outflow_only_not_doubled(temp_db_path, flow):
    import database as db
    from app import scorecard
    import app.money as money

    acct_a = _account(db, "acct-a")
    acct_b = _account(db, "acct-b")
    today = scorecard._local_today().isoformat()
    _txn(db, "out1", acct_a["id"], today, -1000.0, flow, pair_id="p1")
    _txn(db, "in1", acct_b["id"], today, 1000.0, flow, pair_id="p1")

    result = money.summary(weeks=1)
    assert result["totals"][flow]["amount"] == 1000.0
    assert result["totals"][flow]["count"] == 2


# ── Override moves the money: aggregate reads resolved_flow, not bare flow ─────

def test_override_moves_money_out_of_spending_into_transfer(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _txn(db, "t1", acct["id"], today, -50.0, "spending")

    before = money.summary(weeks=1)
    assert before["spent"] == 50.0
    assert "transfer" not in before["totals"]

    db.set_bank_flow_override("t1", "transfer")

    after = money.summary(weeks=1)
    assert after["spent"] == 0
    assert after["spent"] != before["spent"]
    assert after["totals"]["transfer"]["amount"] == 50.0
    assert "spending" not in after["totals"]


# ── Weeks before coverage are absent, not zero ──────────────────────────────────

def test_weeks_before_coverage_are_absent_not_zero(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    txn_monday = this_monday - timedelta(weeks=3)
    _txn(db, "t1", acct["id"], txn_monday.isoformat(), -20.0, "spending")

    result = money.summary(weeks=12)
    assert len(result["weeks"]) <= 4
    assert result["weeks"][0]["week_start"] == txn_monday.isoformat()
    assert result["covered_from"] == txn_monday.isoformat()


# ── Partial flags ────────────────────────────────────────────────────────────

def test_first_week_partial_when_coverage_starts_midweek(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    first_monday = this_monday - timedelta(weeks=5)
    covered_from = first_monday + timedelta(days=2)  # Wednesday, mid-week
    _txn(db, "t1", acct["id"], covered_from.isoformat(), -20.0, "spending")

    result = money.summary(weeks=12)
    weeks = result["weeks"]
    assert weeks[0]["week_start"] == first_monday.isoformat()
    assert weeks[0]["partial"] is True
    assert weeks[-1]["week_start"] == this_monday.isoformat()
    assert weeks[-1]["partial"] is True  # last week always partial — in progress
    if len(weeks) > 2:
        assert weeks[1]["partial"] is False  # a fully-covered middle week


def test_first_week_not_partial_when_coverage_starts_exactly_monday(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    first_monday = this_monday - timedelta(weeks=5)
    _txn(db, "t1", acct["id"], first_monday.isoformat(), -20.0, "spending")

    result = money.summary(weeks=12)
    weeks = result["weeks"]
    assert weeks[0]["week_start"] == first_monday.isoformat()
    assert weeks[0]["partial"] is False
    assert weeks[-1]["partial"] is True  # still always partial, being in progress


# ── Empty table ──────────────────────────────────────────────────────────────

def test_empty_table_returns_shaped_payload(temp_db_path):
    import app.money as money

    result = money.summary(weeks=12)
    assert result == {
        "covered_from": None,
        "covered_to": None,
        "weeks": [],
        "totals": {},
        "spent": 0,
        "tracked": [],
        "triage_counts": {"ambiguous": 0, "inflow_unknown": 0},
    }


# ── tracked reuses app.scorecard.spend(), not a re-derivation ──────────────────

def test_tracked_reuses_scorecard_spend_and_excludes_confirmed_work_ride(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    today = scorecard._local_today().isoformat()
    db.add_ride("r1", "Uber", f"{today}T08:00:00", f"{today}T08:00", "Personal", 20.0)
    db.add_ride("r2", "Lyft", f"{today}T09:00:00", f"{today}T09:00", "Work trip", 40.0)
    rides = db.get_rides_range(today, today)
    work_id = next(r["id"] for r in rides if r["subject"] == "Work trip")
    db.set_ride_work_override(work_id, True)

    result = money.summary(weeks=1)
    tracked = result["tracked"]
    assert any(row["service"] == "Uber" and row["kind"] == "ride" for row in tracked)
    assert not any(row["service"] == "Lyft" for row in tracked)

    # Same figures as the existing scorecard helper for the identical window —
    # proof this is reuse, not a re-derivation.
    assert tracked == scorecard.spend(1)["by_service"]


# ── weeks clamps 1–52 ────────────────────────────────────────────────────────

def test_weeks_below_one_clamps_to_one(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    two_weeks_ago = this_monday - timedelta(weeks=2)
    _txn(db, "old", acct["id"], two_weeks_ago.isoformat(), -10.0, "spending")
    today = scorecard._local_today().isoformat()
    _txn(db, "new", acct["id"], today, -5.0, "spending")

    result = money.summary(weeks=0)
    assert len(result["weeks"]) == 1
    assert result["weeks"][0]["week_start"] == this_monday.isoformat()


def test_weeks_above_52_clamps_to_52(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    far_back_monday = this_monday - timedelta(weeks=60)
    _txn(db, "ancient", acct["id"], far_back_monday.isoformat(), -10.0, "spending")
    today = scorecard._local_today().isoformat()
    _txn(db, "recent", acct["id"], today, -5.0, "spending")

    result = money.summary(weeks=9999)
    # covered_from reflects the WHOLE table, unaffected by the clamp.
    assert result["covered_from"] == far_back_monday.isoformat()
    # but the weeks window itself is clamped to 52, so the 60-week-old week
    # never appears even though the table's coverage reaches back that far.
    assert len(result["weeks"]) == 52
    expected_first = this_monday - timedelta(weeks=51)
    assert result["weeks"][0]["week_start"] == expected_first.isoformat()
    # Pin the partial-flag reading: covered_from predates the window, so the
    # first surviving week is not partial (coverage began before the window starts)
    weeks = result["weeks"]
    assert weeks[0]["partial"] is False


# ── covered_to pin ──────────────────────────────────────────────────────────────

def test_covered_to_equals_latest_posted_date(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    twenty_days_ago = (scorecard._local_today() - timedelta(days=20)).isoformat()
    three_days_ago = (scorecard._local_today() - timedelta(days=3)).isoformat()

    # Seed two transactions in reverse chronological order to test the max() logic.
    _txn(db, "t1", acct["id"], twenty_days_ago, -50.0, "spending")
    _txn(db, "t2", acct["id"], three_days_ago, -30.0, "spending")

    result = money.summary(weeks=52)
    # covered_to should equal the LATER (more recent) of the two posted dates
    assert result["covered_to"] == three_days_ago


# ── triage_counts: ambiguous and inflow_unknown ─────────────────────────────────

def test_triage_counts_counts_unresolved_ambiguous_and_inflow_unknown(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()

    # (a) ambiguous with no user_flow: should be counted
    db.upsert_bank_transaction("ambig1", acct["id"], today, today, -20.0, "AMBIG TX", "", "", None)
    db.set_bank_transaction_derived("ambig1", "spending", None, True)

    # (b) ambiguous with user_flow set: should NOT be counted
    db.upsert_bank_transaction("ambig2", acct["id"], today, today, -30.0, "AMBIG TX 2", "", "", None)
    db.set_bank_transaction_derived("ambig2", "spending", None, True)
    db.set_bank_flow_override("ambig2", "transfer")

    # (c) inflow_unknown with no override: should be counted
    db.upsert_bank_transaction("inflow1", acct["id"], today, today, 100.0, "UNKNOWN INFLOW", "", "", None)
    db.set_bank_transaction_derived("inflow1", "inflow_unknown", None, False)

    result = money.summary(weeks=52)
    # Only one ambiguous (the unresolved one) and one inflow_unknown
    assert result["triage_counts"] == {"ambiguous": 1, "inflow_unknown": 1}


# ── triage(): row shape, label fallback, signature grouping ────────────────────

def test_triage_row_shape_and_label_fallback_from_payee_to_description(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()

    db.upsert_bank_transaction("ambig1", acct["id"], today, today, -20.0,
                                "AMBIG DESC", "AMBIG PAYEE", "", None)
    db.set_bank_transaction_derived("ambig1", "spending", None, True)

    db.upsert_bank_transaction("ambig2", acct["id"], today, today, -30.0,
                                "FALLBACK DESC", "", "", None)
    db.set_bank_transaction_derived("ambig2", "spending", None, True)

    result = money.triage(limit=50)
    rows = {r["simplefin_id"]: r for r in result["ambiguous"]}

    row1 = rows["ambig1"]
    assert set(row1.keys()) == {
        "simplefin_id", "posted", "amount", "payee", "description",
        "account_name", "resolved_flow", "user_flow", "user_note", "signature",
        "signature_count", "signature_amount", "label",
    }
    assert row1["label"] == "AMBIG PAYEE"  # payee present -> used verbatim

    row2 = rows["ambig2"]
    assert row2["payee"] == ""
    assert row2["label"] == "FALLBACK DESC"  # payee empty -> falls back to description


def test_signature_count_excludes_self_and_already_overridden_rows(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()

    # Three Venmo-signature ambiguous rows, all unanswered.
    db.upsert_bank_transaction("v1", acct["id"], today, today, -10.0, "VENMO PAYMENT", "", "", None)
    db.set_bank_transaction_derived("v1", "spending", None, True)
    db.upsert_bank_transaction("v2", acct["id"], today, today, -20.0, "VENMO PAYMENT", "", "", None)
    db.set_bank_transaction_derived("v2", "spending", None, True)
    db.upsert_bank_transaction("v3", acct["id"], today, today, -30.0, "VENMO PAYMENT", "", "", None)
    db.set_bank_transaction_derived("v3", "spending", None, True)

    result = money.triage(limit=50)
    rows = {r["simplefin_id"]: r for r in result["ambiguous"]}

    # v1's count/amount reflect v2 + v3 only -- itself is excluded.
    assert rows["v1"]["signature"] == "venmo"
    assert rows["v1"]["signature_count"] == 2
    assert rows["v1"]["signature_amount"] == 50.0

    # Overriding v3 removes it from the ambiguous queue entirely (db.get_bank_triage's
    # `user_flow IS NULL` predicate), so it must not inflate v1's remaining offer.
    db.set_bank_flow_override("v3", "transfer")
    result2 = money.triage(limit=50)
    rows2 = {r["simplefin_id"]: r for r in result2["ambiguous"]}
    assert rows2["v1"]["signature_count"] == 1
    assert rows2["v1"]["signature_amount"] == 20.0


def test_empty_signature_reports_zero_count_and_amount(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()

    db.upsert_bank_transaction("plain1", acct["id"], today, today, -15.0, "GROCERY STORE", "", "", None)
    db.set_bank_transaction_derived("plain1", "spending", None, True)
    db.upsert_bank_transaction("plain2", acct["id"], today, today, -25.0, "GROCERY STORE", "", "", None)
    db.set_bank_transaction_derived("plain2", "spending", None, True)

    result = money.triage(limit=50)
    rows = {r["simplefin_id"]: r for r in result["ambiguous"]}
    assert rows["plain1"]["signature"] == ""
    assert rows["plain1"]["signature_count"] == 0
    assert rows["plain1"]["signature_amount"] == 0


def test_recent_is_recently_sorted_capped(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()

    for i in range(5):
        sfid = f"r{i}"
        db.upsert_bank_transaction(sfid, acct["id"], today, today, -10.0 - i, f"TXN {i}", "", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)
        db.set_bank_flow_override(sfid, "spending")

    result = money.triage(limit=3)
    assert len(result["recent"]) == 3
    assert all(r["user_flow"] == "spending" for r in result["recent"])


# ── Refund netting ───────────────────────────────────────────────────────────

def test_refund_nets_out_of_spending_total(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _txn(db, "spend1", acct["id"], today, -100.0, "spending")
    _txn(db, "refund1", acct["id"], today, 30.0, "spending", user_flow="refund")

    result = money.summary(weeks=1)
    assert result["spent"] == 70.0
    assert result["totals"]["refund"] == {"count": 1, "amount": 30.0}


def test_refund_nets_within_its_own_posted_week_same_week(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _txn(db, "spend1", acct["id"], today, -100.0, "spending")
    _txn(db, "refund1", acct["id"], today, 30.0, "spending", user_flow="refund")

    result = money.summary(weeks=1)
    assert result["weeks"][-1]["spending"] == 70.0


def test_refund_in_different_week_nets_that_week_not_the_spending_week(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    this_monday = _this_monday(scorecard, metrics)
    last_monday = this_monday - timedelta(weeks=1)
    _txn(db, "spend1", acct["id"], last_monday.isoformat(), -100.0, "spending")
    _txn(db, "refund1", acct["id"], this_monday.isoformat(), 30.0, "spending", user_flow="refund")

    result = money.summary(weeks=2)
    weeks = {w["week_start"]: w["spending"] for w in result["weeks"]}
    # The spending week is untouched by a refund posted in a different week.
    assert weeks[last_monday.isoformat()] == 100.0
    # The refund's own (later) week nets to negative -- no spending of its own.
    assert weeks[this_monday.isoformat()] == -30.0
    # Total spent still reflects the whole-window net.
    assert result["spent"] == 70.0


def test_week_net_goes_negative_when_refunds_exceed_spending(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _txn(db, "spend1", acct["id"], today, -20.0, "spending")
    _txn(db, "refund1", acct["id"], today, 50.0, "spending", user_flow="refund")

    result = money.summary(weeks=1)
    # The backend reports the true negative net -- flooring is the chart's job.
    assert result["weeks"][-1]["spending"] == -30.0


def test_inert_mistap_negative_amount_refund_contributes_to_neither(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _txn(db, "spend1", acct["id"], today, -100.0, "spending")
    _txn(db, "mistap", acct["id"], today, -15.0, "spending", user_flow="refund")

    result = money.summary(weeks=1)
    assert result["spent"] == 100.0
    assert result["totals"]["refund"] == {"count": 0, "amount": 0.0}


def test_round_once_avoids_double_rounding_drift_in_spent(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    # Chosen so round(33.335, 2) - round(0.005, 2) == 33.330000000000005 (a
    # real float artifact) while a single round of the final difference lands
    # exactly on 33.33 -- proves the subtraction is rounded once, at the end.
    _txn(db, "spend1", acct["id"], today, -33.335, "spending")
    _txn(db, "refund1", acct["id"], today, 0.005, "spending", user_flow="refund")

    result = money.summary(weeks=1)
    assert result["spent"] == 33.33


# ── Reviewer findings: round-once spent, sign-filtered spending term ───────────

def test_spent_rounds_once_from_raw_sums_not_from_already_rounded_totals(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    # round(10.007, 2) - round(0.003, 2) == round(10.01, 2) - round(0.0, 2) == 10.01
    # (double-rounded, wrong). round(10.007 - 0.003, 2) == round(10.004, 2) == 10.0
    # (round-once, right, and matches the weekly path below).
    _txn(db, "spend1", acct["id"], today, -10.007, "spending")
    _txn(db, "refund1", acct["id"], today, 0.003, "spending", user_flow="refund")

    result = money.summary(weeks=1)
    assert result["spent"] == 10.0
    # Must also agree with the weekly path, which rounds the raw per-week sums once.
    assert result["spent"] == round(sum(w["spending"] for w in result["weeks"]), 2)


def test_spending_term_excludes_positive_amount_rows_everywhere(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _txn(db, "spend1", acct["id"], today, -100.0, "spending")
    # A positive-amount row resolved to "spending" via override -- reachable through
    # the API since the route only validates flow membership, not sign.
    _txn(db, "over1", acct["id"], today, 40.0, "income", user_flow="spending")

    result = money.summary(weeks=1)
    assert result["spent"] == 100.0
    assert result["totals"]["spending"] == {"count": 1, "amount": 100.0}
    assert result["weeks"][-1]["spending"] == 100.0


# ── triage() rows carry user_note ───────────────────────────────────────────

def test_triage_rows_carry_user_note(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()

    db.upsert_bank_transaction("ambig1", acct["id"], today, today, -20.0,
                                "AMBIG DESC", "AMBIG PAYEE", "", None)
    db.set_bank_transaction_derived("ambig1", "spending", None, True)

    db.upsert_bank_transaction("sorted1", acct["id"], today, today, -10.0,
                                "SORTED DESC", "", "", None)
    db.set_bank_transaction_derived("sorted1", "spending", None, False)
    db.set_bank_flow_override("sorted1", "spending", note="explained already")

    result = money.triage(limit=50)

    ambiguous = {r["simplefin_id"]: r for r in result["ambiguous"]}
    assert "user_note" in ambiguous["ambig1"]
    assert ambiguous["ambig1"]["user_note"] is None

    recent = {r["simplefin_id"]: r for r in result["recent"]}
    assert recent["sorted1"]["user_note"] == "explained already"


def test_limit_clamps_to_one_and_two_hundred(temp_db_path, monkeypatch):
    import database as db
    import app.money as money

    captured = {}

    def fake_triage(limit):
        captured["triage_limit"] = limit
        return {"ambiguous": [], "inflow_unknown": []}

    def fake_recent(limit):
        captured["recent_limit"] = limit
        return []

    monkeypatch.setattr(db, "get_bank_triage", fake_triage)
    monkeypatch.setattr(db, "get_bank_recently_sorted", fake_recent)

    money.triage(limit=0)
    assert captured["triage_limit"] == 1
    assert captured["recent_limit"] == 1

    money.triage(limit=99999)
    assert captured["triage_limit"] == 200
    assert captured["recent_limit"] == 200


# ── suggested_flow is never read by any aggregate ───────────────────────────

def test_summary_ignores_suggested_flow(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _txn(db, "spend1", acct["id"], today, -100.0, "spending")
    _txn(db, "income1", acct["id"], today, 500.0, "income")
    _txn(db, "amb1", acct["id"], today, -20.0, "spending", ambiguous=True)

    before = money.summary(weeks=12)

    written = db.set_bank_suggestions_bulk({
        "spend1": "spending", "income1": "income", "amb1": "spending",
    })
    assert written == 3

    after = money.summary(weeks=12)
    assert after == before


# ── breakdown(): vendor aggregation ────────────────────────────────────────────

def _vendor_txn(db, sfid, account_id, posted, amount, payee, flow="spending",
                user_flow=None, description="RAW DESC"):
    db.upsert_bank_transaction(sfid, account_id, posted, posted, amount,
                               description, payee, "", None)
    db.set_bank_transaction_derived(sfid, flow, None, False)
    if user_flow is not None:
        db.set_bank_flow_override(sfid, user_flow)


def test_breakdown_groups_by_payee_and_sorts_by_net_desc(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct["id"], today, -30.0, "Amazon")
    _vendor_txn(db, "a2", acct["id"], today, -20.0, "Amazon")
    _vendor_txn(db, "u1", acct["id"], today, -40.0, "Uber")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines == [
        {"vendor": "Amazon", "count": 2, "amount": 50.0},
        {"vendor": "Uber", "count": 1, "amount": 40.0},
    ]


def test_breakdown_nets_refunds_into_vendor_line_and_keeps_negative_net(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r1", acct["id"], today, -100.0, "REI")
    _vendor_txn(db, "r2", acct["id"], today, 30.0, "REI", flow="spending",
                user_flow="refund")
    # A refund-only vendor in the window: negative net, still listed, last.
    _vendor_txn(db, "z1", acct["id"], today, 25.0, "Zappos", flow="spending",
                user_flow="refund")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines[0] == {"vendor": "REI", "count": 1, "amount": 70.0}
    assert lines[-1] == {"vendor": "Zappos", "count": 0, "amount": -25.0}


def test_breakdown_excludes_non_spending_flows_and_positive_spending_rows(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "t1", acct["id"], today, -500.0, "Vanguard", flow="transfer")
    _vendor_txn(db, "i1", acct["id"], today, 2000.0, "Payroll", flow="income")
    # Mis-signed spending row (positive) is inert, mirroring _totals().
    _vendor_txn(db, "s1", acct["id"], today, 15.0, "Oddity")

    assert money.breakdown(weeks=1)["lines"] == []


def test_breakdown_account_filter(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct_a = _account(db, "acct-a")
    acct_b = _account(db, "acct-b")
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct_a["id"], today, -10.0, "Amazon")
    _vendor_txn(db, "b1", acct_b["id"], today, -99.0, "Uber")

    lines = money.breakdown(weeks=1, account_id=acct_a["id"])["lines"]
    assert lines == [{"vendor": "Amazon", "count": 1, "amount": 10.0}]


def test_breakdown_vendor_key_falls_back_to_description_when_payee_empty(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "e1", acct["id"], today, -12.0, "", description="CHECK 1042")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines == [{"vendor": "CHECK 1042", "count": 1, "amount": 12.0}]


def test_breakdown_window_excludes_older_weeks(temp_db_path):
    import database as db
    from app import scorecard
    import metrics
    import app.money as money

    acct = _account(db)
    monday = _this_monday(scorecard, metrics)
    old = (monday - timedelta(weeks=2)).isoformat()
    _vendor_txn(db, "old1", acct["id"], old, -50.0, "Amazon")
    _vendor_txn(db, "new1", acct["id"], monday.isoformat(), -10.0, "Amazon")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines == [{"vendor": "Amazon", "count": 1, "amount": 10.0}]


def test_breakdown_rounds_once_per_group(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    # Three thirds that each round to 3.33 but sum to 10.0 raw.
    for i, amt in enumerate((-3.334, -3.333, -3.333)):
        _vendor_txn(db, f"c{i}", acct["id"], today, amt, "Cafe")

    lines = money.breakdown(weeks=1)["lines"]
    assert lines == [{"vendor": "Cafe", "count": 3, "amount": 10.0}]


# ── breakdown_rows(): per-vendor drill-down ────────────────────────────────────

def test_breakdown_rows_returns_vendor_rows_newest_first(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money
    import metrics

    acct = _account(db)
    today = scorecard._local_today()
    # Both dates must land inside the SAME Mon-Sun week as the weeks=1 query
    # window, or the older row falls into the previous week and vanishes from
    # the result. Deriving them from the week's start rather than from "today
    # minus one day" is what makes this hold on a Monday too -- it did not,
    # and this test failed every Monday and passed the other six days.
    week_start, _ = metrics.week_bounds(today)
    d1, d0 = week_start.isoformat(), (week_start + timedelta(days=1)).isoformat()
    _vendor_txn(db, "a1", acct["id"], d1, -30.0, "Amazon")
    _vendor_txn(db, "a2", acct["id"], d0, -20.0, "Amazon")
    _vendor_txn(db, "a3", acct["id"], d0, 5.0, "Amazon", user_flow="refund")
    _vendor_txn(db, "u1", acct["id"], d0, -40.0, "Uber")

    rows = money.breakdown_rows(weeks=1, vendor="Amazon")["rows"]
    assert [r["simplefin_id"] for r in rows] == ["a3", "a2", "a1"]
    assert rows[0]["resolved_flow"] == "refund"
    assert set(rows[0]) == {"simplefin_id", "posted", "amount", "account_name",
                            "resolved_flow", "user_note", "user_label",
                            "suggested_label", "user_no_label", "vendor"}


def test_breakdown_rows_respects_account_filter_and_limit_clamp(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct_a = _account(db, "acct-a")
    acct_b = _account(db, "acct-b")
    today = scorecard._local_today().isoformat()
    for i in range(3):
        _vendor_txn(db, f"a{i}", acct_a["id"], today, -10.0, "Amazon")
    _vendor_txn(db, "b1", acct_b["id"], today, -10.0, "Amazon")

    rows = money.breakdown_rows(weeks=1, vendor="Amazon",
                                account_id=acct_a["id"])["rows"]
    assert len(rows) == 3

    # limit clamps low to 1 (the same 1-200 clamp triage uses)
    rows = money.breakdown_rows(weeks=1, vendor="Amazon", limit=0)["rows"]
    assert len(rows) == 1


def test_breakdown_rows_excludes_other_flows_for_same_vendor(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "v1", acct["id"], today, -900.0, "Vanguard", flow="investment")

    assert money.breakdown_rows(weeks=1, vendor="Vanguard")["rows"] == []


# ── breakdown(by="label") and label-filtered rows ──────────────────────────────

def test_breakdown_by_label_groups_with_unlabeled_bucket_last(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r1", acct["id"], today, -2000.0, "Check")
    db.set_bank_label("r1", "Monthly Rent")
    _vendor_txn(db, "g1", acct["id"], today, -40.0, "Kroger")
    _vendor_txn(db, "g2", acct["id"], today, -60.0, "Safeway")
    for sfid in ("g1", "g2"):
        db.set_bank_label(sfid, "Groceries")
    _vendor_txn(db, "u1", acct["id"], today, -15.0, "Mystery")   # unlabeled

    result = money.breakdown(weeks=1, by="label")
    assert result["lines"] == [
        {"label": "Monthly Rent", "count": 1, "amount": 2000.0, "suggested": 0},
        {"label": "Groceries", "count": 2, "amount": 100.0, "suggested": 0},
        {"label": None, "count": 1, "amount": 15.0, "suggested": 0},
    ]
    assert result["labels"] == ["Groceries", "Monthly Rent"]


def test_breakdown_label_and_vendor_views_sum_identically(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct["id"], today, -30.0, "Amazon")
    db.set_bank_label("a1", "Household")
    _vendor_txn(db, "a2", acct["id"], today, 10.0, "Amazon", user_flow="refund")
    _vendor_txn(db, "u1", acct["id"], today, -5.0, "Cafe")

    by_vendor = money.breakdown(weeks=1)["lines"]
    by_label = money.breakdown(weeks=1, by="label")["lines"]
    assert sum(line["amount"] for line in by_vendor) == sum(line["amount"] for line in by_label)


def test_breakdown_vendor_mode_now_carries_vocabulary(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct["id"], today, -30.0, "Amazon")
    db.set_bank_label("a1", "Household")

    result = money.breakdown(weeks=1)
    assert result["labels"] == ["Household"]
    assert result["lines"][0]["vendor"] == "Amazon"


def test_breakdown_rows_by_label(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "a1", acct["id"], today, -30.0, "Amazon")
    _vendor_txn(db, "k1", acct["id"], today, -40.0, "Kroger")
    for sfid in ("a1", "k1"):
        db.set_bank_label(sfid, "Household")
    _vendor_txn(db, "u1", acct["id"], today, -5.0, "Cafe")

    rows = money.breakdown_rows(weeks=1, label="Household")["rows"]
    assert sorted(r["simplefin_id"] for r in rows) == ["a1", "k1"]
    assert all(r["user_label"] == "Household" for r in rows)


# ── Phase 3: suggestions resolve into the label view, user label wins ──────────

def test_breakdown_by_label_includes_suggestions_via_resolved(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r1", acct["id"], today, -2000.0, "Check")
    _vendor_txn(db, "r2", acct["id"], today, -2000.0, "Check")
    db.set_bank_label("r1", "Monthly Rent")
    db.set_bank_label_suggestions_bulk({"r2": "Monthly Rent"})

    lines = money.breakdown(weeks=1, by="label")["lines"]
    assert lines[0] == {"label": "Monthly Rent", "count": 2, "amount": 4000.0, "suggested": 1}


def test_breakdown_rows_label_filter_matches_resolved_and_carries_vendor(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r2", acct["id"], today, -2000.0, "Check")
    db.set_bank_label_suggestions_bulk({"r2": "Monthly Rent"})

    rows = money.breakdown_rows(weeks=1, label="Monthly Rent")["rows"]
    assert [r["simplefin_id"] for r in rows] == ["r2"]
    assert rows[0]["suggested_label"] == "Monthly Rent"
    assert rows[0]["user_label"] is None
    assert rows[0]["vendor"] == "Check"


def test_breakdown_rows_rejected_suggestion_carries_flag_and_drops_from_label_filter(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r3", acct["id"], today, -2000.0, "Check")
    db.set_bank_label_suggestions_bulk({"r3": "Monthly Rent"})
    db.set_bank_no_label("r3")

    # A rejected suggestion's resolved_label is NULL, so it never matches
    # a label= filter — the label vanishes from the breakdown row too.
    assert money.breakdown_rows(weeks=1, label="Monthly Rent")["rows"] == []

    rows = money.breakdown_rows(weeks=1, vendor="Check")["rows"]
    assert [r["simplefin_id"] for r in rows] == ["r3"]
    assert rows[0]["user_no_label"] is True
    assert rows[0]["suggested_label"] == "Monthly Rent"
    assert rows[0]["user_label"] is None


# ── Label audit list + suggested badges ────────────────────────────────────────

def test_label_suggestions_list_shape_and_total(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "r1", acct["id"], today, -25.0, "Amex")
    db.set_bank_label_suggestions_bulk({"r1": "Amex Benefit"})

    result = money.label_suggestions(limit=10)
    assert result["total"] == 1
    row = result["rows"][0]
    assert row == {
        "simplefin_id": "r1", "posted": today, "amount": -25.0,
        "vendor": "Amex", "account_name": row["account_name"],
        "suggested_label": "Amex Benefit", "description": "RAW DESC",
    }


def test_breakdown_label_lines_carry_suggested_counts(temp_db_path):
    import database as db
    from app import scorecard
    import app.money as money

    acct = _account(db)
    today = scorecard._local_today().isoformat()
    _vendor_txn(db, "u1", acct["id"], today, -10.0, "Check")
    db.set_bank_label("u1", "Rent")
    _vendor_txn(db, "u2", acct["id"], today, -10.0, "Check")
    db.set_bank_label_suggestions_bulk({"u2": "Rent"})
    _vendor_txn(db, "x1", acct["id"], today, -5.0, "Cafe")

    lines = money.breakdown(weeks=1, by="label")["lines"]
    rent = next(line for line in lines if line["label"] == "Rent")
    unlabeled = next(line for line in lines if line["label"] is None)
    assert rent["count"] == 2 and rent["suggested"] == 1
    assert unlabeled["suggested"] == 0

    # vendor mode: no `suggested` key at all
    vlines = money.breakdown(weeks=1)["lines"]
    assert all("suggested" not in line for line in vlines)


def _fake_holdings_payload():
    return {"accounts": [
        {"id": "inv-1", "name": "ROTH IRA (9304)", "org": {"name": "Fidelity"},
         "holdings": [
             {"symbol": "VOO", "description": "Vanguard 500", "shares": "2",
              "cost_basis": "800.00", "market_value": "1000.00"},
             {"symbol": "GIFT", "description": "No basis", "shares": "1",
              "market_value": "50.00"},                         # null basis
         ]},
        {"id": "inv-2", "name": "401K (4542)", "org": {"name": "Fidelity"},
         "holdings": [
             {"symbol": "TDF", "description": "Target date", "shares": "10",
              "cost_basis": "2000.00", "market_value": "1900.00"},
         ]},
    ]}


def test_investments_math_null_basis_and_sorting(temp_db_path, monkeypatch):
    import database as db
    from app import money
    from services import simplefin_service
    db.upsert_bank_account("inv-1", "ROTH IRA (9304)", "Fidelity", "investment")
    db.set_bank_account_nickname("inv-1", "Roth")
    monkeypatch.setattr(simplefin_service, "is_configured", lambda: True)
    monkeypatch.setattr(simplefin_service, "fetch_accounts",
                        lambda days=None: _fake_holdings_payload())

    out = money.investments()
    # accounts sorted by market value desc: 401k (1900) before Roth (1050)
    assert [a["simplefin_id"] for a in out["accounts"]] == ["inv-2", "inv-1"]
    roth = out["accounts"][1]
    assert roth["name"] == "Roth"                       # nickname resolution
    assert roth["market_value"] == 1050.0               # null-basis mv still counts
    assert roth["cost_basis"] == 800.0                  # null-basis excluded
    assert roth["gain"] == 200.0 and roth["gain_pct"] == 25.0
    voo = roth["holdings"][0]                           # holdings sorted mv desc
    assert voo["symbol"] == "VOO" and voo["gain"] == 200.0 and voo["gain_pct"] == 25.0
    gift = roth["holdings"][1]
    assert gift["gain"] is None and gift["gain_pct"] is None and gift["cost_basis"] is None
    tdf = out["accounts"][0]["holdings"][0]
    assert tdf["gain"] == -100.0 and tdf["gain_pct"] == -5.0
    total = out["total"]
    assert total["market_value"] == 2950.0
    assert total["cost_basis"] == 2800.0
    assert total["gain"] == 100.0
    assert total["gain_pct"] == 3.6                     # round((2900-2800)/2800*100, 1)


def test_investments_not_configured_returns_empty(temp_db_path, monkeypatch):
    from app import money
    from services import simplefin_service
    monkeypatch.setattr(simplefin_service, "is_configured", lambda: False)
    assert money.investments() == {"total": None, "accounts": []}
