"""Pair matching and flow classification — pure arithmetic, no DB, no AI."""
import bank_flows


def txn(sfid, account_id, posted, amount, pair_id=None, description="", payee="", mcc=None):
    return {"simplefin_id": sfid, "account_id": account_id, "posted": posted,
            "amount": amount, "pair_id": pair_id, "description": description,
            "payee": payee, "mcc": mcc}


def test_opposite_amounts_across_accounts_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-02", 500.0),
    ])
    assert out == {"a": "a", "b": "a"}  # pair_id is the smaller simplefin_id


def test_same_account_movement_does_not_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 1, "2026-07-01", 500.0),
    ])
    assert out == {}


def test_outside_the_window_does_not_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-09", 500.0),
    ], window_days=3)
    assert out == {}


def test_near_miss_amounts_do_not_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-01", 500.01),
    ])
    assert out == {}


def test_same_sign_amounts_do_not_pair():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-01", -500.0),
    ])
    assert out == {}


def test_already_paired_transaction_is_not_repaired():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0, pair_id="a"),
        txn("b", 2, "2026-07-01", 500.0, pair_id="a"),
        txn("c", 3, "2026-07-01", 500.0),
    ])
    assert out == {}  # 'a' is taken; 'c' has nothing free to pair with


def test_half_arriving_in_a_later_sync_pairs_on_the_later_run():
    """The first sync sees only one half and matches nothing; the second sees both."""
    first = [txn("a", 1, "2026-07-01", -500.0)]
    assert bank_flows.match_pairs(first) == {}
    second = [txn("a", 1, "2026-07-01", -500.0), txn("b", 2, "2026-07-02", 500.0)]
    assert bank_flows.match_pairs(second) == {"a": "a", "b": "a"}


def test_ties_resolve_by_smallest_date_gap_then_lowest_id():
    """Two equally valid partners: the nearer date wins."""
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("z", 2, "2026-07-03", 500.0),
        txn("y", 2, "2026-07-01", 500.0),
    ])
    assert out == {"a": "a", "y": "a"}   # same-day 'y' beats 'z'


def test_exact_date_tie_resolves_by_lowest_id():
    out = bank_flows.match_pairs([
        txn("m", 1, "2026-07-01", -500.0),
        txn("z", 2, "2026-07-01", 500.0),
        txn("a", 3, "2026-07-01", 500.0),
    ])
    assert out == {"m": "a", "a": "a"}  # 'a' < 'z'


def test_matching_is_deterministic_regardless_of_input_order():
    rows = [txn("a", 1, "2026-07-01", -500.0),
            txn("z", 2, "2026-07-01", 500.0),
            txn("b", 3, "2026-07-01", 500.0)]
    assert bank_flows.match_pairs(rows) == bank_flows.match_pairs(list(reversed(rows)))


def test_float_cents_still_pair():
    """Money compares in integer cents — float equality would drop this pair."""
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -1234.56),
        txn("b", 2, "2026-07-01", 1234.56),
    ])
    assert out == {"a": "a", "b": "a"}


def test_one_outflow_claims_only_one_partner():
    out = bank_flows.match_pairs([
        txn("a", 1, "2026-07-01", -500.0),
        txn("b", 2, "2026-07-01", 500.0),
        txn("c", 3, "2026-07-01", 500.0),
    ])
    assert out == {"a": "a", "b": "a"}  # 'c' is left unpaired


# ── Text corroboration as a tie-break (equal-amount collisions) ───────────────
# Real Wells Fargo online transfers state the counterparty's last four digits
# ("...XXXXXX1003"), and card payments state the issuer ("AMERICAN EXPRESS ACH
# PMT"). When several equal-amount rows land inside the window, amount+date
# alone cross-wires them. Text is used ONLY to rank candidates, never to
# exclude one — a row with no extractable signature must pair exactly as before.

ACCOUNTS = [
    {"id": 1, "name": "EVERYDAY CHECKING ...1002 (1002)", "org": "Wells Fargo"},
    {"id": 2, "name": "EVERYDAY CHECKING ...1003 (1003)", "org": "Wells Fargo"},
    {"id": 3, "name": "Example Card (1004)", "org": "American Express"},
    {"id": 4, "name": "SAVINGS (5511)", "org": "Wells Fargo"},
]


def test_equal_amount_collision_pairs_by_stated_last_four():
    """The 2026-04-29 $3,200 collision: a checking-to-checking transfer and an
    Amex payment settle the same day for the same amount. Without text the
    matcher pairs 1002's transfer outflow with the Amex inflow, inventing
    $3,200 of phantom spending AND $3,200 of phantom inflow_unknown."""
    rows = [
        txn("a1", 1, "2026-04-29", -3200.0,
            description="ONLINE TRANSFER TO EXAMPLE A EVERYDAY CHECKING XXXXXX1003 REF #IB0X ON 04/29/26"),
        txn("a2", 2, "2026-04-29", -3200.0,
            description="AMERICAN EXPRESS ACH PMT    260429 W3006    Thomas Keefe"),
        txn("b1", 3, "2026-04-29", 3200.0, description="ONLINE PAYMENT - THANK YOU"),
        txn("b2", 2, "2026-04-29", 3200.0,
            description="ONLINE TRANSFER FROM EXAMPLE A EVERYDAY CHECKING XXXXXX1002 REF #IB0X ON 04/29/26"),
    ]
    out = bank_flows.match_pairs(rows, accounts=ACCOUNTS)
    assert out == {"a1": "a1", "b2": "a1", "a2": "a2", "b1": "a2"}


