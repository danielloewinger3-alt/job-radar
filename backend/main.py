import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import func, select

from backend.ai_apply import generate_application, revise_with_feedback
from backend.config import (
    ALL_CITIES,
    ANTHROPIC_API_KEY,
    BUSINESS_CATEGORIES,
    GITHUB_TOKEN,
    GITHUB_USERNAME,
    MAX_CV_BYTES,
    NEWS_CATEGORIES,
    OPENAI_API_KEY,
    PROSPECT_AREAS,
    SECTORS,
    TARGET_CITIES,
    UPLOAD_DIR,
)
from backend.cv_text import extract_text as extract_cv_text
from backend.db import get_session, init_db
from backend import news as news_module
from backend.models import CV, Application, Business, Job, Profile, Project, utcnow
from backend import poller
from backend.prospects import scan as prospect_scan
from backend import scheduler as scheduler_module

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    poller.clear_stop()
    init_db()
    poller.try_start_background_poll(name="startup-poll")
    scheduler_module.start()
    try:
        yield
    finally:
        poller.request_stop()
        scheduler_module.shutdown()
        if not poller.join_worker(timeout=10.0):
            logging.getLogger("lifespan").warning(
                "poll worker still running after shutdown wait"
            )


app = FastAPI(title="Job Search Tool", lifespan=lifespan)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


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


@app.post("/api/refresh", status_code=202)
def refresh():
    started = poller.try_start_background_poll(name="refresh-poll")
    # Poll now runs in the background. Shape kept for frontend compatibility:
    # new_jobs stays an object, total_new stays an int (both empty). The real
    # counts land in the DB and the client re-reads them via /api/cities.
    return {
        "status": "started" if started else "already_running",
        "new_jobs": {},
        "total_new": 0,
    }


class NotesUpdate(BaseModel):
    notes: str


@app.post("/api/jobs/{job_id:path}/notes")
def update_notes(job_id: str, body: NotesUpdate):
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        job.notes = body.notes
        session.add(job)
        session.commit()
        return {"ok": True}


# ---------- CV library ----------

@app.get("/api/cvs")
def list_cvs():
    with get_session() as session:
        return session.exec(select(CV).order_by(CV.uploaded_at.desc())).all()


@app.post("/api/cvs")
async def upload_cv(file: UploadFile = File(...), label: str = Form(...), role_type: str = Form("")):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="only PDF files are supported")
    data = await file.read()
    if len(data) > MAX_CV_BYTES:
        raise HTTPException(status_code=400, detail="file too large (max 15MB)")

    stored_name = f"{uuid.uuid4().hex}.pdf"
    (UPLOAD_DIR / stored_name).write_bytes(data)

    original_name = (file.filename or stored_name).replace("\r", "").replace("\n", "")
    with get_session() as session:
        cv = CV(label=label, role_type=role_type, filename=stored_name, original_name=original_name)
        session.add(cv)
        session.commit()
        session.refresh(cv)
        return cv


@app.get("/api/cvs/{cv_id}/file")
def get_cv_file(cv_id: int):
    with get_session() as session:
        cv = session.get(CV, cv_id)
        if cv is None:
            raise HTTPException(status_code=404, detail="cv not found")
        path = UPLOAD_DIR / cv.filename
        if not path.exists():
            raise HTTPException(status_code=404, detail="file missing on disk")
        return FileResponse(path, media_type="application/pdf", filename=cv.original_name)


@app.delete("/api/cvs/{cv_id}")
def delete_cv(cv_id: int):
    with get_session() as session:
        cv = session.get(CV, cv_id)
        if cv is None:
            raise HTTPException(status_code=404, detail="cv not found")
        path = UPLOAD_DIR / cv.filename
        if path.exists():
            path.unlink()
        session.delete(cv)
        session.commit()
        return {"ok": True}


# ---------- projects ----------

class ProjectIn(BaseModel):
    title: str
    description: str = ""
    tags: str = ""
    link: str = ""


@app.get("/api/projects")
def list_projects():
    with get_session() as session:
        return session.exec(select(Project).order_by(Project.created_at.desc())).all()


@app.post("/api/projects")
def create_project(body: ProjectIn):
    with get_session() as session:
        project = Project(**body.model_dump())
        session.add(project)
        session.commit()
        session.refresh(project)
        return project


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int):
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        session.delete(project)
        session.commit()
        return {"ok": True}


# ---------- github ----------

