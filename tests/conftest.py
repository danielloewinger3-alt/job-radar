"""Test isolation and shared fixtures for the Job Radar regression suite.

Safety guarantees established here, most of them *before* any ``backend`` module
is imported during collection:

* **No paid API calls / no real auth** - every source / API-key environment
  variable is forced to ``""`` before ``backend.config`` is imported, so its
  module-level constants can never pick up a real key.
* **The real ``jobs.db`` is never opened.** A process-wide SQLAlchemy ``connect``
  guard aborts any connection whose on-disk file resolves to the repository's
  real ``jobs.db``. That path is only ever compared as a normalized string;
  it is never opened, inspected, or written beside.
* **Every test gets a throwaway SQLite file** (``isolated_db``), and the
  application engine is rebound to it before ``backend.main`` is imported.
* **No live network** - every test blocks ``AF_INET`` / ``AF_INET6`` socket
  connects (``_no_network``); non-network socket families and plain file I/O
  (SQLite) are untouched.
"""

import os
import socket
import sys

import pytest

# --------------------------------------------------------------------------- #
# 1. Force source / API-key env vars empty BEFORE backend.config is imported.
#    Setting them (rather than popping) also beats a stray .env, because
#    python-dotenv's load_dotenv() uses override=False.
# --------------------------------------------------------------------------- #
_FORCE_EMPTY_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "REED_API_KEY",
    "USAJOBS_API_KEY",
    "USAJOBS_USER_AGENT",
    "GITHUB_USERNAME",
    "GITHUB_TOKEN",
    "COMPANIES_HOUSE_API_KEY",
)
for _name in _FORCE_EMPTY_ENV:
    os.environ[_name] = ""
os.environ.setdefault("POLL_INTERVAL_MINUTES", "60")

# _helpers does not import backend; safe to import right after the env scrub.
from _helpers import (  # noqa: E402
    FakeHTTPClient,
    FakeResponse,
    REAL_DB_PATH,
    install_real_db_guard,
    load_fixture_file,
    make_forbidden_path_guard,
)

# 2. Real-jobs.db connect tripwire (idempotent; string compare only).
install_real_db_guard()

# Re-export for tests that historically imported these from conftest.
__all__ = [
    "FakeHTTPClient",
    "FakeResponse",
    "REAL_DB_PATH",
    "make_forbidden_path_guard",
]


# --------------------------------------------------------------------------- #
# 3. Block real network. Decision is by socket family, not host string.
# --------------------------------------------------------------------------- #
_BLOCKED_SOCKET_FAMILIES = {socket.AF_INET, socket.AF_INET6}
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked_connect(self, address, *args, **kwargs):
        if self.family in _BLOCKED_SOCKET_FAMILIES:
            raise RuntimeError(
                f"TEST SAFETY: blocked network connect "
                f"(family={self.family!r}, address={address!r})"
            )
        return _REAL_CONNECT(self, address, *args, **kwargs)

    def _blocked_connect_ex(self, address, *args, **kwargs):
        if self.family in _BLOCKED_SOCKET_FAMILIES:
            raise RuntimeError(
                f"TEST SAFETY: blocked network connect_ex "
                f"(family={self.family!r}, address={address!r})"
            )
        return _REAL_CONNECT_EX(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)


# --------------------------------------------------------------------------- #
# 4. Per-test throwaway database + upload dir. Autouse so nothing can reach the
#    module-global engine (which points at the real jobs.db) by accident.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    from sqlmodel import SQLModel, create_engine

    import backend.config as backend_config
    import backend.db as backend_db
    import backend.models  # noqa: F401  registers every table on SQLModel.metadata

    db_file = tmp_path / "test_jobs.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(backend_db, "engine", test_engine)

    upload_dir = tmp_path / "uploads" / "cvs"
    upload_dir.mkdir(parents=True)
    monkeypatch.setattr(backend_config, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(backend_db, "UPLOAD_DIR", upload_dir)
    if "backend.main" in sys.modules:
        monkeypatch.setattr(sys.modules["backend.main"], "UPLOAD_DIR", upload_dir)

    SQLModel.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        test_engine.dispose()


@pytest.fixture
def db_session(isolated_db):
    from backend.db import get_session

    with get_session() as session:
        yield session


@pytest.fixture
def client(isolated_db, monkeypatch):
    """FastAPI TestClient with startup side effects neutralized.

    Uses a plain ``TestClient(app)`` (no context manager) so the lifespan / startup
    hook does not run - no poller thread, no scheduler. Tables already exist from
    ``isolated_db``. ``test_lifespan.py`` is the one place the lifespan is exercised.
    """
    import backend.config as backend_config
    import backend.main as backend_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(backend_main, "poll_all_sources", lambda: {})
    monkeypatch.setattr(backend_main.scheduler_module, "start", lambda *a, **k: None)
    monkeypatch.setattr(backend_main, "UPLOAD_DIR", backend_config.UPLOAD_DIR)

    return TestClient(backend_main.app)


# --------------------------------------------------------------------------- #
# Fixture-data helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def load_fixture():
    return load_fixture_file


@pytest.fixture
def fake_httpx():
    """Returns ``(FakeResponse, FakeHTTPClient)`` for building adapter stubs."""
    return FakeResponse, FakeHTTPClient