def test_issuer_signature_outranks_a_lower_id_candidate():
    """'AMERICAN EXPRESS ACH PMT' must prefer the Amex account even when a
    same-day, same-amount candidate has a lexicographically smaller id."""
    rows = [
        txn("out", 2, "2026-05-15", -200.0, description="AMERICAN EXPRESS ACH PMT 260515"),
        txn("aaa", 4, "2026-05-15", 200.0, description="ONLINE TRANSFER FROM CHECKING"),
        txn("zzz", 3, "2026-05-15", 200.0, description="ONLINE PAYMENT - THANK YOU"),
    ]
    out = bank_flows.match_pairs(rows, accounts=ACCOUNTS)
    assert out == {"out": "out", "zzz": "out"}


def test_rows_without_a_signature_pair_exactly_as_before():
    """Regression guard: no extractable text signature anywhere means the old
    (date gap, lowest partner id) ranking still decides, accounts or not."""
    rows = [
        txn("m", 1, "2026-07-01", -500.0, description="SOMETHING OPAQUE"),
        txn("z", 2, "2026-07-01", 500.0, description="ALSO OPAQUE"),
        txn("a", 4, "2026-07-01", 500.0, description=""),
    ]
    assert bank_flows.match_pairs(rows, accounts=ACCOUNTS) == bank_flows.match_pairs(rows)
    assert bank_flows.match_pairs(rows, accounts=ACCOUNTS) == {"m": "a", "a": "a"}


def test_text_corroboration_never_prevents_a_pair():
    """Tie-break only: a contradicting description with a single candidate must
    still pair. Nothing that paired before may become unpaired."""
    rows = [
        txn("a", 1, "2026-07-01", -500.0,
            description="ONLINE TRANSFER TO EXAMPLE A EVERYDAY CHECKING XXXXXX1003 REF #IB0X"),
        txn("b", 4, "2026-07-01", 500.0, description="DEPOSIT"),
    ]
    assert bank_flows.match_pairs(rows, accounts=ACCOUNTS) == {"a": "a", "b": "a"}


def test_stated_last_four_for_an_unknown_account_is_ignored():
    """A last-four that maps to no known account carries no information and must
    not be treated as a contradiction."""
    rows = [
        txn("m", 1, "2026-07-01", -500.0, description="ONLINE TRANSFER TO XXXXXX9999 REF #IB0X"),
        txn("z", 2, "2026-07-01", 500.0),
        txn("a", 4, "2026-07-01", 500.0),
    ]
    assert bank_flows.match_pairs(rows, accounts=ACCOUNTS) == {"m": "a", "a": "a"}


def test_corroborated_pairing_is_independent_of_input_order():
    rows = [
        txn("a1", 1, "2026-04-29", -3200.0,
            description="ONLINE TRANSFER TO EXAMPLE A EVERYDAY CHECKING XXXXXX1003 REF #IB0X"),
        txn("a2", 2, "2026-04-29", -3200.0, description="AMERICAN EXPRESS ACH PMT 260429"),
        txn("b1", 3, "2026-04-29", 3200.0, description="ONLINE PAYMENT - THANK YOU"),
        txn("b2", 2, "2026-04-29", 3200.0,
            description="ONLINE TRANSFER FROM EXAMPLE A EVERYDAY CHECKING XXXXXX1002 REF #IB0X"),
    ]
    forward = bank_flows.match_pairs(rows, accounts=ACCOUNTS)
    backward = bank_flows.match_pairs(list(reversed(rows)), accounts=ACCOUNTS)
    shuffled = bank_flows.match_pairs([rows[2], rows[0], rows[3], rows[1]], accounts=ACCOUNTS)
    assert forward == backward == shuffled


def test_a_nearer_date_still_beats_a_corroborated_farther_candidate_only_when_uncontradicted():
    """Date gap remains the second key: among candidates that don't contradict,
    the nearer date wins, exactly as before."""
    rows = [
        txn("a", 1, "2026-07-01", -500.0, description="SOMETHING OPAQUE"),
        txn("z", 2, "2026-07-03", 500.0),
        txn("y", 4, "2026-07-01", 500.0),
    ]
    assert bank_flows.match_pairs(rows, accounts=ACCOUNTS) == {"a": "a", "y": "a"}


# ── Flow classification ───────────────────────────────────────────────────────

HINTS = ["demandbase", "acme payroll"]


def test_investment_beats_card_payment_and_transfer():
    """Rule 1 wins: a contribution paid from a card-ish account is still saving."""
    t = txn("a", 1, "2026-07-01", -500.0)
    assert bank_flows.classify_flow(t, "spending", "investment", True, HINTS) == "investment"
    assert bank_flows.classify_flow(t, "investment", "credit_card", True, HINTS) == "investment"


