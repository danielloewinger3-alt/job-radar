"""CV library API: upload validation, storage, retrieval, deletion."""

import io

from sqlmodel import select

from backend.models import CV


def _upload(client, *, filename="resume.pdf", content_type="application/pdf",
            data=b"%PDF-1.4 minimal", label="Backend SWE", role_type="Backend"):
    return client.post(
        "/api/cvs",
        files={"file": (filename, io.BytesIO(data), content_type)},
        data={"label": label, "role_type": role_type},
    )


def test_list_cvs_empty(client):
    r = client.get("/api/cvs")
    assert r.status_code == 200
    assert r.json() == []


def test_upload_rejects_non_pdf(client):
    r = _upload(client, content_type="text/plain")
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_upload_rejects_oversize(client, monkeypatch):
    import backend.main as backend_main

    monkeypatch.setattr(backend_main, "MAX_CV_BYTES", 4)
    r = _upload(client, data=b"way too big")
    assert r.status_code == 400
    assert "too large" in r.json()["detail"]


def test_upload_stores_file_and_row(client, db_session):
    r = _upload(client, data=b"%PDF-1.4 hello")
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "Backend SWE"
    assert body["original_name"] == "resume.pdf"

    cv = db_session.exec(select(CV)).one()
    import backend.main as backend_main

    stored = backend_main.UPLOAD_DIR / cv.filename
    assert stored.exists()
    assert stored.read_bytes() == b"%PDF-1.4 hello"


def test_get_cv_file_missing_row_is_404(client):
    r = client.get("/api/cvs/999/file")
    assert r.status_code == 404
    assert r.json()["detail"] == "cv not found"


def test_get_cv_file_missing_on_disk_is_404(client, db_session):
    _upload(client)
    cv = db_session.exec(select(CV)).one()
    import backend.main as backend_main

    (backend_main.UPLOAD_DIR / cv.filename).unlink()
    r = client.get(f"/api/cvs/{cv.id}/file")
    assert r.status_code == 404
    assert r.json()["detail"] == "file missing on disk"


def test_delete_cv_missing_is_404(client):
    r = client.delete("/api/cvs/999")
    assert r.status_code == 404


def test_delete_cv_removes_row_and_file(client, db_session):
    _upload(client)
    cv = db_session.exec(select(CV)).one()
    import backend.main as backend_main

    path = backend_main.UPLOAD_DIR / cv.filename
    assert path.exists()

    r = client.delete(f"/api/cvs/{cv.id}")
    assert r.status_code == 200
    assert not path.exists()

    db_session.expire_all()
    assert db_session.exec(select(CV)).all() == []
