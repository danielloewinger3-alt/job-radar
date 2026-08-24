import httpx

from backend.sources.base import RawJob

API_URL = "https://remoteok.com/api"
# RemoteOK blocks requests without a browser-like User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-search-tool/1.0)"}


def fetch() -> list[RawJob]:
    jobs: list[RawJob] = []
    try:
        with httpx.Client(timeout=15, headers=HEADERS) as client:
            resp = client.get(API_URL)
            if resp.status_code != 200:
                return jobs
            data = resp.json()
    except httpx.HTTPError:
        return jobs

    for posting in data:
        if "id" not in posting or "position" not in posting:
            continue  # first element is a legal notice, not a job
        jobs.append(
            RawJob(
                source="remoteok",
                external_id=str(posting["id"]),
                title=posting.get("position", ""),
                company=posting.get("company", ""),
                location_text=posting.get("location", "") or "Remote",
                url=posting.get("url", ""),
                remote=True,
                posted_at=posting.get("date"),
                description_snippet=(posting.get("description") or "")[:300],
            )
        )
    return jobs