def test_investment_to_investment_is_investment_on_both_sides():
    """Traditional -> Roth conversion contributes to nothing."""
    t = txn("a", 1, "2026-07-01", -6000.0)
    u = txn("b", 2, "2026-07-01", 6000.0)
    assert bank_flows.classify_flow(t, "investment", "investment", True, HINTS) == "investment"
    assert bank_flows.classify_flow(u, "investment", "investment", True, HINTS) == "investment"


def test_card_payment_beats_transfer():
    t = txn("a", 1, "2026-07-01", -2000.0)
    assert bank_flows.classify_flow(t, "spending", "credit_card", True, HINTS) == "card_payment"
    assert bank_flows.classify_flow(t, "credit_card", "spending", True, HINTS) == "card_payment"


def test_matched_pair_between_ordinary_accounts_is_transfer():
    t = txn("a", 1, "2026-07-01", -500.0)
    assert bank_flows.classify_flow(t, "spending", "savings", True, HINTS) == "transfer"


def test_unpaired_deposit_matching_a_payroll_hint_is_income():
    t = txn("a", 1, "2026-07-01", 3200.0, payee="DEMANDBASE PAYROLL")
    assert bank_flows.classify_flow(t, "spending", None, False, HINTS) == "income"


def test_income_hint_matches_description_too_and_is_case_insensitive():
    t = txn("a", 1, "2026-07-01", 3200.0, description="direct dep acme payroll llc")
    assert bank_flows.classify_flow(t, "bills", None, False, HINTS) == "income"


def test_unpaired_deposit_without_a_hint_is_inflow_unknown_never_income():
    """The SoFi hazard: a savings drawdown must never be reported as earnings."""
    t = txn("a", 1, "2026-07-01", 2000.0, description="TRANSFER FROM SOFI")
    assert bank_flows.classify_flow(t, "spending", None, False, HINTS) == "inflow_unknown"


def test_payroll_hint_into_a_non_spending_account_is_not_income():
    """Rule 4 is scoped to spending/bills accounts."""
    t = txn("a", 1, "2026-07-01", 3200.0, payee="DEMANDBASE PAYROLL")
    assert bank_flows.classify_flow(t, "unknown", None, False, HINTS) == "inflow_unknown"


def test_empty_hints_never_produce_income():
    t = txn("a", 1, "2026-07-01", 3200.0, payee="DEMANDBASE PAYROLL")
    assert bank_flows.classify_flow(t, "spending", None, False, []) == "inflow_unknown"


def test_ordinary_unpaired_outflow_is_spending():
    t = txn("a", 1, "2026-07-01", -14.20, payee="COFFEE SHOP")
    assert bank_flows.classify_flow(t, "spending", None, False, HINTS) == "spending"


def test_unpaired_venmo_outflow_stays_spending_and_is_flagged():
    t = txn("a", 1, "2026-07-01", -40.0, description="VENMO PAYMENT 123")
    flow = bank_flows.classify_flow(t, "spending", None, False, HINTS)
    assert flow == "spending"
    assert bank_flows.is_ambiguous(t, flow) is True


def test_a_matched_transfer_is_not_flagged_ambiguous():
    t = txn("a", 1, "2026-07-01", -500.0, description="ONLINE TRANSFER")
    flow = bank_flows.classify_flow(t, "spending", "savings", True, HINTS)
    assert bank_flows.is_ambiguous(t, flow) is False


def test_plain_spending_is_not_flagged_ambiguous():
    t = txn("a", 1, "2026-07-01", -14.20, payee="COFFEE SHOP")
    assert bank_flows.is_ambiguous(t, "spending") is False


def test_classify_all_wires_pairs_roles_and_flags_together():
    txns = [
        txn("a", 1, "2026-07-01", -2000.0, description="AUTOPAY THANK YOU"),
        txn("b", 2, "2026-07-01", 2000.0, description="PAYMENT RECEIVED"),
        txn("c", 1, "2026-07-02", -40.0, description="VENMO PAYMENT"),
        txn("d", 1, "2026-07-03", 3200.0, payee="DEMANDBASE PAYROLL"),
    ]
    roles = {1: "spending", 2: "credit_card"}
    pair_map = bank_flows.match_pairs(txns)
    out = bank_flows.classify_all(txns, roles, pair_map, HINTS)

    assert out["a"] == ("card_payment", "a", False)
    assert out["b"] == ("card_payment", "a", False)
    assert out["c"] == ("spending", None, True)
    assert out["d"] == ("income", None, False)


def test_classify_all_treats_an_unknown_account_role_as_unknown():
    txns = [txn("a", 99, "2026-07-01", 500.0, payee="DEMANDBASE PAYROLL")]
    out = bank_flows.classify_all(txns, {}, {}, HINTS)
    assert out["a"] == ("inflow_unknown", None, False)


def test_classify_all_honours_a_preexisting_pair_id():
    """A pair matched in an earlier sync still classifies as a pair."""
    txns = [
        txn("a", 1, "2026-07-01", -2000.0, pair_id="a"),
        txn("b", 2, "2026-07-01", 2000.0, pair_id="a"),
    ]
    out = bank_flows.classify_all(txns, {1: "spending", 2: "credit_card"}, {}, HINTS)
    assert out["a"][0] == "card_payment"
    assert out["b"][0] == "card_payment"


