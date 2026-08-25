import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass rejects requests with no (or a generic) User-Agent with a 406.
HEADERS = {"User-Agent": "job-radar-prospects/1.0 (local personal-use scan)"}


def _build_query(tag_pairs: list[tuple[str, str]], lat: float, lon: float, radius_m: int) -> str:
    clauses = []
    for key, value in tag_pairs:
        clauses.append(f'node["{key}"="{value}"](around:{radius_m},{lat},{lon});')
        clauses.append(f'way["{key}"="{value}"](around:{radius_m},{lat},{lon});')
    body = "\n  ".join(clauses)
    return f"[out:json][timeout:40];\n(\n  {body}\n);\nout center tags;"


def fetch_category(tag_pairs: list[tuple[str, str]], lat: float, lon: float, radius_km: float) -> list[dict]:
    """Query OpenStreetMap (via Overpass) for businesses matching the given tags near a point.
    Returns [] on any failure — Overpass is a free community service and does occasionally
    time out or rate-limit; a scan should skip a category rather than crash the whole run."""
    query = _build_query(tag_pairs, lat, lon, int(radius_km * 1000))
    try:
        resp = httpx.post(OVERPASS_URL, data={"data": query}, timeout=60, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError:
        return []

    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue  # unnamed POIs aren't useful prospects

        if el.get("type") == "node":
            el_lat, el_lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            el_lat, el_lon = center.get("lat"), center.get("lon")
        if el_lat is None or el_lon is None:
            continue

        address = " ".join(filter(None, [
            tags.get("addr:housenumber"), tags.get("addr:street"),
            tags.get("addr:city"), tags.get("addr:postcode"),
        ]))
        results.append({
            "osm_type": el["type"],
            "osm_id": el["id"],
            "name": name,
            "lat": el_lat,
            "lon": el_lon,
            "address": address,
            "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "website": tags.get("website") or tags.get("contact:website") or "",
        })
    return results
