import logging

from sqlmodel import select

from backend.db import get_session
from backend.matching import is_remote, match_city, passes_filters
from backend.models import Job
from backend.sources import SOURCES

logger = logging.getLogger("poller")


def poll_all_sources() -> dict[str, int]:
    """Fetch every source, filter to relevant roles/locations, and upsert into the DB.

    Returns a per-source count of newly-inserted (unseen) jobs.
    """
    new_counts: dict[str, int] = {}

    with get_session() as session:
        for name, fetch_fn in SOURCES:
            count = 0
            try:
                raw_jobs = fetch_fn()
            except Exception:
                logger.exception("source %s failed", name)
                new_counts[name] = 0
                continue

            for raw in raw_jobs:
                if not passes_filters(raw.title, raw.location_text, raw.remote):
                    continue

                job_id = f"{raw.source}:{raw.external_id}"
                existing = session.get(Job, job_id)
                if existing is not None:
                    continue  # already known; leave seen/first_seen_at untouched

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
                )
                session.add(job)
                count += 1

            session.commit()
            new_counts[name] = count

    return new_counts