# ── Precedence pinning (Finding 3) ────────────────────────────────────────────
# These tests exist to fail loudly if rule order is ever disturbed — in
# particular the mutation that hoisted rule 4 (income) above rule 3 (transfer),
# which every pre-existing income test missed because they all use
# partner_role=None / paired=False.


def test_paired_payroll_hint_deposit_is_transfer_never_income():
    """A paired positive deposit matching a payroll hint must be `transfer`,
    not `income` — pairing always wins over the income rule."""
    t = txn("a", 1, "2026-07-03", 3200.0, payee="DEMANDBASE PAYROLL")
    assert bank_flows.classify_flow(t, "spending", "savings", True, HINTS) == "transfer"


def test_paired_payroll_hint_deposit_with_credit_card_partner_is_card_payment_never_income():
    """Same as above, but a credit card is involved on one side — card_payment
    must still beat income."""
    t = txn("a", 1, "2026-07-03", 3200.0, payee="DEMANDBASE PAYROLL")
    assert bank_flows.classify_flow(t, "spending", "credit_card", True, HINTS) == "card_payment"


def test_classify_all_partner_absent_from_window_outflow_is_not_spending_or_income():
    """Pins Finding 1: a matched pair whose partner has aged out of the window
    (represented here by a pair_id with no matching row in `txns`) must not
    degrade to `spending` — that would invent phantom spending on every
    multi-day card payment or transfer."""
    txns = [txn("a", 1, "2026-07-01", -2000.0, pair_id="p1")]
    out = bank_flows.classify_all(txns, {1: "spending"}, {}, HINTS)
    flow, pid, ambiguous = out["a"]
    assert flow not in ("spending", "income")
    assert flow == "transfer"
    assert pid == "p1"


def test_classify_all_partner_absent_from_window_payroll_hint_inflow_is_not_spending_or_income():
    """Pins Finding 1 for the SoFi-hazard direction: a matched-pair deposit
    whose partner has aged out, with a payee that happens to match a payroll
    hint, must not be reported as income."""
    txns = [txn("a", 1, "2026-07-03", 3200.0, pair_id="p1", payee="DEMANDBASE PAYROLL")]
    out = bank_flows.classify_all(txns, {1: "spending"}, {}, HINTS)
    flow, pid, ambiguous = out["a"]
    assert flow not in ("spending", "income")
    assert flow == "transfer"
    assert pid == "p1"


def test_classify_all_pair_map_overrides_conflicting_preexisting_pair_id():
    """This sync's `pair_map` wins over a stale, conflicting `pair_id` already
    stored on the row. Assert the full returned triple, not just the flow."""
    txns = [
        txn("a", 1, "2026-07-01", -500.0, pair_id="stale"),
        txn("b", 2, "2026-07-01", 500.0),
    ]
    pair_map = {"a": "a", "b": "a"}
    roles = {1: "spending", 2: "savings"}
    out = bank_flows.classify_all(txns, roles, pair_map, HINTS)
    assert out["a"] == ("transfer", "a", False)
    assert out["b"] == ("transfer", "a", False)


def test_is_ambiguous_is_false_for_non_spending_flows():
    """`is_ambiguous` only ever flags `spending` — every other flow is
    definitionally not phantom spending, so it must return False regardless of
    how transfer-ish the wording looks."""
    t = txn("a", 1, "2026-07-01", 3200.0, description="VENMO WIRE TRANSFER")
    assert bank_flows.is_ambiguous(t, "income") is False
    assert bank_flows.is_ambiguous(t, "inflow_unknown") is False
    assert bank_flows.is_ambiguous(t, "investment") is False
    assert bank_flows.is_ambiguous(t, "card_payment") is False


def test_classify_flow_handles_none_payee_and_description():
    """`payee`/`description` are `None` rather than `""` on some real rows —
    must not raise, and must not spuriously match."""
    t = {"simplefin_id": "a", "account_id": 1, "posted": "2026-07-01", "amount": 3200.0,
         "pair_id": None, "description": None, "payee": None, "mcc": None}
    assert bank_flows.classify_flow(t, "spending", None, False, HINTS) == "inflow_unknown"


def test_classify_flow_matches_income_hint_when_only_one_field_is_present():
    t = {"simplefin_id": "a", "account_id": 1, "posted": "2026-07-01", "amount": 3200.0,
         "pair_id": None, "description": "acme payroll deposit", "payee": None, "mcc": None}
    assert bank_flows.classify_flow(t, "spending", None, False, HINTS) == "income"


def test_classify_all_is_deterministic_under_input_reordering():
    txns = [
        txn("a", 1, "2026-07-01", -2000.0, description="AUTOPAY THANK YOU"),
        txn("b", 2, "2026-07-01", 2000.0, description="PAYMENT RECEIVED"),
        txn("c", 1, "2026-07-02", -40.0, description="VENMO PAYMENT"),
        txn("d", 1, "2026-07-03", 3200.0, payee="DEMANDBASE PAYROLL"),
    ]
    roles = {1: "spending", 2: "credit_card"}
    forward = bank_flows.classify_all(txns, roles, bank_flows.match_pairs(txns), HINTS)
    reordered = list(reversed(txns))
    backward = bank_flows.classify_all(reordered, roles, bank_flows.match_pairs(reordered), HINTS)
    assert forward == backward


