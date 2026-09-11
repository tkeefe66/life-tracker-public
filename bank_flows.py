"""Pure computation for bank ingestion: pair matching and flow classification.

No database, no network, no Claude — the same role metrics.py and receipts.py
play. Everything here is deterministic and re-runnable: the same input always
produces the same output, which is what lets the sync job re-classify from
scratch on every run without churning the database.

The central problem this module exists to solve: about a quarter of the user's
transactions are money *moving*, not money *spent*. Summing outflows naively
would double-count every credit-card purchase and invent spending from
checking-to-checking transfers.
"""
import re
from datetime import date

# Transfer-ish wording that we can't yet prove is a transfer. An unpaired
# transaction matching one of these stays `spending` (never silently reclassified)
# but is flagged for later triage. The Venmo/Zelle/ATM policy is deliberately
# deferred — flagging costs nothing and avoids inventing a rule.
AMBIGUOUS_HINTS = (
    "venmo", "zelle", "cash app", "cashapp", "apple cash", "paypal",
    "atm", "withdrawal", "transfer", "xfer", "wire",
)

# Wording issuers use on the credit-card side of a payment. Deliberately narrow:
# these four phrases cover every real unpaired card payment in the 90-day
# dataset (Chase "Payment Thank You - Web", Amex "ONLINE PAYMENT - THANK YOU")
# while excluding all 20 merchant refunds and statement credits, which never use
# payment wording. Do not broaden to bare "payment" or "credit" — a refund would
# then be misread as debt paydown.
CARD_PAYMENT_HINTS = (
    "payment thank you", "payment received", "online payment", "mobile payment",
)

# ── Ambiguity suppressions ────────────────────────────────────────────────────
# Wording that trips an AMBIGUOUS_HINT but is definitively money *spent*, so
# there is nothing for the user to decide. Each is a narrow carve-out around one
# real-world phrasing, never a retirement of the hint itself — the hint keeps
# doing its job on every other shape. A suppression only ever clears the triage
# flag; it can never reach `classify_flow`, so no flow can change.

# "PAYPAL           PURCHASE   260519 CHEWY INC       THOMAS KEEFE"
# PayPal standing in as a card network for an ordinary merchant — Apple, Chewy,
# Netflix, Spotify, Uber, Uber Eats, PlayStation, DraftKings, City of Aurora.
# 56 rows / $1,506.85 in the 90-day dataset, none of them a money movement.
# The P2P form the `paypal` hint actually exists for is "PAYPAL *URDANETAEVELIN"
# (5 rows / $1,833.08): a person's handle after a star, no PURCHASE token. The
# gap between the two words is column padding in the raw feed, hence `\s+`.
_PAYPAL_CARD_PURCHASE_RE = re.compile(r"paypal\s+purchase")

# "NON-WELLS FARGO ATM TRANSACTION FEE", payee "ATM Fee" — 13 rows, all exactly
# $3.00. A bank fee is money the bank took, definitively spending. Deliberately
# does NOT cover "NON-WF ATM WITHDRAWAL ..." (13 rows / $1,139.10): the cash
# withdrawal itself is the genuine deferred-policy case the `atm` hint is for.
_ATM_FEE_RE = re.compile(r"atm\s+transaction\s+fee")


def _is_paypal_card_purchase(payee, description):
    text = f"{payee or ''} {description or ''}".lower()
    return _PAYPAL_CARD_PURCHASE_RE.search(text) is not None


def _is_atm_fee(payee, description):
    if (payee or "").strip().lower() == "atm fee":
        return True
    return _ATM_FEE_RE.search((description or "").lower()) is not None


AMBIGUITY_SUPPRESSIONS = (
    ("paypal card-network purchase", _is_paypal_card_purchase),
    ("bank ATM fee", _is_atm_fee),
)


def _cents(amount) -> int:
    """Money compares as integer cents. Float equality would silently fail to
    pair a legitimate transfer, which turns into phantom spending."""
    return int(round(float(amount) * 100))


