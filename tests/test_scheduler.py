"""Locks the timezone every scheduled job actually fires in.

Why this file exists: on 2026-08-06 the daily backup was firing at 04:00 UTC —
10:00 PM the previous day in America/Denver — despite BACKUP_HOUR=4 and
TIMEZONE=America/Denver. AsyncIOScheduler(timezone=...) does NOT retag a
trigger that was already constructed: add_job() only applies the scheduler's
timezone when it builds the trigger itself (from a string alias). A standalone
CronTrigger captures the *process's* local zone at construction, which in the
Railway container is UTC. Every cron job silently ran on UTC wall-clock:
backup 4 AM -> 10 PM, calendar scan 6 AM -> midnight, weekly push Mon 9 AM ->
Mon 3 AM. Confirmed against apscheduler 3.11.3.

The tests force TZ=UTC so the process's local zone differs from TIMEZONE —
without that, running on a Denver laptop would make the assertion pass whether
or not the trigger is pinned."""
import importlib
import time

import pytest
from apscheduler.triggers.cron import CronTrigger

from config import TIMEZONE


@pytest.fixture
def utc_process_tz(monkeypatch):
    """Force the process's local zone to UTC for the duration of a test.

    tzlocal caches its answer, so the cache is cleared on both sides — a
    stale cache from an earlier test would silently make these tests vacuous,
    which is the exact failure mode they exist to prevent."""
    import tzlocal

    def _reset():
        tzlocal.reload_localzone()

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    _reset()
    yield
    monkeypatch.undo()
    time.tzset()
    _reset()


def test_process_tz_fixture_actually_differs_from_configured_tz(utc_process_tz):
    """Guards the guard. If TIMEZONE were ever set to UTC, every assertion
    below would pass trivially and this file would stop protecting anything."""
    assert TIMEZONE != "UTC", (
        "TIMEZONE is UTC, so these tests cannot distinguish a pinned trigger "
        "from one that merely inherited the process zone"
    )
    assert str(CronTrigger(hour=4).timezone) == "UTC", (
        "the utc_process_tz fixture did not take effect — a standalone "
        "CronTrigger should capture UTC here"
    )


def test_every_cron_job_fires_in_the_configured_timezone(utc_process_tz):
    import main
    importlib.reload(main)

    scheduler = main.build_scheduler()
    cron_jobs = {j.id: j for j in scheduler.get_jobs() if isinstance(j.trigger, CronTrigger)}

    assert cron_jobs, "no cron jobs registered — this test would be vacuous"
    for job_id, job in cron_jobs.items():
        assert str(job.trigger.timezone) == TIMEZONE, (
            f"job {job_id!r} fires on {job.trigger.timezone} wall-clock, not "
            f"{TIMEZONE}. A CronTrigger constructed before add_job() keeps the "
            f"process's local zone; pass timezone= explicitly."
        )


def test_backup_fires_at_backup_hour_local(utc_process_tz):
    """The concrete symptom, pinned: BACKUP_HOUR is a LOCAL hour."""
    import datetime

    import pytz

    import main
    importlib.reload(main)

    job = {j.id: j for j in main.build_scheduler().get_jobs()}["backup_db"]
    after = pytz.utc.localize(datetime.datetime(2026, 8, 6, 12, 0, 0))
    next_fire = job.trigger.get_next_fire_time(None, after)

    local_hour = next_fire.astimezone(pytz.timezone(TIMEZONE)).hour
    assert local_hour == main.BACKUP_HOUR, (
        f"backup fires at {local_hour}:00 {TIMEZONE}, expected "
        f"{main.BACKUP_HOUR}:00 — the trigger is on the wrong wall-clock"
    )