def test_classify_all_partner_choice_is_order_independent_with_a_stale_triple_pair_id():
    """Pins Finding 4: three rows sharing a stale pair_id (possible from bad
    data) must resolve to the same partner — and thus the same flow —
    regardless of input order."""
    rows = [
        txn("a", 1, "2026-07-01", -2000.0, pair_id="p1"),
        txn("b", 2, "2026-07-01", 2000.0, pair_id="p1"),
        txn("d", 3, "2026-07-01", 2000.0, pair_id="p1"),
    ]
    roles = {1: "spending", 2: "credit_card", 3: "savings"}
    forward = bank_flows.classify_all(rows, roles, {}, HINTS)
    backward = bank_flows.classify_all(list(reversed(rows)), roles, {}, HINTS)
    assert forward["a"] == backward["a"]
    assert forward["a"][0] == "card_payment"  # lowest-id partner 'b' wins deterministically


# ── Finding 2: whitespace-only hints must never produce income ───────────────


def test_whitespace_only_hint_never_produces_income():
    """A whitespace-only configured hint must not make every deposit income —
    the old haystack (payee + ' ' + description) always contained a space,
    so `" " in haystack` was always true."""
    t = txn("a", 1, "2026-07-01", 2000.0, description="TRANSFER FROM SOFI")
    assert bank_flows.classify_flow(t, "spending", None, False, ["   "]) == "inflow_unknown"


# ── Finding 5: payee and description must be matched independently ───────────


def test_income_hint_does_not_match_across_payee_and_description_fields():
    """hint 'acme payroll' must not match payee='ACME' + description='PAYROLL'
    split across two separate fields — each field is checked on its own."""
    t = txn("a", 1, "2026-07-01", 3200.0, payee="ACME", description="PAYROLL")
    assert bank_flows.classify_flow(t, "spending", None, False, HINTS) == "inflow_unknown"


# ── Finding 6: income hints require a word-boundary match ────────────────────


def test_short_income_hint_requires_word_boundary_match():
    hints = ["ach"]
    matching = txn("a", 1, "2026-07-01", 500.0, description="ACH DEPOSIT PAYROLL")
    non_matching = txn("b", 1, "2026-07-01", 500.0, description="BEACH BOARDWALK REFUND")
    assert bank_flows.classify_flow(matching, "spending", None, False, hints) == "income"
    assert bank_flows.classify_flow(non_matching, "spending", None, False, hints) == "inflow_unknown"


# ── Unpaired card payments (SoFi hazard, paying side not connected) ──────────
# 7 real rows worth $12,224.15 sat in `inflow_unknown`: Chase United "Payment
# Thank You - Web" and Amex "ONLINE PAYMENT - THANK YOU", paid from an account
# SimpleFIN can't see, so no opposite outflow exists anywhere in the dataset.
# A positive amount on a credit_card account with payment wording cannot be
# income and cannot be spending — it is debt paydown.

CARD_PAYMENT_WORDINGS = [
    "Payment Thank You - Web",
    "PAYMENT RECEIVED - THANK YOU",
    "ONLINE PAYMENT - THANK YOU",
    "MOBILE PAYMENT - THANK YOU",
]


def test_unpaired_payment_wording_on_a_credit_card_is_card_payment():
    for wording in CARD_PAYMENT_WORDINGS:
        t = txn("a", 1, "2026-07-01", 1500.0, description=wording)
        assert bank_flows.classify_flow(t, "credit_card", None, False, HINTS) == "card_payment", wording


def test_unpaired_card_payment_wording_matches_payee_field_too():
    t = txn("a", 1, "2026-07-01", 510.93, payee="Payment Thank You - Web", description=None)
    assert bank_flows.classify_flow(t, "credit_card", None, False, HINTS) == "card_payment"


def test_merchant_refund_on_a_credit_card_stays_inflow_unknown():
    """The same dataset holds 20 refund/statement-credit rows ($1,440.33) on
    credit cards. None use payment wording; all must stay inflow_unknown."""
    refunds = [
        "REI.COM RETURN",
        "AMAZON.COM*RT4YU REFUND",
        "RESY STATEMENT CREDIT",
        "LULULEMON STATEMENT CREDIT",
        "DIGITAL ENTERTAINMENT CREDIT",
        "UBER EATS ADJUSTMENT",
        "CREDIT ADJUSTMENT",
    ]
    for wording in refunds:
        t = txn("a", 1, "2026-07-01", 42.0, description=wording)
        assert bank_flows.classify_flow(t, "credit_card", None, False, HINTS) == "inflow_unknown", wording


