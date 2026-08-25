import logging
import threading
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import func, select

from backend.config import ALL_CITIES, GITHUB_TOKEN, GITHUB_USERNAME, MAX_CV_BYTES, TARGET_CITIES, UPLOAD_DIR
from backend.db import get_session, init_db
from backend.models import CV, Job, Project
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


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
