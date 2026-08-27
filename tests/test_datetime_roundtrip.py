"""Datetime storage / reload / JSON characterization.

The models use `default_factory=datetime.utcnow` (naive UTC) with plain DateTime
columns. This pins how a value survives commit -> reload -> API serialization so
a future change to timezone-aware values is a conscious, tested decision rather
than a silent drift that later raises 'can't compare offset-naive and
offset-aware'.
"""

import re
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from backend.models import Job


def _utcnow_naive() -> datetime:
    """Naive UTC 'now' without the datetime.utcnow() deprecation warning - matches
    what the models store."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_first_seen_at_roundtrips_as_naive_utc(db_session):
    before = _utcnow_naive()
    job = Job(
        id="greenhouse:acme:1", source="greenhouse", title="Software Engineer",
        company="Acme", location_text="London, UK", url="https://example.com/1",
    )
    db_session.add(job)
    db_session.commit()
    db_session.expire_all()

    reloaded = db_session.exec(select(Job)).one()
    value = reloaded.first_seen_at

    assert isinstance(value, datetime)
    assert value.tzinfo is None, "column round-trips as naive; see module docstring"
    assert before - timedelta(seconds=5) <= value <= _utcnow_naive() + timedelta(seconds=5)

    # Comparing two reloaded naive values must not raise.
    other = Job(id="greenhouse:acme:2", source="greenhouse", title="Software Engineer",
                company="Acme", location_text="London, UK", url="https://example.com/2")
    db_session.add(other)
    db_session.commit()
    db_session.expire_all()
    rows = db_session.exec(select(Job).order_by(Job.first_seen_at)).all()
    assert rows[0].first_seen_at <= rows[1].first_seen_at


def test_raw_sqlite_value_has_no_timezone_suffix(db_session):
    db_session.add(Job(
        id="greenhouse:acme:1", source="greenhouse", title="Software Engineer",
        company="Acme", location_text="London, UK", url="https://example.com/1",
    ))
    db_session.commit()
    raw_str = db_session.connection().exec_driver_sql(
        "SELECT first_seen_at FROM job WHERE id = 'greenhouse:acme:1'"
    ).scalar()
    assert isinstance(raw_str, str)
    assert not raw_str.endswith("Z")
    assert "+00:00" not in raw_str
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", raw_str)  # space-separated, naive


def test_api_serializes_datetime_without_offset(client, db_session):
    db_session.add(Job(
        id="greenhouse:acme:1", source="greenhouse", title="Software Engineer",
        company="Acme", location_text="London, UK", city_key="london",
        url="https://example.com/1",
    ))
    db_session.commit()

    body = client.get("/api/jobs").json()
    stamp = body[0]["first_seen_at"]

    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", stamp)
    assert not stamp.endswith("Z")
    assert not re.search(r"[+-]\d{2}:\d{2}$", stamp)
    # It parses back to a naive datetime.
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is None
    # And is close to now-ish in UTC terms.
    assert abs(parsed - _utcnow_naive()) < timedelta(minutes=5)
