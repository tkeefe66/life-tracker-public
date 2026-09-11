"""Pure metric computation — no DB, no I/O."""

import re
from datetime import date, timedelta

METRICS = {
    "delivery": {"label": "Delivery orders", "direction": "ceiling", "default_target": 1},
    "gym": {"label": "Gym sessions", "direction": "floor", "default_target": 3},
    "social": {"label": "Social events", "direction": "floor", "default_target": 2},
    "alcohol": {"label": "Alcohol days", "direction": "ceiling", "default_target": 2},
    "substances": {"label": "Substances", "direction": "ceiling", "default_target": 0, "private": True},
}


def week_bounds(d):
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


# ── Effective date (night cutoff) — applied to rides only, see repo guide ────

NIGHT_CUTOFF_HOUR = 4


# ── Social classification ambiguity threshold ────────────────────────────────
# The single named constant for "the classifier isn't confident enough to
# trust silently" — below this, an unresolved event surfaces as `uncertain`
# in the day payload instead of quietly following the AI's lean. See
# docs/superpowers/specs/2026-07-30-social-classification-granularity-design.md.
AMBIGUOUS_CONFIDENCE = 0.7


def effective_date(ts: str) -> date:
    """A timestamp before NIGHT_CUTOFF_HOUR local belongs to the PREVIOUS
    calendar day. Handles 'YYYY-MM-DDTHH:MM' and 'YYYY-MM-DDTHH:MM:SS' shapes.
    Any trailing UTC offset (e.g. '-06:00') is ignored on purpose — every
    timestamp in this system already represents local wall-clock time, so the
    offset is inert metadata, never a conversion instruction. This is the pure
    Python twin of database._effective_date_expr, the SQL expression used to
    bucket/filter rides at query time — both must agree on the exact cutoff."""
    day = date.fromisoformat(ts[:10])
    hour = int(ts[11:13])
    if hour < NIGHT_CUTOFF_HOUR:
        return day - timedelta(days=1)
    return day


def nudge_user_date(auto_day, requested, today):
    """Value to store in `user_date` for a ±1-day nudge: None clears the
    override (requested == the automatic day), an ISO string stores it
    (requested is exactly one day off the automatic day). Anything else —
    including any future day — raises ValueError. Pure: the caller supplies
    `today` so this stays clock-free."""
    if requested > today:
        raise ValueError("cannot move an item into the future")
    if requested == auto_day:
        return None
    if abs((requested - auto_day).days) == 1:
        return requested.isoformat()
    raise ValueError("day must be within one day of the automatic day")


def title_is_date(title: str) -> bool:
    """Rule-based date detection — the user's explicit scope: 'only look for
    things that say Date, besides that it's just manual'. A word-boundary
    match so 'Update sync' / 'Candidate interview' never fire; deliberately
    NOT AI (see the 2026-07-30 date-tracking spec's rejected alternatives).
    Plural 'dates' doesn't match: the signal is the literal word."""
    return bool(re.search(r"\bdate\b", title or "", re.IGNORECASE))


def is_hit(direction, count, target):
    return count <= target if direction == "ceiling" else count >= target


def build_scorecard(week_start, counts, targets):
    ws, we = week_bounds(week_start)
    out = {}
    for key, meta in METRICS.items():
        t = targets.get(key, {"direction": meta["direction"], "value": meta["default_target"]})
        count = counts.get(key, 0)
        out[key] = {
            "label": meta["label"],
            "count": count,
            "target": t["value"],
            "direction": t["direction"],
            "hit": is_hit(t["direction"], count, t["value"]),
        }
    return {"week_start": ws.isoformat(), "week_end": we.isoformat(), "metrics": out}


def streaks(history):
    out = {}
    for key in METRICS:
        n = 0
        for card in reversed(history):
            if card["metrics"][key]["hit"]:
                n += 1
            else:
                break
        out[key] = n
    return out


WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def weekday_counts(dates):
    """ISO date strings -> counts per weekday, Monday-first."""
    out = [0] * 7
    for d in dates:
        out[date.fromisoformat(d).weekday()] += 1
    return out


def trend_direction(series):
    """Weekly counts oldest-first. None if fewer than 6 weeks."""
    if len(series) < 6:
        return None
    recent = series[-6:]
    delta = sum(recent[3:]) / 3 - sum(recent[:3]) / 3
    if delta >= 1:
        return "up"
    if delta <= -1:
        return "down"
    return "flat"


def weekday_skew(dates):
    """(weekday_index, share) when one weekday dominates; else None.
    Thresholds: >= 4 total events, max weekday >= 3 events and >= 40% share."""
    counts = weekday_counts(dates)
    total = sum(counts)
    if total < 4:
        return None
    mx = max(counts)
    if mx < 3 or mx / total < 0.4:
        return None
    return counts.index(mx), mx / total


def co_occurrence(dates_a, dates_b):
    """Jaccard overlap of two day sets; None unless both have >= 4 distinct days."""
    a, b = set(dates_a), set(dates_b)
    if len(a) < 4 or len(b) < 4:
        return None
    return len(a & b) / len(a | b)


def noticings(date_lists, series):
    """<= 3 plain-language statements. Priority: co-occurrence, weekday skew, trend."""
    out = []
    j = co_occurrence(date_lists.get("alcohol", []), date_lists.get("delivery", []))
    if j is not None and j >= 0.5:
        out.append("Alcohol days and delivery orders often land on the same day.")
    for key in METRICS:
        skew = weekday_skew(date_lists.get(key, []))
        if skew:
            day, share = skew
            out.append(f"{METRICS[key]['label']} cluster on {WEEKDAY_NAMES[day]}s ({round(share * 100)}% of them).")
    for key in METRICS:
        t = trend_direction(series.get(key, []))
        if t in ("up", "down"):
            out.append(f"{METRICS[key]['label']} trending {t} over the last six weeks.")
    return out[:3]
