from backend.config import ALL_CITIES, INTERNSHIP_EXCLUDE, REMOTE_KEY, ROLE_KEYWORDS, SENIORITY_EXCLUDE


def match_city(location_text: str) -> str | None:
    text = (location_text or "").lower()
    for key, city in ALL_CITIES.items():
        if any(alias in text for alias in city["aliases"]):
            return key
    return None


def is_remote(location_text: str, remote_flag: bool = False) -> bool:
    if remote_flag:
        return True
    return "remote" in (location_text or "").lower()


def matches_role(title: str) -> bool:
    text = (title or "").lower()
    return any(kw in text for kw in ROLE_KEYWORDS)


def is_senior(title: str) -> bool:
    text = (title or "").lower()
    return any(kw in text for kw in SENIORITY_EXCLUDE)


def is_internship(title: str) -> bool:
    text = (title or "").lower()
    return any(kw in text for kw in INTERNSHIP_EXCLUDE)


def passes_filters(title: str, location_text: str, remote_flag: bool = False) -> bool:
    if not matches_role(title):
        return False
    if is_senior(title) or is_internship(title):
        return False
    city = match_city(location_text)
    remote = is_remote(location_text, remote_flag)
    return city is not None or remote
