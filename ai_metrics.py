"""All Claude calls for On Track v2. Model choice is cost-sensitive — this runs many
times per day; do not change MODEL without checking cost."""
import json
import logging
import re

import anthropic

from config import ANTHROPIC_API_KEY, COACH_USAGE_TOKEN, COACH_USAGE_URL
from usage import report

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

APP_SLUG = "life-tracker"  # must match a `name` in app-builder-coach's apps.yaml

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


_DECODER = json.JSONDecoder()


def _extract_json_object(s: str) -> dict:
    """Pull the first complete JSON object out of a model response.

    Models routinely wrap the answer in ```json fences and/or pad it with prose
    ("...``` \n\n This is a ride receipt"), which plain json.loads rejects with
    "Extra data". Rather than regex the braces (which breaks on a `}` inside a
    string value), walk each `{` and let the real JSON decoder tell us where the
    object ends — raw_decode stops at the close brace and ignores the rest.
    Raises ValueError when no JSON object is present, so the caller's failure
    path (log + default) is unchanged for genuinely unparseable responses."""
    stripped = _strip_fences(s)
    for text in (stripped, s):
        start = text.find("{")
        while start != -1:
            try:
                parsed, _ = _DECODER.raw_decode(text, start)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
            start = text.find("{", start + 1)
    raise ValueError("no JSON object found in response")


def _call_json(prompt: str, max_tokens: int = 300, default=None):
    raw = ""
    try:
        msg = _get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # Reported before parsing: the call is billed whether or not the
        # response turns out to be readable JSON. Never raises, never blocks.
        report(APP_SLUG, MODEL, getattr(msg, "usage", None),
               url=COACH_USAGE_URL, token=COACH_USAGE_TOKEN)
        raw = msg.content[0].text.strip()
        return _extract_json_object(raw)
    except Exception as e:
        # `raw` is model output generated from personal content (and, once bank
        # data flows through this same helper, transaction descriptions) — cap
        # what lands in logs rather than dumping the full response.
        logger.error("ai_metrics JSON call failed: %s | raw=%r", e, raw[:40])
        return default if default is not None else {}


def classify_receipt(sender: str, subject: str, snippet: str = "") -> bool:
    """True if this email is a receipt/confirmation for a FOOD DELIVERY ORDER
    (not a ride, promo, refund notice, tip receipt, or account email)."""
    prompt = f"""You classify emails. Is this email a receipt or confirmation for a food
delivery ORDER the user placed (Uber Eats, DoorDash, Grubhub, etc.)?

Promotions, ride receipts, refunds, password resets, and newsletters are NOT orders.
Tip receipts, refund adjustments, and cancellation notices for an EXISTING order
are NOT new orders.

From: {sender}
Subject: {subject}
Preview: {snippet[:200]}

Reply with only JSON: {{"is_order": true|false}}"""
    result = _call_json(prompt, default={"is_order": False})
    return bool(result.get("is_order", False))


def classify_social_event(title: str, description: str, location: str, attendees: list, examples=None) -> dict:
    """Classify a calendar event as social (spending leisure time with other people)
    or not. Returns {"is_social": bool, "confidence": float}.

    `examples` — recent user corrections (see database.get_classification_examples) —
    are folded into the prompt so the model learns from past overrides."""
    example_block = ""
    if examples:
        lines = "\n".join(
            f'- "{e["title"]}" IS {"" if e["user_is_social"] else "NOT "}social' for e in examples
        )
        example_block = f"\nThe user has corrected past classifications:\n{lines}\n"

    prompt = f"""You classify calendar events. "Social" means leisure time spent with other
people: dinners, drinks, parties, dates, hangouts, group activities, weddings.

NOT social: work meetings, appointments (doctor, dentist), errands, solo activities
(gym, haircut), reminders, flights, focus blocks.

Some activities are inherently either solo or social depending on who's there — a
movie, a restaurant meal, a hobby that can go either way. When the title,
description, and attendees don't tell you which it was, do not guess: pick your
best lean but return a LOW confidence score, rather than a confident answer you
can't actually support.
{example_block}
Title: {title}
Description: {description[:300]}
Location: {location}
Attendees: {", ".join(attendees) if attendees else "(none listed)"}

Reply with only JSON: {{"is_social": true|false, "confidence": 0.0-1.0}}"""
    result = _call_json(prompt, default={"is_social": False, "confidence": 0.0})

    # Guard confidence coercion: if confidence is non-numeric, default to 0.0
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "is_social": bool(result.get("is_social", False)),
        "confidence": confidence,
    }


