import httpx

from backend.config import ADZUNA_APP_ID, ADZUNA_APP_KEY
from backend.sources.base import RawJob

SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"

# Adzuna country codes covering our target cities. Tel Aviv/Budapest/Dublin/Zurich
# aren't Adzuna markets, so those rely on the other sources.
COUNTRIES = ["gb", "us", "de", "nl", "fr", "pl"]
WHAT = "software engineer graduate"


def fetch() -> list[RawJob]:
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        return []

    jobs: list[RawJob] = []
    with httpx.Client(timeout=15) as client:
        for country in COUNTRIES:
            try:
                resp = client.get(
                    SEARCH_URL.format(country=country),
                    params={
                        "app_id": ADZUNA_APP_ID,
                        "app_key": ADZUNA_APP_KEY,
                        "what": WHAT,
                        "results_per_page": 50,
                        "max_days_old": 7,
                        "content-type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except httpx.HTTPError:
                continue

            for posting in data.get("results", []):
                location = posting.get("location", {}).get("display_name", "")
                jobs.append(
                    RawJob(
                        source="adzuna",
                        external_id=str(posting.get("id")),
                        title=posting.get("title", ""),
                        company=(posting.get("company") or {}).get("display_name", ""),
                        location_text=location,
                        url=posting.get("redirect_url", ""),
                        posted_at=posting.get("created"),
                        description_snippet=(posting.get("description") or "")[:300],
                    )
                )
    return jobs
