"""bank_accounts / bank_transactions: upserts preserve user data, COALESCE resolves in SQL."""
import pytest


def _seed_account(db, sfid="acct-1", role=None):
    """Seed a bank account and return its db id."""
    db.upsert_bank_account(sfid, "Everyday Checking", "Wells Fargo", "checking")
    if role:
        db.set_bank_account_role(sfid, role)
    return next(a for a in db.get_bank_accounts() if a["simplefin_id"] == sfid)["id"]


def _seed_txn(db, simplefin_id, account_id, posted, amount):
    """Seed a bank transaction."""
    db.upsert_bank_transaction(simplefin_id, account_id, posted, posted, amount,
                               f"Description for {simplefin_id}", f"Payee for {simplefin_id}", "", None)
    db.set_bank_transaction_derived(simplefin_id, "spending", None, False)


def _account(db, sfid="acct-1", role=None):
    db.upsert_bank_account(sfid, "Everyday Checking", "Wells Fargo", "checking")
    if role:
        db.set_bank_account_role(sfid, role)
    return next(a for a in db.get_bank_accounts() if a["simplefin_id"] == sfid)


def test_new_account_defaults_to_unknown_role(temp_db_path):
    import database as db
    acct = _account(db)
    assert acct["role"] == "unknown"
    assert acct["active"] is True


def test_account_upsert_refreshes_name_but_never_role(temp_db_path):
    import database as db
    _account(db, role="spending")
    db.upsert_bank_account("acct-1", "RENAMED CHECKING", "Wells Fargo", "checking")
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-1")
    assert acct["name"] == "RENAMED CHECKING"
    assert acct["role"] == "spending"  # user data survives the sync


def test_transaction_upsert_updates_amount_but_never_user_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "PENDING COFFEE", "Coffee", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    db.set_bank_flow_override("t1", "transfer")

    # Pending transaction settles: amount and description change.
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-02", "2026-07-01",
                               -12.5, "COFFEE SHOP #4", "Coffee", "", "5814")

    rows = db.get_bank_transactions_range("2026-06-01", "2026-08-01")
    assert len(rows) == 1
    assert rows[0]["amount"] == -12.5
    assert rows[0]["description"] == "COFFEE SHOP #4"
    assert rows[0]["user_flow"] == "transfer"       # untouched
    assert rows[0]["resolved_flow"] == "transfer"   # COALESCE, computed in SQL


def test_resolved_flow_falls_back_to_derived_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "COFFEE", "Coffee", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    rows = db.get_bank_transactions_range("2026-06-01", "2026-08-01")
    assert rows[0]["resolved_flow"] == "spending"
    assert rows[0]["user_flow"] is None


def test_ambiguous_round_trips_as_a_real_bool(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)
    rows = db.get_bank_transactions_range("2026-06-01", "2026-08-01")
    assert rows[0]["ambiguous"] is True  # not 1 — SQLite ints must not leak to the API


def test_flow_override_returns_false_for_unknown_id(temp_db_path):
    import database as db
    assert db.set_bank_flow_override("nope", "transfer") is False


def test_bulk_derived_write_commits_all_rows_together(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "A", "", "", None)
    db.upsert_bank_transaction("t2", acct["id"], "2026-07-02", "2026-07-01",
                               20.0, "B", "", "", None)

    db.set_bank_transactions_derived_bulk([
        ("t1", "transfer", "t1", False),
        ("t2", "transfer", "t1", False),
    ])

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["t1"]["flow"] == "transfer"
    assert rows["t2"]["flow"] == "transfer"
    assert rows["t1"]["pair_id"] == rows["t2"]["pair_id"] == "t1"


def test_bulk_derived_write_is_atomic_an_exception_partway_leaves_no_rows_updated(temp_db_path):
    """A review found the per-row version leaves a hole: if a run is interrupted
    partway, one half of a matched pair keeps its pair_id and the other goes free
    — and it does not self-heal on the next sync. The bulk write must commit
    all-or-nothing."""
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "A", "", "", None)
    db.upsert_bank_transaction("t2", acct["id"], "2026-07-02", "2026-07-01",
                               20.0, "B", "", "", None)

    # The second item's pair_id is a list — sqlite3 (and psycopg2) cannot bind an
    # unsupported parameter type, so execute() raises partway through the loop.
    items = [
        ("t1", "transfer", "t1", False),
        ("t2", "transfer", ["not", "a", "valid", "param"], False),
    ]
    with pytest.raises(Exception):
        db.set_bank_transactions_derived_bulk(items)

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["t1"]["flow"] is None  # the first row's write was rolled back too
    assert rows["t2"]["flow"] is None


