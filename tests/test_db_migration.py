"""Additive, idempotent column migration in backend.db._add_missing_columns."""

from sqlmodel import SQLModel, create_engine

import backend.db as backend_db


def _legacy_engine(tmp_path, *, with_business=True):
    """A SQLite file with an OLD `job` schema (missing description_full / notes)
    and, optionally, an OLD `business` schema (missing description)."""
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE job (id TEXT PRIMARY KEY, source TEXT, title TEXT, "
            "company TEXT, location_text TEXT, url TEXT)"
        )
        conn.exec_driver_sql(
            "INSERT INTO job (id, source, title, company, location_text, url) "
            "VALUES ('greenhouse:acme:1', 'greenhouse', 'Software Engineer', "
            "'Acme', 'London, UK', 'https://example.com/1')"
        )
        if with_business:
            conn.exec_driver_sql(
                "CREATE TABLE business (id TEXT PRIMARY KEY, area_key TEXT, "
                "category TEXT, name TEXT, lat REAL, lon REAL)"
            )
            conn.exec_driver_sql(
                "INSERT INTO business (id, area_key, category, name, lat, lon) "
                "VALUES ('osm:node:1', 'bristol', 'dentist', 'Bright Smiles', 51.4, -2.5)"
            )
    return eng


def _columns(engine, table):
    with engine.connect() as conn:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def test_adds_missing_columns_and_preserves_data(tmp_path, monkeypatch):
    eng = _legacy_engine(tmp_path)
    monkeypatch.setattr(backend_db, "engine", eng)

    assert "description_full" not in _columns(eng, "job")

    backend_db._add_missing_columns()

    assert {"description_full", "notes"} <= _columns(eng, "job")
    assert "description" in _columns(eng, "business")

    with eng.connect() as conn:
        title = conn.exec_driver_sql(
            "SELECT title FROM job WHERE id = 'greenhouse:acme:1'"
        ).scalar()
        desc = conn.exec_driver_sql(
            "SELECT description_full FROM job WHERE id = 'greenhouse:acme:1'"
        ).scalar()
    assert title == "Software Engineer"
    assert desc == ""          # ALTER ... DEFAULT ''


def test_migration_is_idempotent(tmp_path, monkeypatch):
    eng = _legacy_engine(tmp_path)
    monkeypatch.setattr(backend_db, "engine", eng)

    backend_db._add_missing_columns()
    # Second run must not raise (columns already present).
    backend_db._add_missing_columns()

    assert {"description_full", "notes"} <= _columns(eng, "job")


def test_migration_tolerates_absent_business_table(tmp_path, monkeypatch):
    eng = _legacy_engine(tmp_path, with_business=False)
    monkeypatch.setattr(backend_db, "engine", eng)

    backend_db._add_missing_columns()   # `business_cols` empty -> guarded, no crash

    assert {"description_full", "notes"} <= _columns(eng, "job")


def test_init_db_on_empty_file_creates_current_schema(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(backend_db, "engine", eng)
    # init_db also makes UPLOAD_DIR; keep it inside tmp_path
    monkeypatch.setattr(backend_db, "UPLOAD_DIR", tmp_path / "uploads")

    backend_db.init_db()

    job_cols = _columns(eng, "job")
    assert {"id", "title", "description_full", "notes", "first_seen_at", "seen"} <= job_cols
    assert "business" in {
        r[0]
        for r in eng.connect().exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
