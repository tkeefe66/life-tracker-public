"""Send-only Telegram notifications. The bot has no inbound handlers in v2."""
import logging
import threading

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def notify(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured; dropping notification")
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        r.raise_for_status()
        logger.info("Telegram notification sent")
        return True
    except Exception:
        logger.exception("Telegram notify failed")
        return False


def notify_background(text: str) -> None:
    """Fire-and-forget notify() on a daemon thread.

    Callers on latency-sensitive or lock-holding paths must not block on a
    15-second HTTP timeout — the login lockout path in particular runs while
    holding the process-wide login lock, so a blocking send there would let
    anyone stall every login by triggering a lockout. notify() already
    swallows every exception, so nothing can escape the thread."""
    threading.Thread(target=notify, args=(text,), daemon=True).start()
