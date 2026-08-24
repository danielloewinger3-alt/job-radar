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
