from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


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
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
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
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    notes: str = ""


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: str = ""
    tags: str = ""  # comma-separated freeform tags
    link: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Profile(SQLModel, table=True):
    """Single-row table (id is always 1) used to personalize generated cover letters."""
    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    location: str = ""
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    cv_id: Optional[int] = None
    cover_letter: str = ""
    review_notes: str = ""  # the reviewer model's critique, kept for transparency
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
