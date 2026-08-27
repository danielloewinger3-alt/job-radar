"""Application startup / lifespan.

The core test is implementation-neutral: it must pass on the current master
(daemon poll thread + one module-global BackgroundScheduler + blocking
/api/refresh) *and* on the backend-reliability branch (fresh scheduler per
lifespan, tracked worker, 202 refresh). It asserts only observable invariants
and touches only names present on both branches.
"""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect


def _scheduler_has_shutdown():
    import backend.scheduler as s

    return hasattr(s, "shutdown")


def _patch_poll(monkeypatch, m, fn):
    """Neutralize the poll at whichever seam this branch uses.

    master calls ``backend.main.poll_all_sources`` (imported name); the
    restart-safe branch's background worker looks up
    ``backend.poller.poll_all_sources`` at call time. Patch both so this test
    stays implementation-neutral.
    """
    import backend.poller as poller_mod

    monkeypatch.setattr(poller_mod, "poll_all_sources", fn)
    monkeypatch.setattr(m, "poll_all_sources", fn, raising=False)


def _join_poll_worker(timeout: float) -> bool:
    """Wait for any background poll worker to finish (and release the gate).

    No-op / True on master, which has no tracked worker."""
    import backend.poller as poller_mod

    joiner = getattr(poller_mod, "join_worker", None)
    return joiner(timeout=timeout) if joiner else True


def test_app_lifespan_boots_and_serves(monkeypatch):
    import backend.db as backend_db
    import backend.main as m

    seen = {"sched_start": 0, "poll": 0}
    _patch_poll(
        monkeypatch, m,
        lambda: (seen.__setitem__("poll", seen["poll"] + 1), {})[1],
    )
    monkeypatch.setattr(
        m.scheduler_module, "start",
        lambda *a, **k: seen.__setitem__("sched_start", seen["sched_start"] + 1),
    )
    if _scheduler_has_shutdown():
        monkeypatch.setattr(m.scheduler_module, "shutdown", lambda *a, **k: None)

    with TestClient(m.app) as c:                       # runs startup / lifespan
        assert c.get("/api/news/categories").status_code == 200

    # leaving the block ran teardown without raising
    assert seen["sched_start"] >= 1
    # init_db() ran against the ISOLATED engine, not the real jobs.db
    assert "job" in inspect(backend_db.engine).get_table_names()
    # seen["poll"] is recorded but intentionally not asserted: the current branch
    # runs it once in a daemon thread; a lock-guarded branch may legitimately skip.


@pytest.mark.skipif(
    not _scheduler_has_shutdown(),
    reason="restart-safe scheduler (backend.scheduler.shutdown) not on this branch",
)
def test_lifespan_survives_two_cycles(monkeypatch):
    """Forward-looking: dormant on master, active once the reliability branch
    lands a per-lifespan scheduler + shutdown()."""
    import backend.main as m

    _patch_poll(monkeypatch, m, lambda: {})

    baseline = threading.active_count()
    for _ in range(2):
        with TestClient(m.app) as c:
            assert c.get("/api/news/categories").status_code == 200

    # no unbounded thread growth across cycles
    assert threading.active_count() <= baseline + 1


def test_refresh_already_running_contract(client, monkeypatch):
    """Capability-gated by the endpoint's *public behaviour*, not an internal
    class. Skips on the blocking master implementation."""
    import backend.main as m

    _patch_poll(monkeypatch, m, lambda: {})
    probe = client.post("/api/refresh")
    if not (probe.status_code == 202 and probe.json().get("status") == "started"):
        pytest.skip("async /api/refresh contract not present on this branch")
    # let the probe's fast worker finish and release the poll gate
    assert _join_poll_worker(timeout=5)

    release = threading.Event()
    _patch_poll(monkeypatch, m, lambda: release.wait(timeout=5) or {})
    first = client.post("/api/refresh")
    second = client.post("/api/refresh")
    release.set()
    assert _join_poll_worker(timeout=5)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json().get("status") == "already_running"