def _day(posted) -> date:
    return date.fromisoformat(str(posted)[:10])


def vendor_key(txn) -> str:
    """The vendor identity used everywhere labels and the breakdown group:
    payee, falling back to description when the bridge sent no payee."""
    return txn.get("payee") or txn.get("description") or ""


def label_suggestions(txns):
    """Same-vendor label inheritance, computed from scratch each sync (like
    classify_all, unlike suggested_flow's write-once): a vendor's unlabeled
    rows inherit its user label only when every user label on that vendor
    agrees — any conflict and the vendor stays silent. Returns a suggestion
    (or None) for EVERY row, so writing the result also retires stale
    suggestions whose vendor lost its labels. Rows with no vendor identity
    (both payee and description empty, vendor_key == "") never inherit —
    pooling them would propagate one no-identity row's label to every other
    unrelated no-identity row. Rows carrying the user's no-label verdict
    never inherit."""
    labels_by_vendor = {}
    for t in txns:
        # A row carrying both a label and the no-label flag is illegal (the
        # writers enforce exclusivity) — but this function takes opaque dicts,
        # so a rejected row's label must never seed the inheritance pool.
        if t.get("user_label") and not t.get("user_no_label"):
            labels_by_vendor.setdefault(vendor_key(t), set()).add(t["user_label"])
    unanimous = {v: next(iter(ls)) for v, ls in labels_by_vendor.items() if v and len(ls) == 1}
    return {
        t["simplefin_id"]: (None if t.get("user_label") or t.get("user_no_label")
                            else unanimous.get(vendor_key(t)))
        for t in txns
    }


# ── Text corroboration ────────────────────────────────────────────────────────
# Amount + date + different-account is not enough on its own: when several
# equal-amount rows land inside the pairing window they cross-wire, and one
# mis-pair invents phantom spending on one side and a phantom inflow on the
# other. Descriptions frequently name the counterparty — Wells Fargo online
# transfers state its last four digits, card payments name the issuer — so we
# use that text to RANK candidates. It is a tie-break, never a filter: a row
# with no extractable signature ranks exactly as it did before, and a single
# contradicting candidate still pairs.

# The masked account number Wells Fargo embeds in transfer descriptions,
# e.g. "ONLINE TRANSFER TO EXAMPLE A EVERYDAY CHECKING XXXXXX1003 REF #...".
_STATED_LAST4_RE = re.compile(r"X{4,}(\d{4})")

# Trailing "(1234)" in a SimpleFIN account name, e.g. "Example Card (1004)".
_ACCOUNT_LAST4_RE = re.compile(r"\((\d{4})\)\s*$")

# Issuer wording that appears in the *paying* account's description (the card's
# own side usually says only "ONLINE PAYMENT - THANK YOU"), mapped to a keyword
# matched against the counterparty account's org/name.
ISSUER_SIGNATURES = (
    ("american express", "american express"),
    ("barclaycard", "barclay"),
    ("chase credit", "chase"),
)


def _account_last4(account) -> str:
    name = str(account.get("name") or "")
    m = _ACCOUNT_LAST4_RE.search(name)
    return m.group(1) if m else ""


def _build_account_index(accounts):
    """{last4 -> {account_id}} and {issuer signature -> {account_id}}.

    Both are derived from the accounts actually passed in — nothing about this
    user's specific account numbers is hardcoded.
    """
    by_last4, by_issuer = {}, {}
    for a in accounts or []:
        aid = a.get("id")
        if aid is None:
            continue
        last4 = _account_last4(a)
        if last4:
            by_last4.setdefault(last4, set()).add(aid)
        haystack = f"{a.get('org') or ''} {a.get('name') or ''}".lower()
        for signature, keyword in ISSUER_SIGNATURES:
            if keyword in haystack:
                by_issuer.setdefault(signature, set()).add(aid)
    return by_last4, by_issuer


