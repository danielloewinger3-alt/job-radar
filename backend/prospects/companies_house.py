import httpx

from backend.config import COMPANIES_HOUSE_API_KEY

SEARCH_URL = "https://api.company-information.service.gov.uk/search/companies"
PROFILE_URL = "https://api.company-information.service.gov.uk/company/{number}"


def lookup(name: str) -> dict | None:
    """Best-effort match against Companies House by name, with a follow-up call for
    filing-health flags. Returns None if unconfigured, no match, or on any request
    failure — this is a bonus signal, not a required one. Sole traders and unincorporated
    businesses will never match here, which is expected, not an error."""
    if not COMPANIES_HOUSE_API_KEY:
        return None

    try:
        resp = httpx.get(
            SEARCH_URL,
            params={"q": name, "items_per_page": 3},
            auth=(COMPANIES_HOUSE_API_KEY, ""),
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("items", [])
    except httpx.HTTPError:
        return None

    name_lower = name.strip().lower()
    match = next((i for i in items if name_lower in (i.get("title") or "").lower() or (i.get("title") or "").lower() in name_lower), None)
    if match is None:
        return None

    number = match.get("company_number", "")
    status = match.get("company_status", "")

    overdue = []
    try:
        profile_resp = httpx.get(PROFILE_URL.format(number=number), auth=(COMPANIES_HOUSE_API_KEY, ""), timeout=15)
        if profile_resp.status_code == 200:
            profile = profile_resp.json()
            if (profile.get("accounts") or {}).get("overdue"):
                overdue.append("accounts overdue")
            if (profile.get("confirmation_statement") or {}).get("overdue"):
                overdue.append("confirmation statement overdue")
    except httpx.HTTPError:
        pass

    return {"number": number, "status": status + (" — " + ", ".join(overdue) if overdue else "")}
