import logging

from apscheduler.schedulers.background import BackgroundScheduler

from backend.config import POLL_INTERVAL_MINUTES
from backend.poller import poll_all_sources

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()


def _run_poll() -> None:
    counts = poll_all_sources()
    total = sum(counts.values())
    logger.info("poll complete: %d new jobs (%s)", total, counts)


def start() -> None:
    scheduler.add_job(_run_poll, "interval", minutes=POLL_INTERVAL_MINUTES)
    scheduler.start()