def test_balances_are_never_stored(temp_db_path):
    """The most sensitive field stays out of the database by construction."""
    import database as db
    with db._cursor() as c:
        if db.USE_POSTGRES:
            c.execute("SELECT column_name AS name FROM information_schema.columns "
                      "WHERE table_name = 'bank_accounts'")
        else:
            c.execute("PRAGMA table_info(bank_accounts)")
        cols = {r["name"] for r in c.fetchall()}
    assert not (cols & {"balance", "available_balance"})


# ── Triage queries and the bulk override writer ────────────────────────────────

def test_triage_returns_ambiguous_row_with_no_user_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)

    triage = db.get_bank_triage(50)
    assert [r["simplefin_id"] for r in triage["ambiguous"]] == ["t1"]
    assert triage["ambiguous"][0]["ambiguous"] is True
    assert triage["ambiguous"][0]["user_flow"] is None


def test_reappearing_queue_trap_resync_after_override_does_not_reopen(temp_db_path):
    """spec §6.4. Without the `user_flow IS NULL` predicate the third assertion
    fails and the queue is uncleanable — this is the most important test in the
    feature."""
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)

    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]
    assert "t1" in ids

    db.set_bank_flow_override("t1", "transfer")
    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]
    assert "t1" not in ids

    # Simulate the next sync's classification pass recomputing ambiguous = true
    # from scratch (it reads `flow`, not `resolved_flow`, so it has no idea a
    # user already ruled on this row).
    db.set_bank_transaction_derived("t1", "spending", None, True)
    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]
    assert "t1" not in ids


def test_clearing_override_returns_row_to_the_queue(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)
    db.set_bank_flow_override("t1", "transfer")
    assert "t1" not in [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]

    db.set_bank_flow_override("t1", None)
    assert "t1" in [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]


def test_inflow_unknown_bucket_keyed_on_resolved_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               500.0, "MYSTERY DEPOSIT", "", "", None)
    db.set_bank_transaction_derived("t1", "inflow_unknown", None, False)
    db.upsert_bank_transaction("t2", acct["id"], "2026-07-02", "2026-07-02",
                               600.0, "MYSTERY DEPOSIT 2", "", "", None)
    db.set_bank_transaction_derived("t2", "inflow_unknown", None, False)
    db.set_bank_flow_override("t2", "income")

    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["inflow_unknown"]]
    assert ids == ["t1"]  # t2's resolved_flow is "income", not inflow_unknown


