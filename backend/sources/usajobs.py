import httpx

from backend.config import USAJOBS_API_KEY, USAJOBS_USER_AGENT
from backend.config import TARGET_CITIES
from backend.sources.base import RawJob

SEARCH_URL = "https://data.usajobs.gov/api/search"
KEYWORDS = "software engineer"


def fetch() -> list[RawJob]:
    if not USAJOBS_API_KEY or not USAJOBS_USER_AGENT:
        return []

    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": USAJOBS_USER_AGENT,
        "Authorization-Key": USAJOBS_API_KEY,
    }
    us_cities = [c["label"] for c in TARGET_CITIES.values() if c["country"] == "US"]

    jobs: list[RawJob] = []
    with httpx.Client(timeout=15, headers=headers) as client:
        for city in us_cities:
            try:
                resp = client.get(
                    SEARCH_URL,
                    params={"Keyword": KEYWORDS, "LocationName": city, "ResultsPerPage": 50},
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except httpx.HTTPError:
                continue

            items = data.get("SearchResult", {}).get("SearchResultItems", [])
            for item in items:
                d = item.get("MatchedObjectDescriptor", {})
                locations = d.get("PositionLocation") or []
                location_text = locations[0].get("LocationName", "") if locations else city
                jobs.append(
                    RawJob(
                        source="usajobs",
                        external_id=str(d.get("PositionID", item.get("MatchedObjectId", ""))),
                        title=d.get("PositionTitle", ""),
                        company=d.get("OrganizationName", ""),
                        location_text=location_text,
                        url=d.get("PositionURI", ""),
                        posted_at=d.get("PublicationStartDate"),
                        description_snippet=(d.get("UserArea", {}).get("Details", {}).get("JobSummary") or "")[:300],
                    )
                )
    return jobs
