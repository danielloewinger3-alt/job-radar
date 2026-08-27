"""Deduplication / upsert behaviour of backend.poller.poll_all_sources."""

from backend import poller
from backend.models import Job
from backend.sources.base import RawJob


def _raw(**over):
    base = dict(
        source="greenhouse",
        external_id="acme:1",
        title="Software Engineer",
        company="Acme",
        location_text="London, UK",
        url="https://example.com/jobs/1",
        remote=False,
        posted_at="2026-08-01",
        description_snippet="",
        description_full="",
    )
    base.update(over)
    return RawJob(**base)


def _use_sources(monkeypatch, raw_jobs):
    monkeypatch.setattr(poller, "SOURCES", [("greenhouse", lambda: list(raw_jobs))])


def test_first_poll_inserts_second_poll_is_noop(monkeypatch, db_session):
    _use_sources(monkeypatch, [_raw(description_full="Full text", description_snippet="Full text")])

    assert poller.poll_all_sources() == {"greenhouse": 1}
    assert poller.poll_all_sources() == {"greenhouse": 0}

    from sqlmodel import select

    jobs = db_session.exec(select(Job)).all()
    assert len(jobs) == 1
    assert jobs[0].id == "greenhouse:acme:1"


def test_repoll_does_not_reset_seen_or_first_seen(monkeypatch, db_session):
    _use_sources(monkeypatch, [_raw()])
    poller.poll_all_sources()

    from sqlmodel import select

    job = db_session.exec(select(Job)).one()
    original_first_seen = job.first_seen_at
    job.seen = True
    db_session.add(job)
    db_session.commit()

    poller.poll_all_sources()

    db_session.expire_all()
    job = db_session.exec(select(Job)).one()
    assert job.seen is True
    assert job.first_seen_at == original_first_seen


def test_repoll_backfills_missing_description(monkeypatch, db_session):
    _use_sources(monkeypatch, [_raw(description_full="", description_snippet="")])
    poller.poll_all_sources()

    _use_sources(
        monkeypatch,
        [_raw(description_full="Now has detail", description_snippet="Now has detail")],
    )
    poller.poll_all_sources()

    from sqlmodel import select

    job = db_session.exec(select(Job)).one()
    assert job.description_full == "Now has detail"
    assert job.description_snippet == "Now has detail"


def test_filtered_out_jobs_are_never_inserted(monkeypatch, db_session):
    _use_sources(
        monkeypatch,
        [
            _raw(external_id="acme:senior", title="Senior Software Engineer"),
            _raw(external_id="acme:ok", title="Software Engineer"),
        ],
    )
    counts = poller.poll_all_sources()
    assert counts == {"greenhouse": 1}

    from sqlmodel import select

    ids = {j.id for j in db_session.exec(select(Job)).all()}
    assert ids == {"greenhouse:acme:ok"}


def test_source_exception_is_isolated(monkeypatch, db_session):
    def boom():
        raise RuntimeError("source down")

    monkeypatch.setattr(
        poller,
        "SOURCES",
        [("greenhouse", boom), ("lever", lambda: [_raw(source="lever", external_id="x:1")])],
    )
    counts = poller.poll_all_sources()
    assert counts == {"greenhouse": 0, "lever": 1}
