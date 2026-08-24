import logging
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlmodel import func, select

from backend.config import ALL_CITIES, TARGET_CITIES
from backend.db import get_session, init_db
from backend.models import Job
from backend.poller import poll_all_sources
from backend import scheduler as scheduler_module

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Job Search Tool")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    threading.Thread(target=poll_all_sources, daemon=True).start()
    scheduler_module.start()


@app.get("/api/cities")
def get_cities():
    result = []
    with get_session() as session:
        for key, city in ALL_CITIES.items():
            total = session.exec(
                select(func.count()).select_from(Job).where(Job.city_key == key)
            ).one()
            unseen = session.exec(
                select(func.count()).select_from(Job).where(Job.city_key == key, Job.seen == False)  # noqa: E712
            ).one()
            result.append(
                {
                    "key": key,
                    "label": city["label"],
                    "country": city["country"],
                    "lat": city["lat"],
                    "lon": city["lon"],
                    "tier": "primary" if key in TARGET_CITIES else "eu",
                    "total_jobs": total,
                    "unseen_jobs": unseen,
                }
            )
    return result


@app.get("/api/jobs")
def get_jobs(city: str | None = None, only_unseen: bool = False, remote: bool = False):
    with get_session() as session:
        query = select(Job)
        if remote:
            query = query.where(Job.remote == True)  # noqa: E712
        elif city:
            if city not in ALL_CITIES:
                raise HTTPException(status_code=404, detail="unknown city")
            query = query.where(Job.city_key == city)
        if only_unseen:
            query = query.where(Job.seen == False)  # noqa: E712
        query = query.order_by(Job.first_seen_at.desc())
        jobs = session.exec(query).all()
        return jobs


@app.post("/api/jobs/{job_id:path}/seen")
def mark_seen(job_id: str):
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        job.seen = True
        session.add(job)
        session.commit()
        return {"ok": True}


@app.post("/api/refresh")
def refresh():
    counts = poll_all_sources()
    return {"new_jobs": counts, "total_new": sum(counts.values())}


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