def test_payment_wording_on_a_non_credit_card_account_is_unaffected():
    """Rule is scoped to credit_card-role accounts — a checking deposit reading
    'ONLINE PAYMENT' must still land in inflow_unknown."""
    t = txn("a", 1, "2026-07-01", 900.0, description="ONLINE PAYMENT - THANK YOU")
    assert bank_flows.classify_flow(t, "spending", None, False, HINTS) == "inflow_unknown"
    assert bank_flows.classify_flow(t, "savings", None, False, HINTS) == "inflow_unknown"
    assert bank_flows.classify_flow(t, "unknown", None, False, HINTS) == "inflow_unknown"


def test_unpaired_card_outflow_with_payment_wording_stays_spending():
    """Sign matters: only a positive amount is a paydown."""
    t = txn("a", 1, "2026-07-01", -80.0, description="ONLINE PAYMENT - THANK YOU")
    assert bank_flows.classify_flow(t, "credit_card", None, False, HINTS) == "spending"


def test_paired_card_payment_is_unchanged_by_the_unpaired_rule():
    """Regression: rule 2 still owns every paired case, wording or not."""
    t = txn("a", 1, "2026-07-01", 2000.0, description="ONLINE PAYMENT - THANK YOU")
    assert bank_flows.classify_flow(t, "credit_card", "spending", True, HINTS) == "card_payment"
    assert bank_flows.classify_flow(t, "spending", "credit_card", True, HINTS) == "card_payment"
    plain = txn("b", 1, "2026-07-01", 2000.0, description="SOMETHING OPAQUE")
    assert bank_flows.classify_flow(plain, "credit_card", "spending", True, HINTS) == "card_payment"


def test_investment_still_beats_the_unpaired_card_payment_rule():
    """Precedence pin: rule 1 keeps winning even with payment wording."""
    t = txn("a", 1, "2026-07-01", 2000.0, description="ONLINE PAYMENT - THANK YOU")
    assert bank_flows.classify_flow(t, "investment", None, False, HINTS) == "investment"
    assert bank_flows.classify_flow(t, "credit_card", "investment", True, HINTS) == "investment"


def test_unpaired_card_payment_is_not_flagged_ambiguous():
    t = txn("a", 1, "2026-07-01", 1500.0, description="ONLINE PAYMENT - THANK YOU")
    flow = bank_flows.classify_flow(t, "credit_card", None, False, HINTS)
    assert bank_flows.is_ambiguous(t, flow) is False


def test_classify_all_moves_an_unpaired_card_payment_out_of_inflow_unknown():
    txns = [
        txn("a", 2, "2026-07-01", 3345.0, description="Payment Thank You - Web"),
        txn("b", 2, "2026-07-05", 61.23, description="REI.COM RETURN"),
    ]
    out = bank_flows.classify_all(txns, {2: "credit_card"}, {}, HINTS)
    assert out["a"] == ("card_payment", None, False)
    assert out["b"] == ("inflow_unknown", None, False)


def test_payment_wording_requires_a_word_boundary():
    """Uses the existing `_hint_matches_field` discipline — no bare-substring
    sweep-ups like 'preonline paymentx'."""
    t = txn("a", 1, "2026-07-01", 100.0, description="PREONLINE PAYMENTX")
    assert bank_flows.classify_flow(t, "credit_card", None, False, HINTS) == "inflow_unknown"


def test_is_ambiguous_respects_word_boundaries():
    """`is_ambiguous` routes through the module's own `_hint_matches_field`, the
    same discipline every other hint match uses. Over-flagging is not free: each
    false flag is a row the user has to triage by hand."""
    t = txn("a", 1, "2026-07-01", -10.0, description="XFERRED TO SAVINGS")
    assert bank_flows.is_ambiguous(t, "spending") is False


def test_wire_hint_does_not_match_verizon_wireless():
    """Real data: 'VERIZON WIRELESS PAYMENTS' is a phone bill, not a wire — 3
    rows / $795.77 swept up by the raw substring test."""
    t = txn("a", 1, "2026-07-01", -265.26, payee="Verizon Wireless",
            description="VERIZON WIRELESS PAYMENTS")
    assert bank_flows.is_ambiguous(t, "spending") is False


def test_a_genuine_wire_transfer_is_still_flagged():
    t = txn("a", 1, "2026-07-01", -2500.0, description="WIRE TRANSFER OUT")
    assert bank_flows.is_ambiguous(t, "spending") is True


def test_every_ambiguous_hint_still_matches_its_real_world_form():
    """Word boundaries must not silently retire a hint. Each phrase below is the
    wording the hint was written for."""
    real_forms = {
        "venmo": "VENMO PAYMENT 1044202",
        "zelle": "ZELLE TO EXAMPLE A",
        "cash app": "CASH APP*SARAH J",
        "cashapp": "CASHAPP TRANSFER",
        "apple cash": "APPLE CASH SENT MONEY",
        "paypal": "PAYPAL *JOHNDOE",
        "atm": "ATM WITHDRAWAL AUTHORIZED",
        "withdrawal": "ATM WITHDRAWAL AUTHORIZED",
        "transfer": "ONLINE TRANSFER TO SAVINGS",
        "xfer": "MOBILE XFER TO CHECKING",
        "wire": "WIRE TRANSFER OUT",
    }
    assert set(real_forms) == set(bank_flows.AMBIGUOUS_HINTS)
    for hint, description in real_forms.items():
        t = txn("a", 1, "2026-07-01", -10.0, description=description)
        assert bank_flows.is_ambiguous(t, "spending") is True, hint


