"""Additive, idempotent ``business`` columns for the outreach workstream.

``run(engine)`` is the deterministic hook ``backend.features.run_feature_migrations``
calls after the legacy additive migration. It only ever ``ALTER TABLE ... ADD
COLUMN`` -- it never drops, recreates, renames or copies ``business``.

Column set (all additive):

* ``official_website``        TEXT DEFAULT ''    -- verified final URL (same-host)
* ``website_confidence``      TEXT DEFAULT ''    -- provenance string, never numeric:
                                                   '' | osm | guessed_verified |
                                                   companies_house | manual
* ``contact_page_url``        TEXT DEFAULT ''
* ``discovery_at``            TEXT               -- completion; set ONLY on 'resolved'
* ``discovery_status``        TEXT DEFAULT ''    -- '' | resolved | unresolved |
                                                   unsafe | transient_failure
* ``discovery_error``         TEXT DEFAULT ''    -- bounded, sanitized code
* ``discovery_attempted_at``  TEXT               -- last attempt, any outcome
* ``discovery_attempts``      INTEGER DEFAULT 0  -- backoff counter
* ``contacts_collected_at``   TEXT               -- last successful contact crawl
"""

from __future__ import annotations

# (name, column definition) -- names/defs are literals, never interpolated input.
_BUSINESS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("official_website", "TEXT DEFAULT ''"),
    ("website_confidence", "TEXT DEFAULT ''"),
    ("contact_page_url", "TEXT DEFAULT ''"),
    ("discovery_at", "TEXT"),
    ("discovery_status", "TEXT DEFAULT ''"),
    ("discovery_error", "TEXT DEFAULT ''"),
    ("discovery_attempted_at", "TEXT"),
    ("discovery_attempts", "INTEGER DEFAULT 0"),
    ("contacts_collected_at", "TEXT"),
)


def run(engine) -> None:
    """Add any missing outreach columns to ``business``. Safe to run repeatedly;
    a no-op when the table is absent or every column already exists."""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(business)").fetchall()
        if not rows:
            return  # no business table yet -- nothing to migrate, never create it
        existing = {row[1] for row in rows}
        for name, decl in _BUSINESS_COLUMNS:
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE business ADD COLUMN {name} {decl}")
        conn.commit()
