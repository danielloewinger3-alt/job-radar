from dataclasses import dataclass


@dataclass
class RawJob:
    source: str
    external_id: str
    title: str
    company: str
    location_text: str
    url: str
    remote: bool = False
    posted_at: str | None = None
    description_snippet: str = ""
