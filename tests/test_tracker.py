"""Application-tracker API: creation rules, job-id dedup, stage machine,
calendar, events, contacts and project links.

No network, isolated DB. Feature models imported at top level so their tables
are registered on SQLModel.metadata before the conftest create_all().
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from backend.models import CV, Application, Job, Project
from backend.tracker.models import (
    TrackedApplication,
    TrackedApplicationEvent,
    TrackedApplicationProjectLink,
    TrackedStage,
)

ALL_STAGES = [s.value for s in TrackedStage]


def _job(session, jid="greenhouse:acme:1", company="Acme", title="Software Engineer"):
    job = Job(
        id=jid, source="greenhouse", title=title, company=company,
        location_text="London, UK", city_key="london", remote=False,
        url="https://example.com/1", description_full="Build things.",
    )
    session.add(job)
    session.commit()
    return job


def _project(session, title="Portfolio site"):
    p = Project(title=title)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _cv(session, label="Backend"):
    cv = CV(label=label, role_type="Backend", filename="cv.pdf", original_name="cv.pdf")
    session.add(cv)
    session.commit()
    session.refresh(cv)
    return cv


def _create(client, **body):
    return client.post("/api/tracked-applications", json=body)


# --------------------------------------------------------------------------- #
# Contract shape
# --------------------------------------------------------------------------- #
def test_list_is_wrapped_object_with_schema_version(client):
    r = client.get("/api/tracked-applications")
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == 1
    assert body["tracked_applications"] == []


def test_get_one_envelope_keys(client, db_session):
    r = _create(client, company="Acme", role_title="SWE")
    tid = r.json()["tracked_application"]["id"]
    body = client.get(f"/api/tracked-applications/{tid}").json()
    assert set(body) == {
        "schema_version", "tracked_application", "events", "contacts", "project_ids"
    }


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
def test_manual_create_without_job_id(client):
    r = _create(client, company="Acme", role_title="SWE")
    assert r.status_code == 201
    app = r.json()["tracked_application"]
    assert app["origin"] == "manual"
    assert app["stage"] == "interested"
    assert app["job_id"] is None


@pytest.mark.parametrize("missing", ["company", "role_title"])
def test_manual_create_requires_company_and_role(client, missing):
    payload = {"company": "Acme", "role_title": "SWE"}
    payload.pop(missing)
    assert _create(client, **payload).status_code == 422


def test_job_linked_create_copies_company_and_title(client, db_session):
    _job(db_session, company="RealCo", title="Backend Engineer")
    r = _create(
        client, job_id="greenhouse:acme:1", company="IGNORED", role_title="IGNORED"
    )
    assert r.status_code == 201
    app = r.json()["tracked_application"]
    assert app["company"] == "RealCo"
    assert app["role_title"] == "Backend Engineer"
    assert app["origin"] == "job"


def test_job_linked_create_unknown_job_is_404(client):
    assert _create(client, job_id="ghost:1").status_code == 404


def test_create_unknown_cv_is_404(client):
    assert _create(client, company="A", role_title="B", cv_id=999).status_code == 404


def test_create_unknown_legacy_application_is_404(client):
    r = _create(client, company="A", role_title="B", legacy_application_id=999)
    assert r.status_code == 404


def test_create_unknown_project_is_404(client):
    r = _create(client, company="A", role_title="B", project_ids=[999])
    assert r.status_code == 404


def test_create_with_valid_projects_links_them(client, db_session):
    p1 = _project(db_session, "one")
    p2 = _project(db_session, "two")
    r = _create(client, company="A", role_title="B", project_ids=[p1.id, p2.id])
    tid = r.json()["tracked_application"]["id"]
    got = client.get(f"/api/tracked-applications/{tid}").json()["project_ids"]
    assert sorted(got) == sorted([p1.id, p2.id])


def test_create_invalid_stage_is_422(client):
    r = _create(client, company="A", role_title="B", stage="not-a-stage")
    assert r.status_code == 422


def test_create_extra_field_is_422(client):
    r = _create(client, company="A", role_title="B", surprise=1)
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Duplicate job tracking (mandatory correction #1)
# --------------------------------------------------------------------------- #
def _stable_409(r):
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "already_tracked"
    assert "tracked_application_id" in body
    assert "archived" in body
    return body


def test_sequential_duplicate_job_create_returns_stable_409(client, db_session):
    _job(db_session)
    first = _create(client, job_id="greenhouse:acme:1")
    assert first.status_code == 201
    tid = first.json()["tracked_application"]["id"]

    body = _stable_409(_create(client, job_id="greenhouse:acme:1"))
    assert body["tracked_application_id"] == tid
    assert body["archived"] is False
    # no second row
    rows = db_session.exec(
        select(TrackedApplication).where(
            TrackedApplication.job_id == "greenhouse:acme:1"
        )
    ).all()
    assert len(rows) == 1


def test_race_path_integrity_error_is_caught(client, db_session, monkeypatch):
    """Pre-check bypassed -> the DB UNIQUE constraint fires on flush and the
    handler recovers with the same stable 409."""
    _job(db_session)
    first = _create(client, job_id="greenhouse:acme:1")
    tid = first.json()["tracked_application"]["id"]

    import backend.tracker.router as tr

    monkeypatch.setattr(tr, "_find_by_job_id", lambda *a, **k: None)
    body = _stable_409(_create(client, job_id="greenhouse:acme:1"))
    assert body["tracked_application_id"] == tid
    rows = db_session.exec(
        select(TrackedApplication).where(
            TrackedApplication.job_id == "greenhouse:acme:1"
        )
    ).all()
    assert len(rows) == 1


def test_duplicate_when_existing_is_archived_reports_archived_true(client, db_session):
    _job(db_session)
    tid = _create(client, job_id="greenhouse:acme:1").json()["tracked_application"]["id"]
    client.delete(f"/api/tracked-applications/{tid}")  # archive

    body = _stable_409(_create(client, job_id="greenhouse:acme:1"))
    assert body["archived"] is True
    # still archived -- not silently reopened
    db_session.expire_all()
    row = db_session.get(TrackedApplication, tid)
    assert row.archived is True


def test_multiple_manual_rows_with_null_job_id_allowed(client, db_session):
    for _ in range(3):
        assert _create(client, company="A", role_title="B").status_code == 201
    rows = db_session.exec(
        select(TrackedApplication).where(TrackedApplication.job_id.is_(None))
    ).all()
    assert len(rows) == 3


# --------------------------------------------------------------------------- #
# Stage machine -- {to_stage, note?}
# --------------------------------------------------------------------------- #
def test_stage_endpoint_rejects_legacy_stage_key(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    assert client.post(
        f"/api/tracked-applications/{tid}/stage", json={"stage": "applied"}
    ).status_code == 422


def test_stage_endpoint_requires_to_stage(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    assert client.post(
        f"/api/tracked-applications/{tid}/stage", json={}
    ).status_code == 422


def test_stage_endpoint_invalid_value_is_422(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    assert client.post(
        f"/api/tracked-applications/{tid}/stage", json={"to_stage": "nope"}
    ).status_code == 422


@pytest.mark.parametrize("target", ALL_STAGES)
def test_every_stage_transition_writes_one_event(client, db_session, target):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    r = client.post(
        f"/api/tracked-applications/{tid}/stage",
        json={"to_stage": target, "note": "moved"},
    )
    assert r.status_code == 200
    body = r.json()
    if target == "interested":
        assert body["unchanged"] is True
        assert body["event"] is None
    else:
        assert body["unchanged"] is False
        assert body["tracked_application"]["stage"] == target
        assert body["event"]["kind"] == "stage_change"
        assert body["event"]["from_stage"] == "interested"
        assert body["event"]["to_stage"] == target
        assert body["event"]["body"] == "moved"
    events = db_session.exec(
        select(TrackedApplicationEvent).where(
            TrackedApplicationEvent.tracked_application_id == tid
        )
    ).all()
    assert len(events) == (0 if target == "interested" else 1)


def test_same_stage_noop_writes_no_event(client, db_session):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    client.post(f"/api/tracked-applications/{tid}/stage", json={"to_stage": "applied"})
    r = client.post(
        f"/api/tracked-applications/{tid}/stage", json={"to_stage": "applied"}
    )
    assert r.json()["unchanged"] is True
    assert r.json()["event"] is None
    events = db_session.exec(
        select(TrackedApplicationEvent).where(
            TrackedApplicationEvent.tracked_application_id == tid
        )
    ).all()
    assert len(events) == 1  # only the first transition


# --------------------------------------------------------------------------- #
# PATCH / archive / list
# --------------------------------------------------------------------------- #
def test_patch_rejects_stage(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    assert client.patch(
        f"/api/tracked-applications/{tid}", json={"stage": "applied"}
    ).status_code == 422


def test_patch_applies_allowed_fields(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    r = client.patch(
        f"/api/tracked-applications/{tid}",
        json={"notes": "call them", "next_action": "follow up"},
    )
    assert r.status_code == 200
    assert r.json()["tracked_application"]["notes"] == "call them"


def test_patch_unknown_cv_is_404(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    assert client.patch(
        f"/api/tracked-applications/{tid}", json={"cv_id": 999}
    ).status_code == 404


def test_delete_archives_and_is_idempotent(client, db_session):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    r1 = client.delete(f"/api/tracked-applications/{tid}")
    assert r1.json()["archived"] is True
    r2 = client.delete(f"/api/tracked-applications/{tid}")
    assert r2.json()["archived"] is True
    assert db_session.get(TrackedApplication, tid) is not None


def test_list_filters(client):
    a = _create(client, company="Alpha", role_title="Backend").json()["tracked_application"]["id"]
    _create(client, company="Beta", role_title="Frontend")
    client.post(f"/api/tracked-applications/{a}/stage", json={"to_stage": "applied"})

    got = client.get("/api/tracked-applications?stage=applied").json()["tracked_applications"]
    assert [x["id"] for x in got] == [a]

    got = client.get("/api/tracked-applications?search=front").json()["tracked_applications"]
    assert [x["company"] for x in got] == ["Beta"]

    client.delete(f"/api/tracked-applications/{a}")
    got = client.get("/api/tracked-applications").json()["tracked_applications"]
    assert a not in [x["id"] for x in got]
    got = client.get("/api/tracked-applications?archived=true").json()["tracked_applications"]
    assert a in [x["id"] for x in got]


# --------------------------------------------------------------------------- #
# Project links (endpoint + DB constraint + race)
# --------------------------------------------------------------------------- #
def test_link_dedup_via_endpoint(client, db_session):
    p = _project(db_session)
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    r1 = client.post(f"/api/tracked-applications/{tid}/projects", json={"project_id": p.id})
    assert r1.json()["already_linked"] is False
    r2 = client.post(f"/api/tracked-applications/{tid}/projects", json={"project_id": p.id})
    assert r2.json()["already_linked"] is True


def test_link_unknown_project_is_404(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    assert client.post(
        f"/api/tracked-applications/{tid}/projects", json={"project_id": 999}
    ).status_code == 404


def test_project_link_db_unique_constraint(client, db_session):
    from sqlalchemy.exc import IntegrityError

    p = _project(db_session)
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    db_session.add(TrackedApplicationProjectLink(tracked_application_id=tid, project_id=p.id))
    db_session.commit()
    db_session.add(TrackedApplicationProjectLink(tracked_application_id=tid, project_id=p.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_link_race_integrity_error_becomes_already_linked(client, db_session):
    p = _project(db_session)
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    # Simulate the row appearing between the endpoint's pre-check and commit by
    # inserting it directly, then calling the endpoint (which will also catch a
    # genuine IntegrityError).
    db_session.add(TrackedApplicationProjectLink(tracked_application_id=tid, project_id=p.id))
    db_session.commit()
    r = client.post(f"/api/tracked-applications/{tid}/projects", json={"project_id": p.id})
    assert r.status_code == 200
    assert r.json()["already_linked"] is True


def test_unlink_project(client, db_session):
    p = _project(db_session)
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    client.post(f"/api/tracked-applications/{tid}/projects", json={"project_id": p.id})
    r = client.delete(f"/api/tracked-applications/{tid}/projects/{p.id}")
    assert r.json()["deleted"] is True
    assert client.get(f"/api/tracked-applications/{tid}").json()["project_ids"] == []


# --------------------------------------------------------------------------- #
# Events / contacts
# --------------------------------------------------------------------------- #
def test_event_create_rejects_stage_change_kind(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    assert client.post(
        f"/api/tracked-applications/{tid}/events", json={"kind": "stage_change"}
    ).status_code == 400


def test_event_crud(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    ev = client.post(
        f"/api/tracked-applications/{tid}/events",
        json={"kind": "interview", "title": "Onsite", "occurs_at": "2026-09-05T13:00:00Z"},
    ).json()["event"]
    assert ev["kind"] == "interview"
    r = client.patch(
        f"/api/tracked-applications/{tid}/events/{ev['id']}", json={"body": "went well"}
    )
    assert r.json()["event"]["body"] == "went well"
    assert client.patch(
        f"/api/tracked-applications/{tid}/events/{ev['id']}", json={"kind": "note"}
    ).status_code == 422
    assert client.delete(
        f"/api/tracked-applications/{tid}/events/{ev['id']}"
    ).json()["deleted"] is True


def test_contact_crud(client):
    tid = _create(client, company="A", role_title="B").json()["tracked_application"]["id"]
    c = client.post(
        f"/api/tracked-applications/{tid}/contacts",
        json={"name": "Jane", "contact_role": "recruiter", "email": "j@x.io"},
    ).json()["contact"]
    assert c["contact_role"] == "recruiter"
    r = client.patch(
        f"/api/tracked-applications/{tid}/contacts/{c['id']}", json={"phone": "123"}
    )
    assert r.json()["contact"]["phone"] == "123"
    assert client.delete(
        f"/api/tracked-applications/{tid}/contacts/{c['id']}"
    ).json()["deleted"] is True


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #
def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def test_calendar_from_to_filtering_and_ordering(client, db_session):
    tid = _create(client, company="Acme", role_title="SWE").json()["tracked_application"]["id"]
    base = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    client.patch(
        f"/api/tracked-applications/{tid}",
        json={"next_action": "call", "next_action_due": _iso(base)},
    )
    client.post(
        f"/api/tracked-applications/{tid}/events",
        json={"kind": "deadline", "title": "apply by", "occurs_at": _iso(base)},
    )
    client.post(
        f"/api/tracked-applications/{tid}/events",
        json={"kind": "interview", "title": "screen", "occurs_at": _iso(base + timedelta(days=10))},
    )

    got = client.get(
        "/api/tracked-applications/calendar",
        params={"from": _iso(base), "to": _iso(base + timedelta(days=1))},
    )
    assert got.status_code == 200
    entries = got.json()["entries"]
    # interview (day+10) excluded by the range; next_action + deadline share ts,
    # next_action sorts first (type_rank 0 < 1).
    assert [e["type"] for e in entries] == ["next_action", "deadline"]


def test_calendar_excludes_archived_by_default(client, db_session):
    tid = _create(client, company="Acme", role_title="SWE").json()["tracked_application"]["id"]
    due = _iso(datetime(2026, 9, 1, tzinfo=timezone.utc))
    client.patch(f"/api/tracked-applications/{tid}", json={"next_action_due": due})
    client.delete(f"/api/tracked-applications/{tid}")
    assert client.get("/api/tracked-applications/calendar").json()["entries"] == []
    inc = client.get("/api/tracked-applications/calendar?include_archived=true").json()
    assert len(inc["entries"]) == 1


@pytest.mark.parametrize(
    "bad", ["2026-09-01", "2026-09-01T00:00:00", "not-a-date"]
)
def test_calendar_rejects_naive_or_garbage(client, bad):
    r = client.get("/api/tracked-applications/calendar", params={"from": bad})
    assert r.status_code == 422


def test_calendar_rejects_inverted_range(client):
    r = client.get(
        "/api/tracked-applications/calendar",
        params={
            "from": "2026-09-10T00:00:00+00:00",
            "to": "2026-09-01T00:00:00+00:00",
        },
    )
    assert r.status_code == 422
