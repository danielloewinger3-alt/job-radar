"""Projects CRUD and single-row Profile API."""

from sqlmodel import select

from backend.models import Profile, Project


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
def test_projects_list_empty(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == []


def test_project_create_then_list(client):
    r = client.post(
        "/api/projects",
        json={"title": "Job Radar", "description": "this app", "tags": "python,fastapi", "link": "http://x"},
    )
    assert r.status_code == 200
    created = r.json()
    assert created["title"] == "Job Radar"
    assert created["id"]

    listed = client.get("/api/projects").json()
    assert [p["id"] for p in listed] == [created["id"]]


def test_project_delete_happy(client, db_session):
    pid = client.post("/api/projects", json={"title": "Temp"}).json()["id"]
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200
    db_session.expire_all()
    assert db_session.exec(select(Project)).all() == []


def test_project_delete_missing_is_404(client):
    r = client.delete("/api/projects/999")
    assert r.status_code == 404
    assert r.json()["detail"] == "project not found"


# --------------------------------------------------------------------------- #
# Profile (row id is always 1)
# --------------------------------------------------------------------------- #
def test_get_profile_autocreates_singleton(client, db_session):
    r = client.get("/api/profile")
    assert r.status_code == 200
    assert r.json()["id"] == 1
    assert db_session.exec(select(Profile)).one().id == 1


def test_put_profile_updates_fields(client):
    client.get("/api/profile")   # ensure it exists
    r = client.put(
        "/api/profile",
        json={"full_name": "Ada Lovelace", "email": "ada@example.com", "phone": "", "linkedin": "", "location": "London"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["full_name"] == "Ada Lovelace"
    assert body["location"] == "London"

    again = client.get("/api/profile").json()
    assert again["full_name"] == "Ada Lovelace"