# ── Suppressions: wording that trips a hint but is definitively spending ──────
# Descriptions below are copied verbatim from the real 90-day dataset, column
# padding included — the padding is why the PayPal rule can't assume one space.

PAYPAL_PURCHASE_DESC = (
    "PAYPAL           PURCHASE   260519 CHEWY INC       THOMAS KEEFE           "
)
ATM_FEE_DESC = "NON-WELLS FARGO ATM TRANSACTION FEE"
ATM_WITHDRAWAL_DESC = (
    "NON-WF ATM WITHDRAWAL          AUTHORIZED ON   06/16 9999 W. 38TH AVENUE  "
    "     WHEAT RIDGE   CO  356167514886913   ATM ID LK340169 CARD 7872"
)


def test_paypal_purchase_is_not_flagged():
    """PayPal acting as a card network for a merchant — 56 rows / $1,506.85 of
    ordinary spending, nothing to triage."""
    t = txn("a", 1, "2026-07-01", -95.17, payee="Chewy",
            description=PAYPAL_PURCHASE_DESC)
    assert bank_flows.is_ambiguous(t, "spending") is False


def test_paypal_p2p_to_a_person_is_still_flagged():
    """The case the `paypal` hint exists for — 5 rows / $1,833.08."""
    t = txn("a", 1, "2026-07-01", -700.0, payee="Securedreli",
            description="PAYPAL *SECUREDRELI")
    assert bank_flows.is_ambiguous(t, "spending") is True


def test_paypal_remains_an_ambiguous_hint():
    """The suppression is a carve-out, not a retirement of the hint."""
    assert "paypal" in bank_flows.AMBIGUOUS_HINTS


def test_atm_fee_is_not_flagged():
    """A bank fee is money spent, not money moved — 13 rows, all exactly $3.00."""
    t = txn("a", 1, "2026-07-01", -3.0, payee="ATM Fee",
            description=ATM_FEE_DESC)
    assert bank_flows.is_ambiguous(t, "spending") is False


def test_atm_withdrawal_is_still_flagged():
    """13 rows / $1,139.10 — the genuine deferred-policy case: cash out of an
    ATM may be spending or may be a movement, and we haven't decided."""
    t = txn("a", 1, "2026-07-01", -203.50, payee="ATM Withdrawal",
            description=ATM_WITHDRAWAL_DESC)
    assert bank_flows.is_ambiguous(t, "spending") is True


def test_suppressions_never_change_the_flow():
    """`ambiguous` is a triage marker only. Both suppressed shapes must still
    classify — and still be reported — as spending."""
    txns = [
        txn("a", 1, "2026-07-01", -95.17, payee="Chewy",
            description=PAYPAL_PURCHASE_DESC),
        txn("b", 1, "2026-07-02", -3.0, payee="ATM Fee", description=ATM_FEE_DESC),
    ]
    out = bank_flows.classify_all(txns, {1: "spending"}, {}, HINTS)
    assert out["a"] == ("spending", None, False)
    assert out["b"] == ("spending", None, False)


# ── triage_signature: a counterparty grouping token, not a classification ─────
# The token is used only to bulk-select rows in the triage queue's UI; it never
# feeds classify_flow or is_ambiguous, and it must return "" for anything the
# suppressions already excluded from the queue so it can never group a row the
# user will never see.

def test_triage_signature_venmo():
    t = txn("a", 1, "2026-07-01", -20.0,
            description="VENMO PAYMENT 1234567 THOMAS KEEFE")
    assert bank_flows.triage_signature(t) == "venmo"


def test_triage_signature_zelle():
    t = txn("a", 1, "2026-07-01", -20.0, description="ZELLE TO ...")
    assert bank_flows.triage_signature(t) == "zelle"


def test_triage_signature_cash_app():
    t = txn("a", 1, "2026-07-01", -20.0, description="CASH APP*...")
    assert bank_flows.triage_signature(t) == "cash app"


def test_triage_signature_apple_cash():
    t = txn("a", 1, "2026-07-01", -20.0, description="APPLE CASH ...")
    assert bank_flows.triage_signature(t) == "apple cash"


def test_triage_signature_atm():
    t = txn("a", 1, "2026-07-01", -20.0, description="NON-WF ATM WITHDRAWAL ...")
    assert bank_flows.triage_signature(t) == "atm"


def test_triage_signature_paypal():
    t = txn("a", 1, "2026-07-01", -20.0, description="PAYPAL *URDANETAEVELIN")
    assert bank_flows.triage_signature(t) == "paypal"


def test_triage_signature_suppressed_paypal_purchase_never_groups():
    """Suppressed from ambiguity entirely, so it can never be in the triage
    queue — and must not group as if it were."""
    t = txn("a", 1, "2026-07-01", -95.17, payee="Chewy",
            description=PAYPAL_PURCHASE_DESC)
    assert bank_flows.triage_signature(t) == ""


