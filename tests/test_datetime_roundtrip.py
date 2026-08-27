"""Datetime storage / reload / JSON characterization.

The models use `default_factory=utcnow` (timezone-aware UTC) with the
`UTCDateTime` type decorator. Every timestamp column round-trips as an
aware-UTC ``datetime`` and the API serializes it as ISO 8601 with a trailing
``Z`` designator. These tests pin that contract so a regression back to naive
values (which later raises "can't compare offset-naive and offset-aware") is
caught immediately.
"""

import re
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from backend.models import Job


def _utcnow_aware() -> datetime:
    """Timezone-aware UTC 'now' - matches what the models store."""
    return datetime.now(timezone.utc)


def test_first_seen_at_roundtrips_as_aware_utc(db_session):
    before = _utcnow_aware()
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
    assert value.tzinfo is not None, "column round-trips as aware; see module docstring"
    assert value.utcoffset() == timedelta(0), "aware value is UTC"
    assert before - timedelta(seconds=5) <= value <= _utcnow_aware() + timedelta(seconds=5)

    # Comparing two reloaded aware values must not raise.
    other = Job(id="greenhouse:acme:2", source="greenhouse", title="Software Engineer",
                company="Acme", location_text="London, UK", url="https://example.com/2")
    db_session.add(other)
    db_session.commit()
    db_session.expire_all()
    rows = db_session.exec(select(Job).order_by(Job.first_seen_at)).all()
    assert rows[0].first_seen_at <= rows[1].first_seen_at


def test_raw_sqlite_value_has_no_timezone_suffix(db_session):
    """Storage layer: SQLAlchemy still persists bare ISO text with no offset.
    The aware-UTC guarantee is applied on the way in (normalised to UTC) and on
    the way out (stamped back to UTC) by the UTCDateTime decorator, not by the
    on-disk representation."""
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


def test_api_serializes_datetime_with_trailing_z(client, db_session):
    db_session.add(Job(
        id="greenhouse:acme:1", source="greenhouse", title="Software Engineer",
        company="Acme", location_text="London, UK", city_key="london",
        url="https://example.com/1",
    ))
    db_session.commit()

    body = client.get("/api/jobs").json()
    stamp = body[0]["first_seen_at"]

    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", stamp)
    assert stamp.endswith("Z"), "API serializes timestamps with a trailing Z"
    assert "+00:00" not in stamp
    assert not re.search(r"[+-]\d{2}:\d{2}$", stamp)
    # It parses back to an aware UTC datetime.
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    # And is close to now-ish in UTC terms.
    assert abs(parsed - _utcnow_aware()) < timedelta(minutes=5)
