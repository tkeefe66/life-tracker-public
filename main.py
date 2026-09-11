"""On Track v2 entry point — FastAPI app + in-process APScheduler."""
import datetime
import logging
from contextlib import asynccontextmanager

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import database as db
from app.api import create_app
from config import (BACKUP_HOUR, CALENDAR_SCAN_HOUR, GMAIL_SCAN_INTERVAL_HOURS,
                    SIMPLEFIN_SYNC_INTERVAL_HOURS, TIMEZONE, WEEKLY_PUSH_HOUR)

logging.basicConfig(
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    level=logging.INFO,
)

# httpx logs the full request URL at INFO, and the SimpleFIN access URL *is*
# the credential (services/simplefin_service.py) — so at INFO every sync
# publishes the bearer token to the deploy logs. This bypasses the redaction
# boundary described in CLAUDE.md completely: nothing is raised and nothing is
# stored, so neither safe_status() nor app_settings ever sees it. Prevent the
# line from being emitted rather than scrubbing it afterwards. Leaked in
# production on 2026-07-23; locked by a test in tests/test_simplefin_service.py.
# Never lower these.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _cron(**fields) -> CronTrigger:
    """A CronTrigger explicitly pinned to TIMEZONE.

    Never construct a bare CronTrigger here. Passing timezone= to
    AsyncIOScheduler does NOT retag an already-built trigger — add_job()
    applies the scheduler's timezone only when it constructs the trigger
    itself. A standalone CronTrigger captures the *process's* local zone,
    which in the Railway container is UTC, so every cron job ran on UTC
    wall-clock: the 4 AM backup fired at 10 PM Denver, the 6 AM calendar scan
    at midnight, the Monday 9 AM push at 3 AM. Found in production
    2026-08-06; locked by tests/test_scheduler.py."""
    return CronTrigger(timezone=pytz.timezone(TIMEZONE), **fields)


def build_scheduler() -> AsyncIOScheduler:
    """Registers every scheduled job and returns the scheduler, unstarted.

    Split out of lifespan() so tests can inspect the registered triggers
    without starting a scheduler or initializing a database — see
    tests/test_scheduler.py, which locks the timezone of every cron trigger."""
    from jobs.backup_db import run as backup_db
    from jobs.scan_calendar import run as scan_calendar
    from jobs.scan_gmail import run as scan_gmail
    from jobs.sync_bank import run as sync_bank
    from jobs.weekly_push import run as weekly_push

    scheduler = AsyncIOScheduler(timezone=pytz.timezone(TIMEZONE))
    # next_run_time=now: run once at startup so a deploy/restart refreshes
    # gmail_last_status immediately instead of waiting a full interval.
    scheduler.add_job(
        scan_gmail,
        IntervalTrigger(hours=GMAIL_SCAN_INTERVAL_HOURS),
        id="scan_gmail",
        next_run_time=datetime.datetime.now(pytz.timezone(TIMEZONE)),
    )
    # next_run_time=now, same reasoning as scan_gmail: a deploy should refresh
    # bank_last_status immediately rather than waiting a full interval. Also
    # matters more here — SimpleFIN keeps only a rolling 90 days, so a sync that
    # silently stops running loses history permanently.
    scheduler.add_job(
        sync_bank,
        IntervalTrigger(hours=SIMPLEFIN_SYNC_INTERVAL_HOURS),
        id="sync_bank",
        next_run_time=datetime.datetime.now(pytz.timezone(TIMEZONE)),
    )
    # next_run_time=now, same reasoning as scan_gmail/sync_bank: a deploy
    # should refresh calendar data too, not wait for the 6 AM cron. Cheap to
    # run at startup — the job already no-ops when Google isn't configured,
    # and classification is skipped for already-classified events.
    scheduler.add_job(
        scan_calendar,
        _cron(hour=CALENDAR_SCAN_HOUR, minute=0),
        id="scan_calendar",
        next_run_time=datetime.datetime.now(pytz.timezone(TIMEZONE)),
    )
    scheduler.add_job(weekly_push, _cron(day_of_week="mon", hour=WEEKLY_PUSH_HOUR, minute=0), id="weekly_push")
    # Daily, not weekly: manual check-ins are hand-entered and reconstructable
    # from nothing, so a weekly cadence risked losing up to seven days of them.
    # A dump is ~390 KB — the cost of daily is noise.
    scheduler.add_job(backup_db, _cron(hour=BACKUP_HOUR, minute=0), id="backup_db")
    return scheduler


@asynccontextmanager
async def lifespan(app):
    db.initialize_db()
    db.seed_default_targets()
    db.delete_expired_sessions(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds"))

    scheduler = build_scheduler()
    scheduler.start()
    logger.info("On Track started — gmail every %dh, bank every %dh, calendar daily @%02d:00 %s, "
                "push Mon @%02d:00 %s, backup daily @%02d:00 %s",
                GMAIL_SCAN_INTERVAL_HOURS, SIMPLEFIN_SYNC_INTERVAL_HOURS,
                CALENDAR_SCAN_HOUR, TIMEZONE, WEEKLY_PUSH_HOUR, TIMEZONE,
                BACKUP_HOUR, TIMEZONE)
    yield
    scheduler.shutdown(wait=False)


app = create_app(lifespan=lifespan)
