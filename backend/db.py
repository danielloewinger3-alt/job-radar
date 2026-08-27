import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from backend.config import DATABASE_URL, UPLOAD_DIR

logger = logging.getLogger("db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def _install_sqlite_pragmas(target_engine) -> None:
    """Register a connect-time hook applying the *per-connection* SQLite PRAGMAs.

    ``journal_mode`` (WAL) is a persistent property of the database file and is
    set once by :func:`_enable_wal`. ``busy_timeout`` and ``synchronous`` reset
    to their defaults on every new connection, so they are (re)applied here for
    every pooled connection.
    """

    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _enable_wal(target_engine) -> None:
    """Enable WAL journalling once. WAL persists in the database file header, so
    this is a safe, idempotent one-time switch. If the underlying filesystem
    cannot support WAL the PRAGMA reports the unchanged mode; we log and carry
    on rather than fail startup."""
    with target_engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode=WAL").scalar()
        if str(mode).lower() != "wal":
            logger.warning("SQLite WAL not enabled (journal_mode=%s); continuing", mode)
        conn.commit()


_install_sqlite_pragmas(engine)


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
    _enable_wal(engine)
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Request/task-scoped session: rolls back on exception, always closes.

    Callers keep the existing ``with get_session() as session:`` usage and
    remain responsible for calling ``session.commit()`` explicitly.
    """
    session = Session(engine)
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
