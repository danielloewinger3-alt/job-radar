from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, TypeDecorator
from sqlmodel import Field, SQLModel


class UTCDateTime(TypeDecorator):
    """Store tz-aware datetimes as UTC; always return tz-aware UTC datetimes.

    SQLAlchemy's SQLite ``DateTime`` persists bare ISO text and reloads it
    naive, so a plain column silently drops the offset on the round trip. This
    decorator normalises every write to aware-UTC and stamps every read back to
    aware-UTC, keeping the whole round trip consistent. Naive inputs (legacy
    rows written by the old ``datetime.utcnow()``) are treated as UTC.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    """Timezone-aware UTC now. Replacement for the deprecated ``datetime.utcnow()``."""
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)  # f"{source}:{external_id}"
    source: str
    title: str
    company: str
    location_text: str
    city_key: Optional[str] = Field(default=None, index=True)  # key into ALL_CITIES, or None if unmatched
    remote: bool = False
    url: str
    posted_at: Optional[str] = None  # ISO string as reported by source, best-effort
    first_seen_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    seen: bool = Field(default=False, index=True)
    description_snippet: str = ""
    description_full: str = ""  # plain-text, HTML stripped
    notes: str = ""  # user-entered: pay grade, start date, interview process, etc.


class CV(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str  # e.g. "Backend SWE resume"
    role_type: str = ""  # freeform tag, e.g. "Backend", "Data", "ML"
    filename: str  # stored filename on disk
    original_name: str  # original upload filename
    uploaded_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    notes: str = ""


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: str = ""
    tags: str = ""  # comma-separated freeform tags
    link: str = ""
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class Profile(SQLModel, table=True):
    """Single-row table (id is always 1) used to personalize generated cover letters."""
    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    location: str = ""
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    cv_id: Optional[int] = None
    cover_letter: str = ""
    review_notes: str = ""  # the reviewer model's critique, kept for transparency
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class Business(SQLModel, table=True):
    id: str = Field(primary_key=True)  # f"osm:{osm_type}:{osm_id}"
    area_key: str = Field(index=True)  # key into PROSPECT_AREAS
    category: str = Field(index=True)  # key into BUSINESS_CATEGORIES
    name: str
    lat: float
    lon: float
    address: str = ""
    phone: str = ""
    website: str = ""
    companies_house_number: str = ""
    companies_house_status: str = ""  # e.g. "active", "active — accounts overdue", "dissolved"
    description: str = ""  # Claude's one-line summary of what the business actually does
    opportunity_summary: str = ""  # Claude's assessment against the opportunity rubric
    opportunity_tags: str = ""  # comma-separated matched opportunity categories
    analyzed_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    discovered_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
