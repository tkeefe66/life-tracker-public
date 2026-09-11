"""sync_bank: idempotent, never crashes, never leaks, never clobbers user data."""
import datetime
import time

# sync_bank.run() reclassifies the ENTIRE bank_transactions table on every run
# (see jobs/sync_bank.py) rather than a sliding lookback window, so a fixed
# epoch literal would work here too — but the fixture stays relative to the
# real wall clock anyway, for consistency with the "old" fixtures below that
# rely on being far outside SIMPLEFIN_LOOKBACK_DAYS.
_POSTED = int(time.time()) - 5 * 86400


def _seed_account(db, sfid="acct-1"):
    """Seed a bank account directly (no SimpleFIN payload) and return its db
    id, for label-pass tests that don't need a full run(). Mirrors
    tests/test_database_bank.py's _seed_account convention."""
    db.upsert_bank_account(sfid, "Everyday Checking", "Wells Fargo", "checking")
    return next(a for a in db.get_bank_accounts() if a["simplefin_id"] == sfid)["id"]


def _payload():
    return {
        "accounts": [
            {"id": "chk", "name": "EVERYDAY CHECKING", "org": {"name": "Wells Fargo"},
             "transactions": [
                 {"id": "p1", "posted": _POSTED, "amount": "-2000.00",
                  "description": "AUTOPAY PAYMENT THANK YOU"},
                 {"id": "s1", "posted": _POSTED, "amount": "-14.20",
                  "description": "COFFEE SHOP", "mcc": "5814"},
             ]},
            {"id": "card", "name": "Platinum Card", "org": {"name": "American Express"},
             "transactions": [
                 {"id": "p2", "posted": _POSTED, "amount": "2000.00",
                  "description": "PAYMENT RECEIVED"},
             ]},
        ],
        "errors": [],
    }


def _inflow_unknown_payload(n, prefix="d", account_id="mystery"):
    """`n` unpaired positive-amount deposits into a role="unknown" account.

    No matching negative counterpart exists anywhere, so match_pairs never
    pairs them, and rule 6 in bank_flows.classify_flow ("any other unpaired
    deposit") lands every one of them in inflow_unknown — exactly the queue
    bucket get_bank_unsuggested_triage selects on, with no role/income-hint
    setup required.
    """
    return {
        "accounts": [
            {"id": account_id, "name": "Mystery Account", "org": {"name": "Some Bank"},
             "transactions": [
                 {"id": f"{prefix}{i}", "posted": _POSTED - i, "amount": f"{10 + i}.00",
                  "description": f"MYSTERY DEPOSIT {i}"}
                 for i in range(n)
             ]},
        ],
        "errors": [],
    }


def _configure(monkeypatch, roles=True):
    import database as db
    from jobs import sync_bank
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)
    monkeypatch.setattr(sync_bank, "INCOME_PAYEE_HINTS", ["demandbase"])
    sync_bank.run(payload=_payload())
    if roles:
        db.set_bank_account_role("chk", "spending")
        db.set_bank_account_role("card", "credit_card")
    return sync_bank


def test_sync_stores_accounts_and_transactions(temp_db_path, monkeypatch):
    import database as db
    _configure(monkeypatch, roles=False)

    accts = {a["simplefin_id"] for a in db.get_bank_accounts()}
    assert accts == {"chk", "card"}
    assert all(a["role"] == "unknown" for a in db.get_bank_accounts())
    rows = db.get_bank_transactions_range("2020-01-01", "2030-01-01")
    assert {r["simplefin_id"] for r in rows} == {"p1", "s1", "p2"}
    assert db.get_setting("bank_last_status") == "ok"