def test_triage_signature_suppressed_atm_fee_never_groups():
    t = txn("a", 1, "2026-07-01", -3.0, payee="ATM Fee", description=ATM_FEE_DESC)
    assert bank_flows.triage_signature(t) == ""


def test_triage_signature_no_opinion_for_ordinary_merchant():
    t = txn("a", 1, "2026-07-01", -42.0, payee="Trader Joes",
            description="TRADER JOES #123")
    assert bank_flows.triage_signature(t) == ""


def test_triage_signature_matches_payee_only():
    t = txn("a", 1, "2026-07-01", -20.0, payee="Venmo", description="")
    assert bank_flows.triage_signature(t) == "venmo"


def test_triage_signature_matches_description_only():
    t = txn("a", 1, "2026-07-01", -20.0, payee="", description="ZELLE TO EXAMPLE A")
    assert bank_flows.triage_signature(t) == "zelle"


def test_triage_signature_empty_fields_yield_no_opinion():
    t = txn("a", 1, "2026-07-01", -20.0, payee="", description="")
    assert bank_flows.triage_signature(t) == ""


def test_triage_signature_word_boundary_atm_not_substring():
    """'atm' is a substring of 'BATMAN' but not a word-boundary match.
    A naive substring matcher would wrongly return 'atm'; word-boundary
    matching must return '' instead."""
    t = txn("a", 1, "2026-07-01", -42.0, payee="BATMAN COMICS 123",
            description="")
    assert bank_flows.triage_signature(t) == ""


def test_triage_signature_cashapp_no_space_variant():
    """The 'cashapp' hint (no space) must still match; word-boundary discipline
    applies to all token variants."""
    t = txn("a", 1, "2026-07-01", -20.0, payee="CASHAPP*JOHN DOE",
            description="")
    assert bank_flows.triage_signature(t) == "cash app"


def test_classify_flow_never_returns_refund():
    """`refund` is a user-only verdict (spec §1) — the classifier must never
    produce it, across a representative grid of roles, pairing, amount sign,
    and income-hint match. This is the guard that keeps refund user-only:
    it must fail if a future "helpful" classifier change starts inventing it."""
    import itertools

    roles = ("spending", "credit_card", "savings", "investment", "unknown")
    for role, partner_role, paired, amount, hint_match in itertools.product(
        roles, roles + (None,), (True, False), (150.0, -150.0), (True, False)
    ):
        hints = HINTS if hint_match else []
        t = txn("a", 1, "2026-07-01", amount,
                 payee="acme payroll" if hint_match else "some merchant",
                 description="PAYMENT - THANK YOU")
        flow = bank_flows.classify_flow(t, role, partner_role, paired, hints)
        assert flow != "refund", (
            f"classify_flow returned 'refund' for role={role!r}, "
            f"partner_role={partner_role!r}, paired={paired}, amount={amount}, "
            f"hint_match={hint_match}"
        )


# ── vendor_key + label_suggestions: same-vendor label inheritance ──────────────

def _ltxn(sfid, payee, user_label=None, description="RAW"):
    return {"simplefin_id": sfid, "payee": payee, "description": description,
            "user_label": user_label}


def test_vendor_key_prefers_payee_falls_back_to_description():
    assert bank_flows.vendor_key(_ltxn("a", "Amazon")) == "Amazon"
    assert bank_flows.vendor_key(_ltxn("b", "", description="CHECK 1042")) == "CHECK 1042"


def test_label_suggestions_unanimous_vendor_propagates():
    txns = [
        _ltxn("r1", "Check", user_label="Monthly Rent"),
        _ltxn("r2", "Check"),
        _ltxn("x1", "Cafe"),
    ]
    assert bank_flows.label_suggestions(txns) == {
        "r1": None,          # already user-labeled — nothing to suggest
        "r2": "Monthly Rent",
        "x1": None,          # vendor has no user labels
    }


def test_label_suggestions_conflicting_vendor_stays_silent():
    txns = [
        _ltxn("a1", "Amazon", user_label="Household"),
        _ltxn("a2", "Amazon", user_label="Gifts"),
        _ltxn("a3", "Amazon"),
    ]
    assert bank_flows.label_suggestions(txns)["a3"] is None


def test_label_suggestions_uses_vendor_key_fallback():
    txns = [
        _ltxn("c1", "", user_label="Monthly Rent", description="CHECK 1042"),
        _ltxn("c2", "", description="CHECK 1042"),
    ]
    assert bank_flows.label_suggestions(txns)["c2"] == "Monthly Rent"


def test_label_suggestions_no_identity_rows_never_inherit():
    txns = [
        _ltxn("e1", "", user_label="Misc", description=""),
        _ltxn("e2", "", description=""),
    ]
    assert bank_flows.label_suggestions(txns)["e2"] is None


def test_label_suggestions_skips_rejected_rows():
    import bank_flows
    txns = [
        _ltxn("r1", "Check", user_label="Monthly Rent"),
        {**_ltxn("r2", "Check"), "user_no_label": True},
        _ltxn("r3", "Check"),
    ]
    result = bank_flows.label_suggestions(txns)
    assert result["r2"] is None
    assert result["r3"] == "Monthly Rent"
