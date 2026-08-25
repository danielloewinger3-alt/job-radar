import httpx

from backend.config import LEVER_COMPANIES
from backend.sources.base import RawJob
from backend.util import strip_html

BOARD_URL = "https://api.lever.co/v0/postings/{company}?mode=json"


def fetch() -> list[RawJob]:
    jobs: list[RawJob] = []
    with httpx.Client(timeout=15) as client:
        for company in LEVER_COMPANIES:
            try:
                resp = client.get(BOARD_URL.format(company=company))
                if resp.status_code != 200:
                    continue
                postings = resp.json()
            except httpx.HTTPError:
                continue

            for posting in postings:
                categories = posting.get("categories") or {}
                description_full = strip_html(posting.get("descriptionPlain") or "")
                jobs.append(
                    RawJob(
                        source="lever",
                        external_id=f"{company}:{posting['id']}",
                        title=posting.get("text", ""),
                        company=company.replace("-", " ").title(),
                        location_text=categories.get("location", ""),
                        url=posting.get("hostedUrl", ""),
                        posted_at=str(posting.get("createdAt", "")),
                        description_snippet=description_full[:300],
                        description_full=description_full,
                    )
                )
    return jobs
