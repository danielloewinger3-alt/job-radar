"""Additive `business` columns for the outreach workstream
(``backend.outreach.migrate``)."""

from sqlmodel import SQLModel, create_engine

import backend.db as backend_db
import backend.outreach.models  # noqa: F401  -- registers the 7 outreach tables
from backend.outreach import migrate

_EXPECTED = {
    "official_website",
    "website_confidence",
    "contact_page_url",
    "discovery_at",
    "discovery_status",
    "discovery_error",
    "discovery_attempted_at",
    "discovery_attempts",
    "contacts_collected_at",
}


def _cols(engine, table):
    with engine.connect() as conn:
        return {row[1]: row for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def _legacy_engine(tmp_path, *, with_business=True):
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE job (id TEXT PRIMARY KEY, source TEXT, title TEXT, "
            "company TEXT, location_text TEXT, url TEXT)"
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


def test_adds_all_nine_columns_with_types_and_defaults(tmp_path):
    eng = _legacy_engine(tmp_path)
    migrate.run(eng)
    cols = _cols(eng, "business")
    assert _EXPECTED <= set(cols)
    # website_confidence is TEXT provenance, default '' -- never numeric
    assert cols["website_confidence"][2].upper() == "TEXT"
    assert cols["website_confidence"][4] == "''"
    assert cols["discovery_status"][2].upper() == "TEXT"
    assert cols["discovery_status"][4] == "''"
    assert cols["discovery_attempts"][2].upper() == "INTEGER"
    assert cols["discovery_attempts"][4] == "0"
    # nullable, no default
    for nullable in ("discovery_at", "discovery_attempted_at", "contacts_collected_at"):
        assert cols[nullable][4] is None


def test_idempotent(tmp_path):
    eng = _legacy_engine(tmp_path)
    migrate.run(eng)
    migrate.run(eng)  # must not raise / must not duplicate
    names = [r[1] for r in _cols(eng, "business").values()]
    assert len(names) == len(set(names))
    assert _EXPECTED <= set(names)


def test_preserves_existing_rows(tmp_path):
    eng = _legacy_engine(tmp_path)
    with eng.connect() as conn:
        before = conn.exec_driver_sql("SELECT id, name, area_key FROM business").fetchall()
    migrate.run(eng)
    with eng.connect() as conn:
        after = conn.exec_driver_sql("SELECT id, name, area_key FROM business").fetchall()
        wc = conn.exec_driver_sql(
            "SELECT website_confidence, discovery_status FROM business WHERE id='osm:node:1'"
        ).fetchone()
    assert before == after
    assert wc == ("", "")


def test_tolerates_absent_business_table(tmp_path):
    eng = _legacy_engine(tmp_path, with_business=False)
    migrate.run(eng)  # no business table -> no-op, no crash
    with eng.connect() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "business" not in tables


def test_noop_when_columns_present(tmp_path):
    eng = _legacy_engine(tmp_path)
    migrate.run(eng)
    sql_before = _business_create_sql(eng)
    migrate.run(eng)
    assert _business_create_sql(eng) == sql_before


def _business_create_sql(engine):
    with engine.connect() as conn:
        return conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='business'"
        ).scalar()


def test_only_alters_never_recreates(tmp_path):
    eng = _legacy_engine(tmp_path)
    sql_before = _business_create_sql(eng)
    migrate.run(eng)
    sql_after = _business_create_sql(eng)
    # ADD COLUMN appends before the closing paren: the original column list is
    # preserved verbatim as a prefix, the table is never dropped/recreated, and
    # the pre-existing row survives.
    assert sql_after.count("CREATE TABLE") == 1
    assert sql_after.startswith(sql_before[:-1])  # everything up to the final ')'
    for original_col in ("id TEXT PRIMARY KEY", "area_key TEXT", "lat REAL", "lon REAL"):
        assert original_col in sql_after
    with eng.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM business").scalar() == 1


def test_init_db_runs_outreach_migration(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(backend_db, "engine", eng)
    monkeypatch.setattr(backend_db, "UPLOAD_DIR", tmp_path / "uploads")

    backend_db.init_db()

    assert _EXPECTED <= set(_cols(eng, "business"))
    with eng.connect() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("outreachcontact", "contactevidence", "outreachthread", "outreachevent",
              "outreachattempt", "outreachsuppression", "discoverylog"):
        assert t in tables


def test_legacy_business_row_still_loadable_after_migration(tmp_path, monkeypatch):
    from sqlmodel import Session

    from backend.models import Business

    eng = _legacy_engine(tmp_path)
    # add the columns backend.db's own legacy migration would add, so the ORM model matches
    with eng.begin() as conn:
        for col in ("address", "phone", "website", "companies_house_number",
                    "companies_house_status", "description", "opportunity_summary",
                    "opportunity_tags"):
            conn.exec_driver_sql(f"ALTER TABLE business ADD COLUMN {col} TEXT DEFAULT ''")
        conn.exec_driver_sql("ALTER TABLE business ADD COLUMN analyzed_at DATETIME")
        conn.exec_driver_sql("ALTER TABLE business ADD COLUMN discovered_at DATETIME")
    migrate.run(eng)
    with Session(eng) as s:
        biz = s.get(Business, "osm:node:1")
        assert biz is not None and biz.name == "Bright Smiles"


def test_outreach_attempt_has_no_unique_business_email_constraint():
    from backend.outreach.models import OutreachAttempt

    uniques = [
        c for c in OutreachAttempt.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    ]
    for c in uniques:
        assert {col.name for col in c.columns} != {"business_id", "email_normalized"}