def test_triage_ordering_newest_posted_first_tie_broken_by_simplefin_id(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    for sfid, posted in [("b", "2026-07-01"), ("a", "2026-07-01"), ("c", "2026-07-03")]:
        db.upsert_bank_transaction(sfid, acct["id"], posted, posted, -10.0, "X", "", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, True)

    ids = [r["simplefin_id"] for r in db.get_bank_triage(50)["ambiguous"]]
    assert ids == ["c", "a", "b"]  # newest posted first; 07-01 tie broken a < b


def test_triage_limit_caps_each_bucket_independently(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    for i in range(3):
        sfid = f"amb-{i}"
        db.upsert_bank_transaction(sfid, acct["id"], f"2026-07-0{i+1}", f"2026-07-0{i+1}",
                                   -10.0, "X", "", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, True)
    for i in range(3):
        sfid = f"inf-{i}"
        db.upsert_bank_transaction(sfid, acct["id"], f"2026-07-0{i+1}", f"2026-07-0{i+1}",
                                   500.0, "Y", "", "", None)
        db.set_bank_transaction_derived(sfid, "inflow_unknown", None, False)

    triage = db.get_bank_triage(2)
    assert len(triage["ambiguous"]) == 2
    assert len(triage["inflow_unknown"]) == 2


def test_recently_sorted_only_user_flow_rows_newest_first_capped(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("no-override", acct["id"], "2026-07-05", "2026-07-05",
                               -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("no-override", "spending", None, False)

    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, False)
    db.set_bank_flow_override("t1", "transfer")

    db.upsert_bank_transaction("t2", acct["id"], "2026-07-03", "2026-07-03",
                               -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("t2", "spending", None, False)
    db.set_bank_flow_override("t2", "income")

    rows = db.get_bank_recently_sorted(50)
    assert [r["simplefin_id"] for r in rows] == ["t2", "t1"]  # newest posted first, no-override absent

    capped = db.get_bank_recently_sorted(1)
    assert [r["simplefin_id"] for r in capped] == ["t2"]


def test_bulk_flow_override_updates_and_counts_skips_unknown_ids(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.upsert_bank_transaction("b", acct["id"], "2026-07-02", "2026-07-02", -10.0, "X", "", "", None)

    count = db.set_bank_flow_overrides_bulk(["a", "b", "nope"], "transfer")
    assert count == 2

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] == "transfer"
    assert rows["b"]["user_flow"] == "transfer"


def test_bulk_flow_override_unknown_flow_raises_before_any_write(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "spending", None, False)
    db.set_bank_flow_override("a", "transfer")

    with pytest.raises(ValueError):
        db.set_bank_flow_overrides_bulk(["a"], "not-a-real-flow")

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] == "transfer"  # unchanged — nothing written


def test_bulk_flow_override_none_clears(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "spending", None, False)
    db.set_bank_flow_override("a", "transfer")

    count = db.set_bank_flow_overrides_bulk(["a"], None)
    assert count == 1

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] is None


# ── refund flow + user_note ────────────────────────────────────────────────────

def test_refund_is_a_valid_override_flow(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", 40.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "inflow_unknown", None, False)

    updated = db.set_bank_flow_override("a", "refund")
    assert updated is True

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] == "refund"
    assert rows["a"]["resolved_flow"] == "refund"


def test_set_bank_flow_override_writes_flow_and_note_together(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", 40.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "inflow_unknown", None, False)

    db.set_bank_flow_override("a", "refund", note="  Amex return — shoes  ")

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] == "refund"
    assert rows["a"]["user_note"] == "Amex return — shoes"


def test_set_bank_flow_override_omitted_note_preserves_stored_note(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", 40.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "inflow_unknown", None, False)
    db.set_bank_flow_override("a", "refund", note="Amex return")

    # Put-back: flow cleared, note not mentioned — must survive.
    db.set_bank_flow_override("a", None)

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] is None
    assert rows["a"]["user_note"] == "Amex return"


def test_set_bank_flow_override_empty_note_clears_it(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", 40.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "inflow_unknown", None, False)
    db.set_bank_flow_override("a", "refund", note="Amex return")

    db.set_bank_flow_override("a", "income", note="")

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["user_flow"] == "income"
    assert rows["a"]["user_note"] is None


def test_user_note_key_present_and_none_by_default(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "spending", None, False)

    rows = db.get_bank_transactions_range("2026-06-01", "2026-08-01")
    assert "user_note" in rows[0]
    assert rows[0]["user_note"] is None


# ── suggested_flow: examples query, unsuggested queue, bulk writer ─────────────

def test_suggested_flow_key_present_and_none_by_default(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.set_bank_transaction_derived("a", "spending", None, False)

    rows = db.get_bank_transactions_range("2026-06-01", "2026-08-01")
    assert "suggested_flow" in rows[0]
    assert rows[0]["suggested_flow"] is None


def test_examples_query_returns_only_answered_rows_newest_first_capped(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("unanswered", acct["id"], "2026-07-05", "2026-07-05",
                               -10.0, "NO OVERRIDE", "No Override", "", None)
    db.set_bank_transaction_derived("unanswered", "spending", None, False)

    for sfid, posted, payee, desc, amount, user_flow in [
        ("t1", "2026-07-01", "Coffee Shop", "COFFEE", -5.0, "spending"),
        ("t2", "2026-07-02", "Employer Inc", "PAYROLL", 1000.0, "income"),
        ("t3", "2026-07-03", "Savings Xfer", "TRANSFER", -200.0, "transfer"),
    ]:
        db.upsert_bank_transaction(sfid, acct["id"], posted, posted, amount, desc, payee, "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)
        db.set_bank_flow_override(sfid, user_flow)

    examples = db.get_bank_flow_examples(limit=20)
    assert [e["user_flow"] for e in examples] == ["transfer", "income", "spending"]  # newest first
    assert all("unanswered" not in str(e) for e in examples)

    capped = db.get_bank_flow_examples(limit=2)
    assert len(capped) == 2
    assert [e["user_flow"] for e in capped] == ["transfer", "income"]


def test_examples_query_side_correct_both_directions(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("out1", acct["id"], "2026-07-01", "2026-07-01",
                               -25.0, "COFFEE", "Coffee Shop", "", None)
    db.set_bank_transaction_derived("out1", "spending", None, False)
    db.set_bank_flow_override("out1", "spending")

    db.upsert_bank_transaction("in1", acct["id"], "2026-07-02", "2026-07-02",
                               500.0, "PAYROLL", "Employer Inc", "", None)
    db.set_bank_transaction_derived("in1", "inflow_unknown", None, False)
    db.set_bank_flow_override("in1", "income")

    examples = {e["payee"]: e for e in db.get_bank_flow_examples(limit=20)}
    assert examples["Coffee Shop"]["side"] == "outflow"
    assert examples["Employer Inc"]["side"] == "inflow"


def test_examples_query_returns_exactly_the_four_key_shape(temp_db_path):
    """Pins the never-send-notes contract at the source: no user_note, no amount,
    no dates, no account fields can leak into the model's input."""
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01",
                               -25.0, "COFFEE", "Coffee Shop", "", None)
    db.set_bank_transaction_derived("a", "spending", None, False)
    db.set_bank_flow_override("a", "spending", note="a personal note")

    examples = db.get_bank_flow_examples(limit=20)
    assert len(examples) == 1
    assert set(examples[0].keys()) == {"payee", "description", "side", "user_flow"}


def test_unsuggested_triage_excludes_rows_with_a_suggestion(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)
    db.upsert_bank_transaction("t2", acct["id"], "2026-07-02", "2026-07-02",
                               -50.0, "VENMO PAYMENT 2", "Venmo", "", None)
    db.set_bank_transaction_derived("t2", "spending", None, True)

    written = db.set_bank_suggestions_bulk({"t1": "spending"})
    assert written == 1

    ids = [r["simplefin_id"] for r in db.get_bank_unsuggested_triage(50)]
    assert ids == ["t2"]


def test_unsuggested_triage_excludes_answered_rows(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("t1", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("t1", "spending", None, True)
    db.set_bank_flow_override("t1", "transfer")

    ids = [r["simplefin_id"] for r in db.get_bank_unsuggested_triage(50)]
    assert "t1" not in ids


def test_unsuggested_triage_includes_both_bucket_kinds(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("amb", acct["id"], "2026-07-01", "2026-07-01",
                               -40.0, "VENMO PAYMENT", "Venmo", "", None)
    db.set_bank_transaction_derived("amb", "spending", None, True)

    db.upsert_bank_transaction("inflow", acct["id"], "2026-07-02", "2026-07-02",
                               500.0, "MYSTERY DEPOSIT", "", "", None)
    db.set_bank_transaction_derived("inflow", "inflow_unknown", None, False)

    ids = {r["simplefin_id"] for r in db.get_bank_unsuggested_triage(50)}
    assert ids == {"amb", "inflow"}


def test_unsuggested_triage_ordering_and_cap(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    for sfid, posted in [("b", "2026-07-01"), ("a", "2026-07-01"), ("c", "2026-07-03")]:
        db.upsert_bank_transaction(sfid, acct["id"], posted, posted, -10.0, "X", "", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, True)

    ids = [r["simplefin_id"] for r in db.get_bank_unsuggested_triage(50)]
    assert ids == ["c", "a", "b"]  # newest posted first; 07-01 tie broken a < b

    capped = [r["simplefin_id"] for r in db.get_bank_unsuggested_triage(2)]
    assert capped == ["c", "a"]


def test_bulk_suggestions_writes_both_rows_and_returns_count(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.upsert_bank_transaction("b", acct["id"], "2026-07-02", "2026-07-02", -10.0, "X", "", "", None)

    written = db.set_bank_suggestions_bulk({"a": "spending", "b": "transfer"})
    assert written == 2

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["suggested_flow"] == "spending"
    assert rows["b"]["suggested_flow"] == "transfer"


def test_bulk_suggestions_unknown_id_skipped(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)

    written = db.set_bank_suggestions_bulk({"a": "spending", "nope": "transfer"})
    assert written == 1


def test_bulk_suggestions_invalid_value_raises_before_any_write(temp_db_path):
    import database as db
    acct = _account(db, role="spending")
    db.upsert_bank_transaction("a", acct["id"], "2026-07-01", "2026-07-01", -10.0, "X", "", "", None)
    db.upsert_bank_transaction("b", acct["id"], "2026-07-02", "2026-07-02", -10.0, "X", "", "", None)

    with pytest.raises(ValueError):
        db.set_bank_suggestions_bulk({"a": "spending", "b": "not-a-real-flow"})

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2026-06-01", "2026-08-01")}
    assert rows["a"]["suggested_flow"] is None  # nothing written, including the valid entry
    assert rows["b"]["suggested_flow"] is None


# ── user_label: set, clear, vocabulary ─────────────────────────────────────────

def test_set_bank_label_roundtrip_and_clear(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)

    assert db.set_bank_label("t1", "Monthly Rent") is True
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["user_label"] == "Monthly Rent"

    assert db.set_bank_label("t1", None) is True
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["user_label"] is None


def test_set_bank_label_unknown_id_returns_false(temp_db_path):
    import database as db
    assert db.set_bank_label("nope", "X") is False


def test_label_vocabulary_distinct_most_used_first(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    for i in range(3):
        _seed_txn(db, f"g{i}", acct_id, "2026-07-20", -10.0)
        db.set_bank_label(f"g{i}", "Groceries")
    _seed_txn(db, "r1", acct_id, "2026-07-20", -100.0)
    db.set_bank_label("r1", "Monthly Rent")
    _seed_txn(db, "u1", acct_id, "2026-07-20", -5.0)   # unlabeled — not in vocab

    assert db.get_bank_label_vocabulary() == ["Groceries", "Monthly Rent"]


def test_label_vocabulary_tiebreak_is_case_insensitive(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "a1", acct_id, "2026-07-20", -10.0)
    db.set_bank_label("a1", "apple")
    _seed_txn(db, "b1", acct_id, "2026-07-20", -10.0)
    db.set_bank_label("b1", "Banana")

    # same count (1 each) — tiebreak must be case-insensitive alphabetical,
    # not "apple" < "Banana" < ... by raw byte order
    assert db.get_bank_label_vocabulary() == ["apple", "Banana"]


# ── suggested_label: derived write, resolution, vendor bulk ────────────────────

def test_label_suggestions_bulk_write_and_resolved_label(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)
    _seed_txn(db, "t2", acct_id, "2026-07-20", -60.0)
    db.set_bank_label("t2", "Groceries")

    # t1's suggested_label starts NULL -> "Groceries" is a real change.
    # t2's suggested_label is already NULL -> writing None again is a no-op.
    written = db.set_bank_label_suggestions_bulk({"t1": "Groceries", "t2": None, "ghost": "X"})
    assert written == 1                     # unknown id skipped; t2 unchanged, not rewritten

    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["t1"]["suggested_label"] == "Groceries"
    assert rows["t1"]["user_label"] is None
    assert rows["t1"]["resolved_label"] == "Groceries"     # suggestion shows
    assert rows["t2"]["resolved_label"] == "Groceries"     # user label wins
    assert rows["t2"]["suggested_label"] is None

    # repeat identical bulk write -> nothing actually changed, 0 rows touched
    written_again = db.set_bank_label_suggestions_bulk({"t1": "Groceries", "t2": None, "ghost": "X"})
    assert written_again == 0


def test_user_label_beats_suggested_in_resolved(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)
    db.set_bank_label_suggestions_bulk({"t1": "Groceries"})
    db.set_bank_label("t1", "Household")
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["resolved_label"] == "Household"


def test_set_bank_labels_by_vendor_skips_user_labeled_rows(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    for sfid in ("a1", "a2", "a3"):
        db.upsert_bank_transaction(sfid, acct_id, "2026-07-20", "2026-07-20",
                                   -10.0, "RAW", "Amazon", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)
    db.set_bank_label("a1", "Gifts")

    assert db.count_bank_unlabeled_by_vendor("Amazon") == 2
    applied = db.set_bank_labels_by_vendor("Amazon", "Household")
    assert applied == 2
    rows = {t["simplefin_id"]: t["user_label"] for t in db.get_all_bank_transactions()}
    assert rows == {"a1": "Gifts", "a2": "Household", "a3": "Household"}


def test_vendor_bulk_uses_description_fallback_for_empty_payee(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    db.upsert_bank_transaction("c1", acct_id, "2026-07-20", "2026-07-20",
                               -900.0, "CHECK 1042", "", "", None)
    db.set_bank_transaction_derived("c1", "spending", None, False)

    assert db.count_bank_unlabeled_by_vendor("CHECK 1042") == 1
    assert db.set_bank_labels_by_vendor("CHECK 1042", "Monthly Rent") == 1


def test_get_bank_transaction_vendor(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)   # payee "" → description
    assert db.get_bank_transaction_vendor("t1") is not None
    assert db.get_bank_transaction_vendor("ghost") is None


# ── user_no_label: durable rejection ───────────────────────────────────────────

def test_no_label_nulls_resolution_and_excludes_from_audit(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)
    db.set_bank_label_suggestions_bulk({"t1": "Groceries"})

    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["resolved_label"] == "Groceries"
    assert db.count_bank_label_suggestions() == 1

    assert db.set_bank_no_label("t1") is True
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["user_no_label"] is True
    assert row["resolved_label"] is None          # instantly Unlabeled, even
    assert row["suggested_label"] == "Groceries"  # though the suggestion lingers
    assert db.count_bank_label_suggestions() == 0
    assert db.get_bank_label_suggestion_rows(10) == []

    assert db.set_bank_no_label("ghost") is False


def test_setting_a_label_clears_no_label_flag(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    _seed_txn(db, "t1", acct_id, "2026-07-20", -50.0)
    db.set_bank_no_label("t1")

    db.set_bank_label("t1", "Household")
    row = next(t for t in db.get_all_bank_transactions() if t["simplefin_id"] == "t1")
    assert row["user_no_label"] is False
    assert row["resolved_label"] == "Household"


def test_vendor_bulk_and_count_skip_rejected_rows(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    for sfid in ("a1", "a2", "a3"):
        db.upsert_bank_transaction(sfid, acct_id, "2026-07-20", "2026-07-20",
                                   -10.0, "RAW", "Amazon", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)
    db.set_bank_no_label("a1")

    assert db.count_bank_unlabeled_by_vendor("Amazon") == 2
    assert db.set_bank_labels_by_vendor("Amazon", "Household") == 2
    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["a1"]["user_label"] is None
    assert rows["a1"]["user_no_label"] is True


def test_label_suggestion_rows_newest_first_and_capped(temp_db_path):
    import database as db
    acct_id = _seed_account(db)
    for i, day in enumerate(("2026-07-18", "2026-07-19", "2026-07-20")):
        _seed_txn(db, f"s{i}", acct_id, day, -10.0)
    db.set_bank_label_suggestions_bulk({"s0": "X", "s1": "X", "s2": "X"})

    rows = db.get_bank_label_suggestion_rows(2)
    assert [r["simplefin_id"] for r in rows] == ["s2", "s1"]
    assert db.count_bank_label_suggestions() == 3


def test_nickname_resolves_in_accounts_and_transactions(temp_db_path):
    import database as db
    db.upsert_bank_account("acct-n1", "EVERYDAY CHECKING ...1001 (1001)", "Wells Fargo", "checking")
    acct_id = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-n1")["id"]
    db.upsert_bank_transaction("txn-n1", acct_id, "2026-07-20", None, -12.5, description="COFFEE")

    assert db.set_bank_account_nickname("acct-n1", "Checking") is True
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-n1")
    assert acct["nickname"] == "Checking"
    assert acct["display_name"] == "Checking"
    # resolution reaches the transactions join too
    row = db.get_bank_transactions_range("2026-07-20", "2026-07-20")[0]
    assert row["account_name"] == "Checking"


def test_nickname_empty_clears_to_bank_name(temp_db_path):
    import database as db
    db.upsert_bank_account("acct-n2", "ROTH IRA (9304)", "Fidelity", "investment")
    db.set_bank_account_nickname("acct-n2", "Roth")
    assert db.set_bank_account_nickname("acct-n2", "   ") is True
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-n2")
    assert acct["nickname"] is None
    assert acct["display_name"] == "ROTH IRA (9304)"


def test_nickname_unknown_account_returns_false(temp_db_path):
    import database as db
    assert db.set_bank_account_nickname("nope", "X") is False


def test_sync_upsert_preserves_nickname(temp_db_path):
    import database as db
    db.upsert_bank_account("acct-n3", "OLD NAME", "Org", "checking")
    db.set_bank_account_nickname("acct-n3", "Mine")
    db.upsert_bank_account("acct-n3", "NEW NAME", "Org", "checking")  # a later sync
    acct = next(a for a in db.get_bank_accounts() if a["simplefin_id"] == "acct-n3")
    assert acct["nickname"] == "Mine"
    assert acct["display_name"] == "Mine"
    assert acct["name"] == "NEW NAME"
