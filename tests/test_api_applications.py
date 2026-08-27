"""AI-assisted application endpoints.

No paid API is ever called: the key guard is tested with keys absent, and the
happy path stubs generate_application / revise_with_feedback / extract_cv_text.
"""

from datetime import datetime

from sqlmodel import select

from backend.models import CV, Application, Job


def _seed_job_and_cv(session):
    session.add(Job(
        id="greenhouse:acme:1", source="greenhouse", title="Software Engineer",
        company="Acme", location_text="London, UK", city_key="london", remote=False,
        url="https://example.com/1", first_seen_at=datetime(2026, 8, 1),
        description_full="Build things.",
    ))
    cv = CV(label="Backend", role_type="Backend", filename="cv.pdf", original_name="cv.pdf")
    session.add(cv)
    session.commit()
    session.refresh(cv)
    return cv


def test_create_application_without_keys_is_400(client, db_session):
    cv = _seed_job_and_cv(db_session)
    r = client.post("/api/jobs/greenhouse:acme:1/applications", json={"cv_id": cv.id})
    assert r.status_code == 400
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_list_applications_empty(client):
    r = client.get("/api/jobs/greenhouse:acme:1/applications")
    assert r.status_code == 200
    assert r.json() == []


def test_create_application_happy_path_stubbed(client, db_session, monkeypatch):
    import backend.main as backend_main

    cv = _seed_job_and_cv(db_session)
    monkeypatch.setattr(backend_main, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(backend_main, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(backend_main, "extract_cv_text", lambda path: "CV body text")
    monkeypatch.setattr(
        backend_main, "generate_application",
        lambda job, cv_text, profile: ("Dear hiring team, ...", "reviewer said: fine"),
    )

    r = client.post("/api/jobs/greenhouse:acme:1/applications", json={"cv_id": cv.id})
    assert r.status_code == 200
    body = r.json()
    assert body["cover_letter"].startswith("Dear hiring team")
    assert body["review_notes"] == "reviewer said: fine"

    saved = db_session.exec(select(Application)).one()
    assert saved.job_id == "greenhouse:acme:1"


def test_create_application_missing_job_is_404(client, db_session, monkeypatch):
    import backend.main as backend_main

    cv = _seed_job_and_cv(db_session)
    monkeypatch.setattr(backend_main, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(backend_main, "OPENAI_API_KEY", "sk-test")
    r = client.post("/api/jobs/ghost:1/applications", json={"cv_id": cv.id})
    assert r.status_code == 404
    assert r.json()["detail"] == "job not found"


def test_revise_application_happy_path_stubbed(client, db_session, monkeypatch):
    import backend.main as backend_main

    _seed_job_and_cv(db_session)
    db_session.add(Application(
        id=None, job_id="greenhouse:acme:1", cv_id=1,
        cover_letter="v1 text", review_notes="notes",
    ))
    db_session.commit()
    app_id = db_session.exec(select(Application)).one().id

    monkeypatch.setattr(backend_main, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(backend_main, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(
        backend_main, "revise_with_feedback",
        lambda cover_letter, feedback, job: "v2 revised text",
    )

    r = client.post(f"/api/applications/{app_id}/revise", json={"feedback": "make it shorter"})
    assert r.status_code == 200
    assert r.json()["cover_letter"] == "v2 revised text"


def test_revise_missing_application_is_404(client, monkeypatch):
    import backend.main as backend_main

    monkeypatch.setattr(backend_main, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(backend_main, "OPENAI_API_KEY", "sk-test")
    r = client.post("/api/applications/999/revise", json={"feedback": "x"})
    assert r.status_code == 404
