"""Application-pack SQLModel table.

One tracked application has one *current* pack (``TrackedApplication.pack_id``)
plus retained version history: regeneration inserts a new row
(``version = prev + 1``, ``supersedes_pack_id = prev.id``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.models import UTCDateTime, utcnow


class ApplicationPack(SQLModel, table=True):
    __tablename__ = "application_pack"

    id: Optional[int] = Field(default=None, primary_key=True)  # serialised as pack_id
    tracked_application_id: int = Field(index=True)
    version: int = 1
    supersedes_pack_id: Optional[int] = None

    cover_letter: str = ""
    answers_json: str = "[]"
    context_summary_json: str = "{}"

    cv_id: Optional[int] = None
    project_ids_json: str = "[]"
    project_file_ids_json: str = "[]"

    generated_model: str = ""
    generated_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)

    reviewed: bool = False
    reviewed_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    reviewer_notes: str = ""

    # Always kept fresh (recomputed on every content mutation).
    content_fingerprint: str = ""
    # Captured only at review; cleared on any content change.
    reviewed_fingerprint: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
