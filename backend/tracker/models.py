"""Application-tracker SQLModel tables.

Startup imports this module (via ``backend.features.import_feature_models``) so
every table below is registered on ``SQLModel.metadata`` before ``create_all()``.
The legacy ``backend.models.Application`` model is untouched.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models import UTCDateTime, utcnow


class TrackedStage(str, enum.Enum):
    interested = "interested"
    preparing = "preparing"
    applied = "applied"
    assessment = "assessment"
    recruiter_screen = "recruiter_screen"
    interview = "interview"
    final_interview = "final_interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


STAGE_VALUES = frozenset(s.value for s in TrackedStage)


class EventKind(str, enum.Enum):
    stage_change = "stage_change"
    deadline = "deadline"
    interview = "interview"
    note = "note"
    contact_log = "contact_log"
    follow_up_draft = "follow_up_draft"


EVENT_KIND_VALUES = frozenset(k.value for k in EventKind)


class TrackedApplication(SQLModel, table=True):
    __tablename__ = "tracked_application"
    # Nullable UNIQUE: SQLite permits multiple NULLs, so manual applications
    # (job_id IS NULL) are unaffected; job-linked rows are deduplicated.
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_tracked_application_job_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[str] = Field(default=None, index=True)
    legacy_application_id: Optional[int] = Field(default=None)
    pack_id: Optional[int] = Field(default=None)
    company: str
    role_title: str
    cv_id: Optional[int] = Field(default=None)
    stage: str = Field(default=TrackedStage.interested.value, index=True)
    origin: str = Field(default="manual")
    application_date: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    next_action: str = ""
    next_action_due: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    notes: str = ""
    archived: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class TrackedApplicationEvent(SQLModel, table=True):
    __tablename__ = "tracked_application_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    tracked_application_id: int = Field(index=True)
    kind: str
    title: str = ""
    body: str = ""
    occurs_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    from_stage: Optional[str] = Field(default=None)
    to_stage: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class TrackedApplicationContact(SQLModel, table=True):
    __tablename__ = "tracked_application_contact"

    id: Optional[int] = Field(default=None, primary_key=True)
    tracked_application_id: int = Field(index=True)
    name: str
    contact_role: str = ""
    email: str = ""
    phone: str = ""
    company: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class TrackedApplicationProjectLink(SQLModel, table=True):
    __tablename__ = "tracked_application_project_link"
    __table_args__ = (
        UniqueConstraint(
            "tracked_application_id",
            "project_id",
            name="uq_tracked_application_project",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tracked_application_id: int = Field(index=True)
    project_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
