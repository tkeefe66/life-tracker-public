"""Rule-based food-delivery receipt detection. Ambiguous cases go to ai_metrics."""
import re

DELIVERY_DOMAINS = {
    "uber.com": "Uber Eats",
    "ubereats.com": "Uber Eats",
    "doordash.com": "DoorDash",
    "grubhub.com": "Grubhub",
    "seamless.com": "Seamless",
    "postmates.com": "Postmates",
    "trycaviar.com": "Caviar",
}

_ORDER_RE = re.compile(r"\b(order|receipt|delivered|delivery confirmation)\b", re.IGNORECASE)
_RIDE_RE = re.compile(r"\b(trip|ride|driver)\b", re.IGNORECASE)
_PROMO_RE = re.compile(r"(\d+% off|promo|deal|free delivery|save \$|don't miss|invite)", re.IGNORECASE)
_FOLLOWUP_RE = re.compile(
    r"(thanks for tipping|refunded|adjusted the total|has been cancell?ed)",
    re.IGNORECASE,
)
_TIP_RE = re.compile(r"thanks for tipping", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"Total \$([\d,]+(?:\.\d{2})?)")


def is_followup(snippet) -> bool:
    """True for follow-up emails about an existing order (tip receipt,
    refund adjustment, cancellation notice) — not a new order."""
    return bool(_FOLLOWUP_RE.search(snippet or ""))


def is_tip_receipt(snippet) -> bool:
    return bool(_TIP_RE.search(snippet or ""))


def extract_amount(snippet):
    """Order total from a receipt snippet, or None."""
    m = _AMOUNT_RE.search(snippet or "")
    return float(m.group(1).replace(",", "")) if m else None


def _sender_domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender)
    if not m:
        return ""
    domain = m.group(1).lower().rstrip(">")
    for known in DELIVERY_DOMAINS:
        if domain == known or domain.endswith("." + known):
            return known
    return ""


def classify_candidate(sender: str, subject: str) -> tuple:
    domain = _sender_domain(sender)
    if not domain:
        return "not_order", ""
    service = DELIVERY_DOMAINS[domain]
    if _RIDE_RE.search(subject):
        return "not_order", service
    if _PROMO_RE.search(subject):
        return "not_order", service
    if _ORDER_RE.search(subject):
        return "order", service
    return "ambiguous", service


# ── Rides (Uber / Lyft) ────────────────────────────────────────────────────────

RIDE_DOMAINS = {"uber.com": "Uber", "lyft.com": "Lyft"}

_ORDER_WORDS_RE = re.compile(r"\b(order|eats)\b", re.IGNORECASE)
_RIDE_TIME_RE = re.compile(
    r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})[, ]+(\d{1,2}):(\d{2})\s*([AP]M)",
    re.IGNORECASE,
)
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _ride_domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender)
    if not m:
        return ""
    domain = m.group(1).lower().rstrip(">")
    for known in RIDE_DOMAINS:
        if domain == known or domain.endswith("." + known):
            return known
    return ""


def classify_ride(sender: str, subject: str) -> tuple:
    """('ride'|'not_ride'|'ambiguous', service)."""
    domain = _ride_domain(sender)
    if not domain:
        return "not_ride", ""
    service = RIDE_DOMAINS[domain]
    if _PROMO_RE.search(subject):
        return "not_ride", service
    if _ORDER_WORDS_RE.search(subject):
        return "not_ride", service
    if _RIDE_RE.search(subject):
        return "ride", service
    return "ambiguous", service


_CANCELLATION_RE = re.compile(r"cancell?ed trip", re.IGNORECASE)


def is_cancellation_fee(snippet) -> bool:
    """True for a canceled-trip fee receipt — keyed on Uber's "canceled trip"
    phrasing (spelling varies: canceled/cancelled). A completed-trip charge
    summary or a thanks-for-riding follow-up never uses this phrase, so this
    is label-only: it never changes whether a ride counts or how much it
    contributes to spend (see repo guide). Note extract_ride_time still works
    on cancellation snippets — the parsed time appears first — so dedupe is
    unaffected."""
    return bool(_CANCELLATION_RE.search(snippet or ""))


def extract_ride_time(snippet):
    """'Jul 19, 2026 4:03 AM' -> '2026-07-19T04:03'; None if absent."""
    m = _RIDE_TIME_RE.search(snippet or "")
    if not m:
        return None
    mon, day, year, hour, minute, ampm = m.groups()
    if mon not in _MONTHS:
        return None
    h = int(hour) % 12 + (12 if ampm.upper() == "PM" else 0)
    return f"{year}-{_MONTHS[mon]:02d}-{int(day):02d}T{h:02d}:{minute}"
