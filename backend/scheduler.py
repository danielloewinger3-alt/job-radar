import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from backend.config import POLL_INTERVAL_MINUTES
from backend import poller

logger = logging.getLogger("scheduler")

JOB_ID = "poll_all_sources"

_lock = threading.Lock()
_scheduler: "BackgroundScheduler | None" = None


def _run_poll() -> None:
    result = poller.run_poll_guarded()
    if result is None:
        logger.info("scheduled poll skipped: another poll already running")
        return
    total = sum(result.values())
    logger.info("scheduled poll complete: %d new jobs (%s)", total, result)


def start() -> None:
    """Start the interval scheduler. Idempotent and restart-safe.

    A fresh ``BackgroundScheduler`` is built on each call, so two lifespan
    start/stop cycles in one process never reuse stale executors, jobstores or
    threads. ``coalesce`` + ``max_instances=1`` are an APScheduler-level
    backstop on top of the shared poll gate.
    """
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            _run_poll,
            "interval",
            minutes=POLL_INTERVAL_MINUTES,
            id=JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler = scheduler


def shutdown() -> None:
    """Stop the scheduler if running and drop the instance so the next
    :func:`start` is pristine. Bounded: ``shutdown(wait=False)`` does not block
    on a running job."""
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=False)
        _scheduler = None
