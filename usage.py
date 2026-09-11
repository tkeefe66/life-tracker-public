"""Report Anthropic usage to coach-web. Copy this file into each app.

Contract: never raises, never blocks. A reporting failure must not affect the
calling app, so every exception is swallowed and the POST runs on a daemon
thread. Standard library only — apps need no extra dependency.
"""
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FIELDS = ("input_tokens", "output_tokens",
          "cache_read_input_tokens", "cache_creation_input_tokens")


def build_payload(app: str, model: str, usage, ts: str | None = None) -> dict:
    def get(field):
        if isinstance(usage, dict):
            value = usage.get(field)
        else:
            value = getattr(usage, field, None)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "app": app,
        "model": str(model),
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        **{field: get(field) for field in FIELDS},
    }


def _post(url: str, token: str, body: bytes) -> None:
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    urllib.request.urlopen(request, timeout=3).close()


def _log_failure(exc: Exception) -> None:
    """Best-effort logging of a swallowed report failure. Must never raise --
    a broken logging config must not turn into a caller-visible error."""
    try:
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                detail = ""
            logger.warning("coach-web usage report rejected: status=%s body=%s",
                           exc.code, detail)
        else:
            logger.warning("coach-web usage report failed: %s", exc)
    except Exception:
        pass


def report(app: str, model: str, usage, url: str | None = None,
           token: str | None = None, blocking: bool = False) -> None:
    """Fire-and-forget a usage row. Safe to call from anywhere."""
    try:
        url = url if url is not None else os.environ.get("COACH_USAGE_URL")
        token = token if token is not None else os.environ.get("COACH_USAGE_TOKEN")
        if not url or not token:
            return
        body = json.dumps(build_payload(app, model, usage)).encode("utf-8")

        def send():
            try:
                _post(url, token, body)
            except Exception as exc:
                # a lost data point must never surface in the calling app --
                # but it must not vanish silently either.
                _log_failure(exc)

        if blocking:
            send()
        else:
            threading.Thread(target=send, daemon=True).start()
    except Exception:
        pass
