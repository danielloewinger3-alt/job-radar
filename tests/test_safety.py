"""Proof that the isolation machinery in conftest actually works.

Critically: the repository's real jobs.db is NEVER opened here. The tripwire is
exercised against a sacrificial temporary database; the real path is only ever
compared as a normalized string.
"""

import os
import socket

import pytest
from sqlalchemy import create_engine, event

import backend.config as backend_config
import backend.db as backend_db
from _helpers import REAL_DB_PATH, make_forbidden_path_guard


# --------------------------------------------------------------------------- #
# Env / key isolation
# --------------------------------------------------------------------------- #
def test_backend_config_carries_no_real_credentials():
    for name in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "REED_API_KEY",
        "USAJOBS_API_KEY", "USAJOBS_USER_AGENT",
        "GITHUB_USERNAME", "GITHUB_TOKEN", "COMPANIES_HOUSE_API_KEY",
    ):
        assert getattr(backend_config, name) == "", name


# --------------------------------------------------------------------------- #
# Database isolation
# --------------------------------------------------------------------------- #
def test_active_engine_points_at_isolated_tmp_db():
    active = os.path.normcase(os.path.abspath(backend_db.engine.url.database))
    assert active != REAL_DB_PATH
    assert os.path.basename(active) == "test_jobs.db"


def test_real_db_path_is_only_ever_a_string_here():
    # Sanity on the constant; we never open it.
    assert REAL_DB_PATH == os.path.normcase(os.path.abspath(REAL_DB_PATH))
    assert os.path.basename(REAL_DB_PATH) == "jobs.db"


def test_forbidden_path_guard_raises_on_a_sacrificial_db(tmp_path):
    sacrificial = tmp_path / "sacrificial.db"
    eng = create_engine(f"sqlite:///{sacrificial}")
    guard = make_forbidden_path_guard(str(sacrificial))
    event.listen(eng, "connect", guard)
    try:
        with pytest.raises(Exception) as excinfo:
            eng.connect()
        assert "forbidden database" in str(excinfo.value)
        # Prove it was our guard, and that connecting *without* the guard is fine.
    finally:
        event.remove(eng, "connect", guard)
        eng.dispose()

    ok_engine = create_engine(f"sqlite:///{sacrificial}")
    try:
        with ok_engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT 1").scalar() == 1
    finally:
        ok_engine.dispose()


def test_guard_ignores_connections_to_allowed_paths(tmp_path):
    # The session-wide real-jobs.db guard must not trip on an ordinary temp DB.
    eng = create_engine(f"sqlite:///{tmp_path / 'plain.db'}")
    try:
        with eng.connect() as conn:
            assert conn.exec_driver_sql("SELECT 1").scalar() == 1
    finally:
        eng.dispose()


# --------------------------------------------------------------------------- #
# Network isolation
# --------------------------------------------------------------------------- #
def test_inet_connect_is_blocked_with_family_and_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            s.connect(("192.0.2.1", 80))       # TEST-NET-1, never routable
        msg = str(excinfo.value)
        assert "blocked network connect" in msg
        assert "family=" in msg and "address=" in msg
    finally:
        s.close()


def test_inet6_connect_is_blocked():
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError):
            s.connect(("::1", 80))
    finally:
        s.close()


def test_loopback_is_also_blocked_decision_is_by_family_not_host():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="no AF_UNIX on this platform")
def test_non_network_socket_family_is_delegated(tmp_path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError) as excinfo:
            s.connect(str(tmp_path / "missing.sock"))
        # delegated to the real connect -> a real OSError, not our RuntimeError
        assert not isinstance(excinfo.value, RuntimeError)
    finally:
        s.close()


def test_testclient_still_works_under_the_network_guard(client):
    assert client.get("/api/news/categories").status_code == 200


def test_sqlite_file_io_unaffected_by_network_guard(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'io.db'}")
    try:
        with eng.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE t (x INTEGER)")
            conn.exec_driver_sql("INSERT INTO t VALUES (42)")
        with eng.connect() as conn:
            assert conn.exec_driver_sql("SELECT x FROM t").scalar() == 42
    finally:
        eng.dispose()
