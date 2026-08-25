import httpx

from backend.config import REED_API_KEY
from backend.sources.base import RawJob
from backend.util import strip_html

SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
QUERIES = ["graduate software engineer", "junior software developer"]


def fetch() -> list[RawJob]:
    if not REED_API_KEY:
        return []

    jobs: list[RawJob] = []
    with httpx.Client(timeout=15, auth=(REED_API_KEY, "")) as client:
        for keywords in QUERIES:
            try:
                resp = client.get(SEARCH_URL, params={"keywords": keywords, "resultsToTake": 50})
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except httpx.HTTPError:
                continue

            for posting in data.get("results", []):
                description_full = strip_html(posting.get("jobDescription") or "")
                jobs.append(
                    RawJob(
                        source="reed",
                        external_id=str(posting.get("jobId")),
                        title=posting.get("jobTitle", ""),
                        company=posting.get("employerName", ""),
                        location_text=posting.get("locationName", ""),
                        url=posting.get("jobUrl", ""),
                        posted_at=posting.get("date"),
                        description_snippet=description_full[:300],
                        description_full=description_full,
                    )
                )
    return jobs