def _stated_counterparty_ids(txn, by_last4, by_issuer):
    """Account ids this transaction's own text claims as the other side.

    Empty means "no opinion" — an unrecognised last-four maps to nothing and is
    treated as silence, not as a contradiction.
    """
    text = f"{txn.get('description') or ''} {txn.get('payee') or ''}"
    ids = set()
    for last4 in _STATED_LAST4_RE.findall(text.upper()):
        ids |= by_last4.get(last4, set())
    lowered = text.lower()
    for signature, _ in ISSUER_SIGNATURES:
        if signature in lowered:
            ids |= by_issuer.get(signature, set())
    return ids


def _contradicts(out_txn, candidate, stated):
    """1 when either side names a counterparty and the other side isn't it.

    Symmetric on purpose: only one half of a real transfer usually carries the
    identifying text, and either half's claim is equally good evidence.
    """
    out_ids = stated[out_txn["simplefin_id"]]
    cand_ids = stated[candidate["simplefin_id"]]
    if out_ids and candidate["account_id"] not in out_ids:
        return 1
    if cand_ids and out_txn["account_id"] not in cand_ids:
        return 1
    return 0


def match_pairs(txns, window_days=3, accounts=None):
    """Find the two halves of each money movement.

    Two transactions pair when ALL hold:
      - different accounts
      - amounts equal in absolute value and opposite in sign
      - `posted` dates within `window_days`
      - neither is already paired

    Returns {simplefin_id: pair_id} for NEWLY matched transactions only;
    already-paired rows are excluded from the result but still consume their
    partner. `pair_id` is the lexicographically smaller of the two ids, so the
    value is stable across re-runs.

    Ties break by text corroboration (a candidate contradicting a stated
    counterparty sorts last), then smallest date gap, then lowest partner id —
    deterministic and independent of input order. `accounts` is optional; omit
    it and ranking falls back to exactly the date-gap/id behaviour.
    """
    by_last4, by_issuer = _build_account_index(accounts)
    free = [t for t in txns if not t.get("pair_id")]
    # Sort so iteration order never depends on how the caller ordered its query.
    free.sort(key=lambda t: (str(t["posted"]), str(t["simplefin_id"])))

    stated = {t["simplefin_id"]: _stated_counterparty_ids(t, by_last4, by_issuer)
              for t in free}

    # Index the positive side by absolute cents; outflows go looking for a partner.
    by_cents = {}
    for t in free:
        if _cents(t["amount"]) > 0:
            by_cents.setdefault(abs(_cents(t["amount"])), []).append(t)

    taken = set()
    matched = {}
    for out_txn in free:
        if _cents(out_txn["amount"]) >= 0 or out_txn["simplefin_id"] in taken:
            continue
        key = abs(_cents(out_txn["amount"]))
        out_day = _day(out_txn["posted"])

        candidates = [
            c for c in by_cents.get(key, [])
            if c["simplefin_id"] not in taken
            and c["account_id"] != out_txn["account_id"]
            and abs((_day(c["posted"]) - out_day).days) <= window_days
        ]
        if not candidates:
            continue
        partner = min(candidates, key=lambda c: (_contradicts(out_txn, c, stated),
                                                 abs((_day(c["posted"]) - out_day).days),
                                                 str(c["simplefin_id"])))
        pair_id = min(str(out_txn["simplefin_id"]), str(partner["simplefin_id"]))
        matched[out_txn["simplefin_id"]] = pair_id
        matched[partner["simplefin_id"]] = pair_id
        taken.add(out_txn["simplefin_id"])
        taken.add(partner["simplefin_id"])

    return matched


def _hint_matches_field(hint, field):
    """Word-boundary substring match: `hint` must already be stripped + lowercased
    by the caller; this function lowercases `field` (case-insensitive) and matches
    `hint` with no word character immediately before or after it. Prevents a short
    hint like "pay" or "ach" from sweeping up unrelated deposits ("payroll",
    "beach") — see Finding 6 in the Task 4 review."""
    if not hint:
        return False
    pattern = r"(?<!\w)" + re.escape(hint) + r"(?!\w)"
    return re.search(pattern, (field or "").lower()) is not None