@app.get("/api/github/repos")
def github_repos():
    if not GITHUB_TOKEN and not GITHUB_USERNAME:
        return {"configured": False, "repos": []}

    try:
        if GITHUB_TOKEN:
            resp = httpx.get(
                "https://api.github.com/user/repos",
                params={"per_page": 100, "sort": "updated"},
                headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
                timeout=15,
            )
        else:
            resp = httpx.get(
                f"https://api.github.com/users/{GITHUB_USERNAME}/repos",
                params={"per_page": 100, "sort": "updated"},
                timeout=15,
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError:
        return {"configured": True, "repos": [], "error": "couldn't reach GitHub"}

    repos = [
        {
            "name": r["name"],
            "description": r.get("description") or "",
            "url": r["html_url"],
            "private": r.get("private", False),
            "language": r.get("language"),
            "stars": r.get("stargazers_count", 0),
            "updated_at": r.get("pushed_at") or r.get("updated_at"),
        }
        for r in data
        if isinstance(r, dict) and "name" in r
    ]
    return {"configured": True, "repos": repos}


# ---------- profile ----------

class ProfileIn(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    location: str = ""


@app.get("/api/profile")
def get_profile():
    with get_session() as session:
        profile = session.get(Profile, 1)
        if profile is None:
            profile = Profile(id=1)
            session.add(profile)
            session.commit()
            session.refresh(profile)
        return profile


@app.put("/api/profile")
def update_profile(body: ProfileIn):
    with get_session() as session:
        profile = session.get(Profile, 1) or Profile(id=1)
        for key, value in body.model_dump().items():
            setattr(profile, key, value)
        profile.updated_at = utcnow()
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile


# ---------- AI-assisted applications ----------

def _require_ai_keys():
    missing = [name for name, val in [("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY), ("OPENAI_API_KEY", OPENAI_API_KEY)] if not val]
    if missing:
        raise HTTPException(status_code=400, detail=f"{' and '.join(missing)} not set in .env")


class ApplyIn(BaseModel):
    cv_id: int


@app.get("/api/jobs/{job_id:path}/applications")
def list_applications(job_id: str):
    with get_session() as session:
        return session.exec(
            select(Application).where(Application.job_id == job_id).order_by(Application.created_at.desc())
        ).all()


@app.post("/api/jobs/{job_id:path}/applications")
def create_application(job_id: str, body: ApplyIn):
    _require_ai_keys()
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        cv = session.get(CV, body.cv_id)
        if cv is None:
            raise HTTPException(status_code=404, detail="cv not found")
        profile = session.get(Profile, 1) or Profile(id=1)

        cv_text = extract_cv_text(UPLOAD_DIR / cv.filename)
        if not cv_text:
            raise HTTPException(status_code=400, detail="couldn't extract text from that CV (scanned/image-only PDFs aren't supported)")

        try:
            cover_letter, review_notes = generate_application(job, cv_text, profile)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

        application = Application(job_id=job_id, cv_id=cv.id, cover_letter=cover_letter, review_notes=review_notes)
        session.add(application)
        session.commit()
        session.refresh(application)
        return application


class ReviseIn(BaseModel):
    feedback: str


@app.post("/api/applications/{application_id}/revise")
def revise_application(application_id: int, body: ReviseIn):
    _require_ai_keys()
    with get_session() as session:
        application = session.get(Application, application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="application not found")
        job = session.get(Job, application.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="the job for this application no longer exists")

        try:
            revised = revise_with_feedback(application.cover_letter, body.feedback, job)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"AI revision failed: {e}")

        application.cover_letter = revised
        application.updated_at = utcnow()
        session.add(application)
        session.commit()
        session.refresh(application)
        return application


# ---------- prospects (local business opportunity scanning) ----------

@app.get("/api/prospects/areas")
def get_prospect_areas():
    with get_session() as session:
        areas = []
        for key, area in PROSPECT_AREAS.items():
            total = session.exec(select(func.count()).select_from(Business).where(Business.area_key == key)).one()
            unanalyzed = session.exec(
                select(func.count()).select_from(Business).where(Business.area_key == key, Business.analyzed_at == None)  # noqa: E711
            ).one()
            areas.append({"key": key, **area, "total_businesses": total, "unanalyzed_businesses": unanalyzed})
    return {
        "areas": areas,
        "sectors": {k: v for k, v in SECTORS.items()},
        "categories": [{"key": k, **v} for k, v in BUSINESS_CATEGORIES.items()],
    }


@app.get("/api/prospects/{area_key}/businesses")
def list_businesses(area_key: str, category: str | None = None):
    if area_key not in PROSPECT_AREAS:
        raise HTTPException(status_code=404, detail="unknown area")
    with get_session() as session:
        query = select(Business).where(Business.area_key == area_key)
        if category:
            query = query.where(Business.category == category)
        return session.exec(query.order_by(Business.discovered_at.desc())).all()


class ScanIn(BaseModel):
    categories: list[str]


@app.post("/api/prospects/{area_key}/scan")
def scan_prospects(area_key: str, body: ScanIn):
    if area_key not in PROSPECT_AREAS:
        raise HTTPException(status_code=404, detail="unknown area")
    unknown = [c for c in body.categories if c not in BUSINESS_CATEGORIES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown categories: {', '.join(unknown)}")
    counts = prospect_scan.discover(area_key, body.categories)
    return {"new_businesses": counts, "total_new": sum(counts.values())}


class AnalyzeIn(BaseModel):
    limit: int = 10


@app.post("/api/prospects/{area_key}/analyze")
def analyze_prospects(area_key: str, body: AnalyzeIn):
    if area_key not in PROSPECT_AREAS:
        raise HTTPException(status_code=404, detail="unknown area")
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY not set in .env")
    analyzed = prospect_scan.analyze_pending(area_key, limit=min(body.limit, 25))
    return {"analyzed": analyzed}


# ---------- news ----------

@app.get("/api/news/categories")
def get_news_categories():
    return [{"key": k, "label": v["label"]} for k, v in NEWS_CATEGORIES.items()]


@app.get("/api/news")
def get_news(category: str | None = None):
    if category:
        if category not in NEWS_CATEGORIES:
            raise HTTPException(status_code=404, detail="unknown category")
        return {"articles": news_module.fetch_category(category)}
    return {"articles": news_module.fetch_all()}


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
