"""Essential API happy paths + 404 / error paths: cities, jobs, seen, notes, refresh."""

from datetime import datetime

from sqlmodel import select

from backend.models import Job


def _make_job(session, **over):
    data = dict(
        id="greenhouse:acme:1",
        source="greenhouse",
        title="Software Engineer",
        company="Acme",
        location_text="London, UK",
        city_key="london",
        remote=False,
        url="https://example.com/jobs/1",
        first_seen_at=datetime(2026, 8, 1, 12, 0, 0),
        seen=False,
        description_snippet="snip",
        description_full="full",
    )
    data.update(over)
    job = Job(**data)
    session.add(job)
    session.commit()
    return job


# --------------------------------------------------------------------------- #
# /api/cities
# --------------------------------------------------------------------------- #
def test_cities_empty_db_reports_zero_counts(client):
    r = client.get("/api/cities")
    assert r.status_code == 200
    body = r.json()
    assert any(c["key"] == "london" for c in body)
    assert all(c["total_jobs"] == 0 and c["unseen_jobs"] == 0 for c in body)
    london = next(c for c in body if c["key"] == "london")
    assert london["tier"] == "primary"
    assert {"label", "country", "lat", "lon"} <= london.keys()


def test_cities_counts_reflect_jobs(client, db_session):
    _make_job(db_session, id="greenhouse:acme:1", seen=False)
    _make_job(db_session, id="greenhouse:acme:2", seen=True)
    r = client.get("/api/cities")
    london = next(c for c in r.json() if c["key"] == "london")
    assert london["total_jobs"] == 2
    assert london["unseen_jobs"] == 1


# --------------------------------------------------------------------------- #
# /api/jobs
# --------------------------------------------------------------------------- #
def test_jobs_empty(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert r.json() == []


def test_jobs_returns_seeded_job(client, db_session):
    _make_job(db_session)
    r = client.get("/api/jobs")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == "greenhouse:acme:1"


def test_jobs_filter_by_city(client, db_session):
    _make_job(db_session, id="greenhouse:acme:1", city_key="london")
    _make_job(db_session, id="greenhouse:acme:2", city_key="new_york")
    r = client.get("/api/jobs", params={"city": "new_york"})
    assert [j["id"] for j in r.json()] == ["greenhouse:acme:2"]


def test_jobs_unknown_city_is_404(client):
    r = client.get("/api/jobs", params={"city": "atlantis"})
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown city"


def test_jobs_filter_remote(client, db_session):
    _make_job(db_session, id="greenhouse:acme:1", remote=False)
    _make_job(db_session, id="greenhouse:acme:2", remote=True, city_key=None)
    r = client.get("/api/jobs", params={"remote": True})
    assert [j["id"] for j in r.json()] == ["greenhouse:acme:2"]


def test_jobs_only_unseen(client, db_session):
    _make_job(db_session, id="greenhouse:acme:1", seen=True)
    _make_job(db_session, id="greenhouse:acme:2", seen=False)
    r = client.get("/api/jobs", params={"only_unseen": True})
    assert [j["id"] for j in r.json()] == ["greenhouse:acme:2"]


# --------------------------------------------------------------------------- #
# /api/jobs/{id}/seen
# --------------------------------------------------------------------------- #
def test_mark_seen_happy(client, db_session):
    _make_job(db_session)
    r = client.post("/api/jobs/greenhouse:acme:1/seen")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    db_session.expire_all()
    job = db_session.exec(select(Job)).one()
    assert job.seen is True


def test_mark_seen_missing_job_is_404(client):
    r = client.post("/api/jobs/nope:nope/seen")
    assert r.status_code == 404
    assert r.json()["detail"] == "job not found"


# --------------------------------------------------------------------------- #
# /api/jobs/{id}/notes
# --------------------------------------------------------------------------- #
def test_update_notes_happy(client, db_session):
    _make_job(db_session)
    r = client.post("/api/jobs/greenhouse:acme:1/notes", json={"notes": "phone screen booked"})
    assert r.status_code == 200

    db_session.expire_all()
    job = db_session.exec(select(Job)).one()
    assert job.notes == "phone screen booked"


def test_update_notes_missing_job_is_404(client):
    r = client.post("/api/jobs/nope:nope/notes", json={"notes": "x"})
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# /api/refresh - contract kept implementation-neutral (see point 6 of the plan)
# --------------------------------------------------------------------------- #
def test_refresh_returns_stable_shape(client, monkeypatch):
    import backend.main as backend_main

    monkeypatch.setattr(backend_main, "poll_all_sources", lambda: {"greenhouse": 0})
    r = client.post("/api/refresh")

    # master returns 200 (blocking); the backend-reliability branch will return 202
    # (fire-and-forget). Both are acceptable here.
    assert r.status_code in (200, 202)
    body = r.json()
    assert isinstance(body.get("total_new"), int)
    assert isinstance(body.get("new_jobs"), dict)
