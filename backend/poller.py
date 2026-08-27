import logging
import threading

from backend.db import get_session
from backend.matching import is_remote, match_city, passes_filters
from backend.models import Job
from backend.sources import SOURCES

logger = logging.getLogger("poller")

# --- single concurrency gate -------------------------------------------------
# _poll_lock is the ONE gate every poll trigger goes through: the lifespan
# startup worker, the APScheduler tick, and POST /api/refresh. It is a plain
# (non-reentrant) Lock and is never acquired twice on one path.
#
# threading.Lock has no owning thread, so one thread may acquire it and hand
# the release to another. /api/refresh relies on that: the request thread
# reserves the gate, then the worker thread it spawns releases it.
_poll_lock = threading.Lock()

# Cooperative cancellation for a run in progress (checked between sources).
_stop_event = threading.Event()

# Most recent tracked poll worker. At most one poll runs at a time (the gate
# guarantees it), so a single reference is enough. A finished thread left here
# is harmless: starting a poll is gated on _poll_lock, never on this handle.
_worker_lock = threading.Lock()
_worker: "threading.Thread | None" = None


def clear_stop() -> None:
    """Clear the cancellation flag. Call at lifespan startup so a fresh cycle is
    not aborted by a stop signal left over from the previous cycle."""
    _stop_event.clear()


def request_stop() -> None:
    """Signal an in-progress poll to stop at the next source boundary."""
    _stop_event.set()


def _track_worker(thread: threading.Thread) -> None:
    global _worker
    with _worker_lock:
        _worker = thread


def join_worker(timeout: float) -> bool:
    """Wait up to ``timeout`` seconds for the tracked poll worker to finish.

    Returns True if there is no worker or it has finished, False if it is still
    running. The worker-state lock is NOT held while joining.
    """
    with _worker_lock:
        thread = _worker
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def _run_locked() -> "dict[str, int] | None":
    """Run a full poll assuming the caller already holds ``_poll_lock``.

    Always releases ``_poll_lock`` in the finally block, on success or
    exception. Never acquires the lock itself.
    """
    try:
        return poll_all_sources()
    finally:
        _poll_lock.release()


def run_poll_guarded() -> "dict[str, int] | None":
    """Acquire the gate (non-blocking) and run a poll on the CURRENT thread.

    Used by the APScheduler tick and the lifespan startup worker. Returns the
    per-source counts, or None if a poll is already running.
    """
    if not _poll_lock.acquire(blocking=False):
        logger.info("poll already running; skipping this trigger")
        return None
    return _run_locked()


def try_start_background_poll(*, name: str = "poll-worker") -> bool:
    """Atomically reserve the gate and start a tracked daemon worker to poll.

    Returns True if a worker was started, False if a poll is already reserved
    or running. The reservation (an acquired ``_poll_lock``) is transferred to
    the worker, which releases it in :func:`_run_locked`'s finally block. If the
    thread cannot be created or started, the reservation is released here.
    """
    if not _poll_lock.acquire(blocking=False):
        return False
    try:
        worker = threading.Thread(target=_run_locked, name=name, daemon=True)
        worker.start()
    except BaseException:
        _poll_lock.release()
        raise
    _track_worker(worker)
    return True


def poll_all_sources() -> "dict[str, int]":
    """Fetch every source, filter to relevant roles/locations, and upsert into the DB.

    Returns a per-source count of newly-inserted (unseen) jobs.

    Each source gets its own short-lived session so the write transaction (and
    the SQLite write lock) is held only while writing that source's rows, never
    across the next source's network fetch. Job ids are ``f"{source}:{id}"`` so
    de-duplication never needs to span sources.
    """
    new_counts: dict[str, int] = {}

    for name, fetch_fn in SOURCES:
        if _stop_event.is_set():
            logger.info("poll aborted before source %s: stop requested", name)
            break

        count = 0
        try:
            raw_jobs = fetch_fn()
        except Exception:
            logger.exception("source %s failed", name)
            new_counts[name] = 0
            continue

        with get_session() as session:
            for raw in raw_jobs:
                if not passes_filters(raw.title, raw.location_text, raw.remote):
                    continue

                job_id = f"{raw.source}:{raw.external_id}"
                existing = session.get(Job, job_id)
                if existing is not None:
                    # Already known; leave seen/first_seen_at untouched, but backfill
                    # fields that didn't exist when this row was first inserted.
                    if not existing.description_full and raw.description_full:
                        existing.description_full = raw.description_full
                        existing.description_snippet = raw.description_snippet
                        session.add(existing)
                    continue

                job = Job(
                    id=job_id,
                    source=raw.source,
                    title=raw.title,
                    company=raw.company,
                    location_text=raw.location_text,
                    city_key=match_city(raw.location_text),
                    remote=is_remote(raw.location_text, raw.remote),
                    url=raw.url,
                    posted_at=raw.posted_at,
                    seen=False,
                    description_snippet=raw.description_snippet,
                    description_full=raw.description_full,
                )
                session.add(job)
                count += 1

            session.commit()

        new_counts[name] = count

    return new_counts
