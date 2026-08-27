"""Plain helper module for the test suite.

Lives outside conftest.py so it is imported exactly once (pytest imports
conftest.py as the top-level module `conftest`, so `from tests.conftest import x`
would re-execute it and, e.g., double-register the SQLAlchemy connect guard).
Test modules and conftest both do `from _helpers import ...` - pytest puts the
tests/ directory on sys.path.
"""

import json
import os
import pathlib

from sqlalchemy import event
from sqlalchemy.engine import Engine

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

# The repository's real database. Only ever compared as a normalized string -
# never opened, connected to, or stat-ed.
REAL_DB_PATH = os.path.normcase(os.path.abspath(REPO_ROOT / "jobs.db"))


# --------------------------------------------------------------------------- #
# Forbidden-database connect guard
# --------------------------------------------------------------------------- #
def _connection_files(dbapi_connection) -> list[str]:
    """Normalized absolute paths of every file a SQLite connection has attached
    (`main` plus any `ATTACH`ed), read from `PRAGMA database_list`."""
    try:
        rows = dbapi_connection.execute("PRAGMA database_list").fetchall()
    except Exception:
        return []
    return [
        os.path.normcase(os.path.abspath(row[2]))
        for row in rows
        if row[2]  # in-memory DBs report an empty filename
    ]


def make_forbidden_path_guard(forbidden_path):
    """Return a SQLAlchemy `connect` listener that raises RuntimeError if the
    just-opened connection points at `forbidden_path`."""
    forbidden = os.path.normcase(os.path.abspath(forbidden_path))

    def _guard(dbapi_connection, connection_record):
        if forbidden in _connection_files(dbapi_connection):
            raise RuntimeError(
                f"TEST SAFETY: refused connection to forbidden database {forbidden!r}"
            )

    return _guard


_REAL_DB_GUARD_INSTALLED = False


def install_real_db_guard() -> None:
    """Idempotently install a session-wide guard against connecting to the real
    jobs.db. Safe to call from multiple conftest imports / xdist workers."""
    global _REAL_DB_GUARD_INSTALLED
    if _REAL_DB_GUARD_INSTALLED:
        return
    event.listen(Engine, "connect", make_forbidden_path_guard(REAL_DB_PATH))
    _REAL_DB_GUARD_INSTALLED = True


# --------------------------------------------------------------------------- #
# Fixture loading
# --------------------------------------------------------------------------- #
def load_fixture_file(name: str):
    path = FIXTURES_DIR / name
    if path.suffix == ".json":
        return json.loads(path.read_text())
    return path.read_text()


# --------------------------------------------------------------------------- #
# httpx stand-ins for the source adapters
# --------------------------------------------------------------------------- #
class FakeResponse:
    """Minimal stand-in for an `httpx.Response`."""

    def __init__(self, *, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("FakeResponse has no JSON body")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPError(f"HTTP {self.status_code}")


class FakeHTTPClient:
    """Stand-in for `httpx.Client` used as a context manager by source adapters.

    `handler(method, url, kwargs) -> FakeResponse`
    """

    def __init__(self, handler, **kwargs):
        self._handler = handler
        self.init_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return self._handler("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._handler("POST", url, kwargs)
