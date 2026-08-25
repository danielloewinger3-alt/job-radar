from sqlmodel import Session, SQLModel, create_engine

from backend.config import DATABASE_URL, UPLOAD_DIR

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def _add_missing_columns() -> None:
    """Additive, idempotent migration so an existing jobs.db picks up new Job columns
    without losing already-polled data (SQLModel.metadata.create_all only creates
    missing tables, not missing columns on existing ones)."""
    with engine.connect() as conn:
        job_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(job)").fetchall()}
        for name in ("description_full", "notes"):
            if name not in job_cols:
                conn.exec_driver_sql(f"ALTER TABLE job ADD COLUMN {name} TEXT DEFAULT ''")

        business_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(business)").fetchall()}
        if business_cols and "description" not in business_cols:
            conn.exec_driver_sql("ALTER TABLE business ADD COLUMN description TEXT DEFAULT ''")

        conn.commit()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_session() -> Session:
    return Session(engine)
