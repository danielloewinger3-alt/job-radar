"""Project-file SQLModel table.

Registered on ``SQLModel.metadata`` at import time (via
``backend.features.import_feature_models``) so ``create_all()`` picks it up.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.models import UTCDateTime, utcnow


class ProjectFile(SQLModel, table=True):
    __tablename__ = "project_file"

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(index=True)
    # Sanitised display name -- used only for Content-Disposition / UI. Never a
    # filesystem path.
    original_name: str
    # UUID-based on-disk name -- the ONLY value ever joined to a path.
    stored_name: str
    extension: str
    byte_size: int = 0
    sha256: str = ""
    description: str = ""
    ai_context_enabled: bool = False
    # ok | truncated | unsupported | empty | error
    extract_status: str = "unsupported"
    extracted_text: str = ""
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