def classify_work_ride(service: str, subject: str, snippet: str = "", examples=None) -> dict:
    """Work vs personal ride. Returns {"is_work": bool, "confidence": float}.

    `examples` — recent user corrections (see database.get_ride_examples) — are
    folded into the prompt so the model learns from past overrides."""
    example_block = ""
    if examples:
        lines = "\n".join(
            f'- "{e["subject"]}" IS {"" if e["user_is_work"] else "NOT "}work' for e in examples
        )
        example_block = f"\nThe user has corrected past classifications:\n{lines}\n"
    prompt = f"""You classify ride-hailing receipts as WORK travel or PERSONAL.

Work rides usually involve airports, hotels, conference venues, out-of-town
addresses, or weekday business hours while travelling. Personal rides are
local trips, nights out, and errands.
{example_block}
Service: {service}
Subject: {subject}
Preview: {snippet[:200]}

Reply with only JSON: {{"is_work": true|false, "confidence": 0.0-1.0}}"""
    result = _call_json(prompt, default={"is_work": False, "confidence": 0.0})
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"is_work": bool(result.get("is_work", False)), "confidence": confidence}


_BANK_SIDE_FLOWS = {
    "inflow": {"income", "transfer", "refund"},
    "outflow": {"spending", "transfer", "card_payment", "investment"},
}


def _bank_side(row: dict) -> str:
    return "inflow" if row.get("amount", 0) > 0 else "outflow"


def suggest_bank_flows(rows: list, examples: list = None) -> dict:
    """Suggest a flow for each unanswered bank row, one Claude call for the
    whole batch. Returns only VALIDATED suggestions — unknown ids, values
    outside the row's side-specific allowed set, and null (abstain) answers
    are all absent from the result, never written.

    Field selection is explicit and exhaustive here: only simplefin_id,
    payee, description, and a derived side ever reach the prompt. `rows` and
    `examples` are full DB-row dicts that also carry user_note, amount,
    account fields, etc. — those must never leak into the model call (spec
    §1), so this function must not serialize either dict wholesale."""
    if not rows:
        return {}
    examples = examples or []

    row_sides = {r["simplefin_id"]: _bank_side(r) for r in rows}

    row_lines = "\n".join(
        f'{r["simplefin_id"]} | payee: {r["payee"]} | description: {r["description"]} | side: {row_sides[r["simplefin_id"]]}'
        for r in rows
    )

    example_block = ""
    if examples:
        example_lines = "\n".join(
            f'- payee: {e["payee"]} | description: {e["description"]} | side: {e["side"]} -> {e["user_flow"]}'
            for e in examples
        )
        example_block = f"\nThe user's past answers:\n{example_lines}\n"

    prompt = f"""You classify bank transactions into a flow category.

Inflow rows (deposits) get one of: income, transfer, refund.
Outflow rows (charges) get one of: spending, transfer, card_payment, investment.

Use the row's own side to pick from the matching list above. A wrong
suggestion is worse than none — if you are not confident, answer null for
that row.
{example_block}
Classify each of these rows:
{row_lines}

Reply with only JSON mapping each id to its flow (or null to abstain), e.g.
{{"id1": "spending", "id2": null}}"""

    result = _call_json(prompt, max_tokens=1000, default={})
    if not isinstance(result, dict):
        return {}

    out = {}
    for row_id, flow in result.items():
        side = row_sides.get(row_id)
        if side is None or flow is None:
            continue
        if flow in _BANK_SIDE_FLOWS[side]:
            out[row_id] = flow
    return out


def weekly_reflection(card: dict, noticings: list) -> str:
    """2-3 sentence reflection on a completed week. Returns "" on any failure."""
    lines = []
    for m in card["metrics"].values():
        sign = "≤" if m["direction"] == "ceiling" else "≥"
        outcome = "hit" if m["hit"] else "missed"
        lines.append(f"- {m['label']}: {m['count']} (target {sign}{m['target']}) — {outcome}")
    summary = "\n".join(lines)
    patterns = "\n".join(f"- {n}" for n in noticings) if noticings else "- (none)"
    prompt = f"""You write a short weekly reflection for a personal habit tracker.

Week {card['week_start']} to {card['week_end']}:
{summary}

Patterns noticed:
{patterns}

Write 2-3 sentences: honest, warm, specific to the numbers. Address the reader as
"you". No emojis, no bullet points, no advice-column clichés.

Reply with only JSON: {{"reflection": "..."}}"""
    result = _call_json(prompt, max_tokens=300, default={"reflection": ""})
    return str(result.get("reflection") or "")
