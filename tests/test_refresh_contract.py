"""Regression test for the async /api/refresh contract the frontend depends on.

Pins the response the frontend's ``refresh()`` reads:
  * HTTP 202
  * body ``{"status": "started" | "already_running", "new_jobs": {}, "total_new": 0}``

and deterministically exercises both status branches in one process. This is the
backend half of the frontend/backend refresh contract - there is no JS
toolchain, so the frontend half is covered by manual QA.

Safety: the ``client`` fixture rebinds the app to a disposable per-test SQLite
file and neutralizes ``backend.poller.poll_all_sources``; the autouse
``_no_network`` fixture blocks real INET sockets. This test additionally gates
the first poll with an Event so the second request is guaranteed to observe a
poll in flight, then releases and joins the worker so the poll gate is free and
no thread outlives the test.
"""

import threading

import backend.poller as backend_poller


def test_refresh_contract_started_then_already_running(client, monkeypatch):
    release = threading.Event()

    def _blocking_poll():
        # Hold the poll gate (released by poller._run_locked's finally block)
        # until the test lets go. Never touches the network.
        release.wait(timeout=5)
        return {}

    monkeypatch.setattr(backend_poller, "poll_all_sources", _blocking_poll)

    try:
        first = client.post("/api/refresh")
        second = client.post("/api/refresh")

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text

        first_body = first.json()
        second_body = second.json()

        assert first_body["status"] == "started"
        assert second_body["status"] == "already_running"

        for body in (first_body, second_body):
            assert body["new_jobs"] == {}
            assert body["total_new"] == 0
    finally:
        release.set()
        assert backend_poller.join_worker(timeout=5), "poll worker did not finish"