def classify_flow(txn, role, partner_role, paired, income_hints):
    """Classify one transaction. Rules apply in order; the first match wins, and
    only the final fallback is a guess.

    `role` is the role of this transaction's own account; `partner_role` is the
    role of the account on the other half of a matched pair, or None if that
    partner's role is unknown (including a partner that exists but has aged out
    of the current classification window). `paired` is the explicit signal for
    "this transaction is one half of a matched pair" — it must NOT be inferred
    from `partner_role is not None`, because a partner can be legitimately
    matched (`pair_id` set) while its row is absent from this window, which
    would otherwise present identically to a genuinely unpaired transaction.
    """
    # 1. Investment — either side. Contributions and the backdoor-Roth conversion
    #    leg are saving, never spending. Investment-to-investment contributes to
    #    nothing on either side.
    if role == "investment" or partner_role == "investment":
        return "investment"

    # 2. Card payment — a matched pair with a credit card on one side. The
    #    purchases are already recorded on the card; counting the payment too
    #    would double-count. Reported separately so paydown reads as progress.
    #    Knowing THIS side is a credit card is enough even if the partner's role
    #    is unknown (partner missing from the window).
    if paired and "credit_card" in (role, partner_role):
        return "card_payment"

    # 3. Transfer — any other matched pair between two known accounts. Also the
    #    landing spot when a matched pair's partner has aged out of the window:
    #    a paired row must never fall through to rules 4/5/6/7 just because its
    #    partner isn't visible right now — that would invent phantom spending or
    #    (worse) mislabel a payroll-shaped transfer as income.
    if paired:
        return "transfer"

    amount = float(txn["amount"])

    # 4. Unpaired card payment — a positive amount on a credit-card account whose
    #    text uses issuer payment wording. The paying account is not connected to
    #    SimpleFIN (the SoFi hazard), so no opposite outflow exists to pair with,
    #    but a credit to a card that says "PAYMENT - THANK YOU" cannot be income
    #    and cannot be spending: it is unambiguously debt paydown. Scoped by role
    #    AND by wording so merchant refunds and statement credits — which post to
    #    the same accounts with the same sign — stay in `inflow_unknown`.
    #    Sits below rules 1-3: investment still wins, and every paired row has
    #    already returned, so nothing that classifies today changes.
    if amount > 0 and role == "credit_card":
        payee = txn.get("payee")
        description = txn.get("description")
        for hint in CARD_PAYMENT_HINTS:
            if _hint_matches_field(hint, payee) or _hint_matches_field(hint, description):
                return "card_payment"

    # 5. Income — an unpaired deposit into a spending/bills account whose payee or
    #    description matches a configured payroll signature. Conservative by
    #    design, and reachable ONLY when genuinely unpaired (see rule 3 above).
    #    A credit_card role never reaches here — rule 4 owns that account role's
    #    inflows, and payroll never lands on a card.
    if amount > 0 and role in ("spending", "bills"):
        payee = txn.get("payee")
        description = txn.get("description")
        for h in income_hints:
            hint = (h or "").strip().lower()
            if not hint:
                continue
            if _hint_matches_field(hint, payee) or _hint_matches_field(hint, description):
                return "income"

    # 6. Any other unpaired deposit. Counted as neither income nor spending.
    #    This is the SoFi hazard guard: money drawn down from an unconnected
    #    savings account arrives here, and must never be reported as earnings.
    if amount > 0:
        return "inflow_unknown"

    # 7. Everything else.
    return "spending"


