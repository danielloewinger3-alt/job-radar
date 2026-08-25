import httpx

from backend.config import GREENHOUSE_COMPANIES
from backend.sources.base import RawJob
from backend.util import strip_html

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"


def fetch() -> list[RawJob]:
    jobs: list[RawJob] = []
    with httpx.Client(timeout=15) as client:
        for company in GREENHOUSE_COMPANIES:
            try:
                resp = client.get(BOARD_URL.format(company=company), params={"content": "true"})
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except httpx.HTTPError:
                continue

            for posting in data.get("jobs", []):
                description_full = strip_html(posting.get("content", ""))
                jobs.append(
                    RawJob(
                        source="greenhouse",
                        external_id=f"{company}:{posting['id']}",
                        title=posting.get("title", ""),
                        company=company.replace("-", " ").title(),
                        location_text=(posting.get("location") or {}).get("name", ""),
                        url=posting.get("absolute_url", ""),
                        posted_at=posting.get("updated_at"),
                        description_snippet=description_full[:300],
                        description_full=description_full,
                    )
                )
    return jobs