def test_card_payment_is_matched_and_not_counted_as_spending(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    sync_bank.run(payload=_payload())  # re-run now that roles are set

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2020-01-01", "2030-01-01")}
    assert rows["p1"]["resolved_flow"] == "card_payment"
    assert rows["p2"]["resolved_flow"] == "card_payment"
    assert rows["p1"]["pair_id"] == rows["p2"]["pair_id"] is not None
    assert rows["s1"]["resolved_flow"] == "spending"


def test_resync_is_idempotent(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    sync_bank.run(payload=_payload())
    before = db.get_bank_transactions_range("2020-01-01", "2030-01-01")
    sync_bank.run(payload=_payload())
    after = db.get_bank_transactions_range("2020-01-01", "2030-01-01")
    assert [dict(r) for r in before] == [dict(r) for r in after]


def test_resync_updates_a_settled_amount_but_keeps_user_flow_and_role(temp_db_path, monkeypatch):
    import database as db
    sync_bank = _configure(monkeypatch)
    db.set_bank_flow_override("s1", "transfer")

    settled = _payload()
    settled["accounts"][0]["transactions"][1]["amount"] = "-16.31"
    sync_bank.run(payload=settled)

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2020-01-01", "2030-01-01")}
    assert rows["s1"]["amount"] == -16.31          # settled value wins
    assert rows["s1"]["user_flow"] == "transfer"   # user override survives
    assert rows["s1"]["resolved_flow"] == "transfer"
    assert {a["simplefin_id"]: a["role"] for a in db.get_bank_accounts()}["chk"] == "spending"


def test_sync_skipped_when_not_configured(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    from services.safe_status import NOT_CONFIGURED
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: False)
    sync_bank.run()
    assert db.get_setting("bank_last_status") == NOT_CONFIGURED
    assert db.get_bank_accounts() == []


def test_transport_failure_records_a_closed_set_status_and_does_not_raise(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    from services.safe_status import CLOSED_SET
    from services.simplefin_service import SimpleFinError

    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    def boom(*a, **kw):
        raise SimpleFinError("error: auth")

    monkeypatch.setattr(sync_bank.simplefin_service, "fetch_accounts", boom)
    sync_bank.run()  # must not raise — an ingestion job never crashes the app

    status = db.get_setting("bank_last_status")
    assert status == "error: auth"
    assert status in CLOSED_SET


def test_an_unexpected_error_still_records_a_closed_set_status(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    from services.safe_status import CLOSED_SET

    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("https://user:secret@bridge.example.com/simplefin exploded")

    monkeypatch.setattr(sync_bank.simplefin_service, "fetch_accounts", boom)
    sync_bank.run()

    stored = " ".join(str(db.get_setting(k)) for k in
                      ("bank_last_status", "bank_last_result", "bank_last_run"))
    assert db.get_setting("bank_last_status") in CLOSED_SET
    assert "secret" not in stored and "bridge.example.com" not in stored


def test_transaction_for_an_unknown_account_is_skipped_not_crashed(temp_db_path, monkeypatch):
    """A transaction whose account SimpleFIN did not report can't get an FK.

    normalize() only ever emits a transaction nested under an account it also
    emitted, so the skip branch is unreachable through the normal payload
    shape — reach it by monkeypatching normalize() itself to inject an orphan
    transaction referencing an account id that was never reported. Appending
    to accounts[0]["transactions"] (a *reported* account) would ingest it
    normally and never touch the skip branch at all.
    """
    import database as db
    from jobs import sync_bank
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    real_normalize = sync_bank.simplefin_service.normalize
    posted_day = datetime.datetime.fromtimestamp(_POSTED, tz=datetime.timezone.utc).date().isoformat()

    def normalize_with_orphan(payload):
        accounts, txns = real_normalize(payload)
        txns = txns + [{
            "simplefin_id": "orphan", "account_simplefin_id": "ghost-account",
            "posted": posted_day, "transacted_at": posted_day, "amount": -5.0,
            "description": "X", "payee": "", "memo": "", "mcc": None,
        }]
        return accounts, txns

    monkeypatch.setattr(sync_bank.simplefin_service, "normalize", normalize_with_orphan)
    sync_bank.run(payload=_payload())

    assert db.get_setting("bank_last_status") == "ok"
    assert "1 skipped" in db.get_setting("bank_last_result")
    stored_ids = {r["simplefin_id"] for r in db.get_bank_transactions_range("2020-01-01", "2030-01-01")}
    assert "orphan" not in stored_ids


def test_transactions_older_than_lookback_are_reclassified_once_roles_are_set(temp_db_path, monkeypatch):
    """The initial 90-day backfill classifies everything while every account
    role is still 'unknown'. A sliding SIMPLEFIN_LOOKBACK_DAYS window would
    freeze that classification permanently the moment a row ages past it —
    the fix is a full-table reclassify on every run, so a role assigned long
    after ingest still corrects old rows."""
    import database as db
    from jobs import sync_bank

    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)
    monkeypatch.setattr(sync_bank, "INCOME_PAYEE_HINTS", [])

    old_posted = int(time.time()) - 200 * 86400
    old_payload = {
        "accounts": [
            {"id": "inv", "name": "Brokerage", "org": {"name": "Fidelity"},
             "transactions": [
                 {"id": "old1", "posted": old_posted, "amount": "-500.00",
                  "description": "CONTRIBUTION"},
             ]},
        ],
        "errors": [],
    }
    sync_bank.run(payload=old_payload)

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2000-01-01", "2030-01-01")}
    assert rows["old1"]["resolved_flow"] == "spending"  # role unknown at ingest time

    db.set_bank_account_role("inv", "investment")
    sync_bank.run(payload=old_payload)  # re-sync well after roles are assigned

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2000-01-01", "2030-01-01")}
    assert rows["old1"]["resolved_flow"] == "investment"


def test_settled_amount_that_no_longer_matches_loses_its_pair_id_and_stops_being_card_payment(
    temp_db_path, monkeypatch
):
    """A pending -2000 that settles to a different amount must stop pairing
    with its old +2000 partner — a wrong pair must never be permanent."""
    import database as db
    sync_bank = _configure(monkeypatch)
    sync_bank.run(payload=_payload())  # p1/p2 pair up as card_payment

    settled = _payload()
    settled["accounts"][0]["transactions"][0]["amount"] = "-1500.00"  # no longer matches p2's +2000
    sync_bank.run(payload=settled)

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2020-01-01", "2030-01-01")}
    assert rows["p1"]["pair_id"] is None
    assert rows["p2"]["pair_id"] is None
    # The paying (checking) side loses card_payment entirely — that's the
    # unpairing guard. p2 stays card_payment, but now via the unpaired
    # credit_card + payment-wording rule, NOT via a stale pair: the pair_id
    # assertion above is what pins that distinction.
    assert rows["p1"]["resolved_flow"] != "card_payment"
    assert rows["p2"]["resolved_flow"] == "card_payment"


def test_simplefin_error_with_a_non_closed_set_status_is_normalized_before_storage(
    temp_db_path, monkeypatch
):
    """SimpleFinError.__init__ accepts any string — the closed-set invariant on
    that path holds only by convention. Defend it at the write site too."""
    import database as db
    from jobs import sync_bank
    from services.safe_status import CLOSED_SET
    from services.simplefin_service import SimpleFinError

    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    def boom(*a, **kw):
        raise SimpleFinError("not a real status")

    monkeypatch.setattr(sync_bank.simplefin_service, "fetch_accounts", boom)
    sync_bank.run()

    status = db.get_setting("bank_last_status")
    assert status == "error: see logs"
    assert status in CLOSED_SET


# ---------------------------------------------------------------------------
# Post-reclassify suggestion pass (spec §4)
# ---------------------------------------------------------------------------

def test_suggestion_pass_writes_suggestions_for_unsuggested_queue_rows(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    def canned(rows, examples):
        return {r["simplefin_id"]: "income" for r in rows}

    monkeypatch.setattr(sync_bank.ai_metrics, "suggest_bank_flows", canned)
    sync_bank.run(payload=_inflow_unknown_payload(2))

    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2000-01-01", "2030-01-01")}
    assert rows["d0"]["suggested_flow"] == "income"
    assert rows["d1"]["suggested_flow"] == "income"
    assert db.get_setting("bank_last_status") == "ok"


def test_suggestion_pass_never_resends_an_already_suggested_row(temp_db_path, monkeypatch):
    from jobs import sync_bank
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    calls = []

    def canned(rows, examples):
        calls.append([r["simplefin_id"] for r in rows])
        return {r["simplefin_id"]: "income" for r in rows}

    monkeypatch.setattr(sync_bank.ai_metrics, "suggest_bank_flows", canned)
    sync_bank.run(payload=_inflow_unknown_payload(2))  # d0, d1 both get suggested_flow="income"

    calls.clear()
    sync_bank.run(payload=_inflow_unknown_payload(2))  # re-sync same rows

    # Both rows already carry a suggestion, so no batch should ever contain them.
    seen = {sfid for call in calls for sfid in call}
    assert seen == set()


def test_suggestion_pass_batches_at_most_three_calls_of_at_most_forty(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    calls = []

    def canned(rows, examples):
        calls.append([r["simplefin_id"] for r in rows])
        return {r["simplefin_id"]: "income" for r in rows}  # every row gets suggested -> loop progresses

    monkeypatch.setattr(sync_bank.ai_metrics, "suggest_bank_flows", canned)
    sync_bank.run(payload=_inflow_unknown_payload(100))

    assert len(calls) == 3
    assert [len(c) for c in calls] == [40, 40, 20]

    suggested = {r["simplefin_id"] for r in db.get_bank_transactions_range("2000-01-01", "2030-01-01")
                 if r["suggested_flow"] is not None}
    assert len(suggested) == 100


def test_suggestion_pass_stops_after_an_all_abstain_batch(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank
    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    calls = []

    def abstains_always(rows, examples):
        calls.append(rows)
        return {}  # every row abstained -> written == 0 -> loop must break, not spin 3x on same rows

    monkeypatch.setattr(sync_bank.ai_metrics, "suggest_bank_flows", abstains_always)
    sync_bank.run(payload=_inflow_unknown_payload(5))

    assert len(calls) == 1
    assert db.get_setting("bank_last_status") == "ok"


def test_suggestion_pass_failure_does_not_touch_sync_status_or_write_anything(temp_db_path, monkeypatch):
    import database as db
    from jobs import sync_bank

    monkeypatch.setattr(sync_bank.simplefin_service, "is_configured", lambda: True)

    def boom(rows, examples):
        raise RuntimeError("AI is down")

    monkeypatch.setattr(sync_bank.ai_metrics, "suggest_bank_flows", boom)
    sync_bank.run(payload=_inflow_unknown_payload(2))  # must not raise

    assert db.get_setting("bank_last_status") == "ok"  # exactly what a suggestion-free sync records
    rows = {r["simplefin_id"]: r for r in db.get_bank_transactions_range("2000-01-01", "2030-01-01")}
    assert rows["d0"]["suggested_flow"] is None
    assert rows["d1"]["suggested_flow"] is None


def test_zero_unsuggested_rows_makes_zero_ai_calls(temp_db_path, monkeypatch):
    from jobs import sync_bank

    calls = []

    def canned(rows, examples):
        calls.append(rows)
        return {}

    monkeypatch.setattr(sync_bank.ai_metrics, "suggest_bank_flows", canned)
    # _configure's default payload (card payment + spending) never lands in the
    # ambiguous/inflow_unknown triage buckets, so the queue starts empty.
    _configure(monkeypatch)

    assert calls == []


# ── Label-suggestion pass: same-vendor inheritance, full recompute ─────────────

def test_label_pass_propagates_and_retires(temp_db_path):
    import database as db
    from jobs import sync_bank
    acct_id = _seed_account(db)
    db.upsert_bank_transaction("r1", acct_id, "2026-07-01", "2026-07-01",
                               -900.0, "RAW", "Check", "", None)
    db.set_bank_transaction_derived("r1", "spending", None, False)
    db.upsert_bank_transaction("r2", acct_id, "2026-07-02", "2026-07-02",
                               -900.0, "RAW", "Check", "", None)
    db.set_bank_transaction_derived("r2", "spending", None, False)
    db.upsert_bank_transaction("x1", acct_id, "2026-07-03", "2026-07-03",
                               -12.0, "RAW", "Cafe", "", None)
    db.set_bank_transaction_derived("x1", "spending", None, False)
    db.set_bank_label("r1", "Monthly Rent")

    sync_bank._suggest_labels()
    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["r2"]["suggested_label"] == "Monthly Rent"
    assert rows["x1"]["suggested_label"] is None

    # user clears the label -> next pass retires the suggestion
    db.set_bank_label("r1", None)
    sync_bank._suggest_labels()
    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["r2"]["suggested_label"] is None


def test_label_pass_conflict_stays_silent(temp_db_path):
    import database as db
    from jobs import sync_bank
    acct_id = _seed_account(db)
    for sfid in ("a1", "a2", "a3"):
        db.upsert_bank_transaction(sfid, acct_id, "2026-07-20", "2026-07-20",
                                   -10.0, "RAW", "Amazon", "", None)
        db.set_bank_transaction_derived(sfid, "spending", None, False)
    db.set_bank_label("a1", "Household")
    db.set_bank_label("a2", "Gifts")
    sync_bank._suggest_labels()
    rows = {t["simplefin_id"]: t for t in db.get_all_bank_transactions()}
    assert rows["a3"]["suggested_label"] is None