def is_ambiguous(txn, flow):
    """True when a transaction we called `spending` uses transfer-ish wording.

    It stays `spending` — an AI or keyword flag alone never excludes anything
    silently — but it is surfaced for later triage. The Venmo/Zelle/ATM policy is
    deliberately deferred; flagging costs nothing now.

    Hints match through `_hint_matches_field`, exactly like every other hint in
    this module. A raw substring test made "wire" fire on "VERIZON WIRELESS
    PAYMENTS" (a phone bill, 3 rows / $795.77 in the 90-day dataset) — flagging
    isn't actually free, since every false flag is a row the user must triage.
    """
    if flow != "spending":
        return False
    payee = txn.get("payee")
    description = txn.get("description")
    if not any(_hint_matches_field(hint, payee) or _hint_matches_field(hint, description)
               for hint in AMBIGUOUS_HINTS):
        return False
    # A hint fired. Drop the flag if this is one of the known shapes that reads
    # transfer-ish but is definitively spending (see AMBIGUITY_SUPPRESSIONS).
    return not any(matches(payee, description)
                   for _reason, matches in AMBIGUITY_SUPPRESSIONS)


# Ordered (token, hints) pairs for triage_signature. Order matters: `apple cash`
# and `cash app` must be checked before any bare `cash` token, should one ever
# be added, so a "CASH APP" row doesn't fall through to a looser "cash" match.
TRIAGE_SIGNATURE_TOKENS = (
    ("venmo", ("venmo",)),
    ("zelle", ("zelle",)),
    ("apple cash", ("apple cash",)),
    ("cash app", ("cash app", "cashapp")),
    ("paypal", ("paypal",)),
    ("atm", ("atm",)),
)


def triage_signature(txn) -> str:
    """A counterparty grouping token for the triage queue's bulk-action UI —
    one of "venmo", "zelle", "cash app", "apple cash", "paypal", "atm", or ""
    for no opinion.

    This is a GROUPING aid only: it never reaches a classification decision and
    can never change a flow. It reuses `_hint_matches_field` so it inherits the
    same word-boundary discipline as `is_ambiguous` — a raw substring test is
    what made "wire" fire on "VERIZON WIRELESS".

    Rows the ambiguity suppressions have already carved out (see
    AMBIGUITY_SUPPRESSIONS) can never appear in the triage queue, so they must
    never group either — checked first, before any token.
    """
    payee = txn.get("payee")
    description = txn.get("description")
    if any(matches(payee, description) for _reason, matches in AMBIGUITY_SUPPRESSIONS):
        return ""
    for token, hints in TRIAGE_SIGNATURE_TOKENS:
        for hint in hints:
            if _hint_matches_field(hint, payee) or _hint_matches_field(hint, description):
                return token
    return ""


def classify_all(txns, roles_by_account_id, pair_map, income_hints):
    """Classify a whole window at once.

    `pair_map` is match_pairs()' output (newly matched only); a transaction's
    existing `pair_id` is honoured too, so pairs matched in an earlier sync keep
    their classification.

    Returns {simplefin_id: (flow, pair_id, ambiguous)} — exactly the argument
    triple db.set_bank_transaction_derived takes.
    """
    pair_of = {}
    for t in txns:
        sfid = t["simplefin_id"]
        pair_of[sfid] = pair_map.get(sfid) or t.get("pair_id") or None

    # Who is on the other side of each pair, by account.
    partners = {}
    for t in txns:
        pid = pair_of[t["simplefin_id"]]
        if pid:
            partners.setdefault(pid, []).append(t)

    out = {}
    for t in txns:
        sfid = t["simplefin_id"]
        pid = pair_of[sfid]
        role = roles_by_account_id.get(t["account_id"], "unknown")

        partner_role = None
        if pid:
            others = [o for o in partners.get(pid, []) if o["simplefin_id"] != sfid]
            if others:
                # Sort so the chosen partner (and thus the resulting flow) doesn't
                # depend on input order — matches match_pairs()'s own tie-breaking
                # discipline. Only reachable with 3+ rows sharing a stale pair_id.
                others.sort(key=lambda o: str(o["simplefin_id"]))
                partner_role = roles_by_account_id.get(others[0]["account_id"], "unknown")

        flow = classify_flow(t, role, partner_role, paired=bool(pid), income_hints=income_hints)
        out[sfid] = (flow, pid, is_ambiguous(t, flow))
    return out
