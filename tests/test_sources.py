"""Source-adapter transformation tests.

Every adapter is exercised against saved local fixtures with httpx fully stubbed -
no live request is made (the _no_network guard would raise on one anyway).
"""

import httpx
import pytest

from backend.sources import adzuna, greenhouse, lever, reed, remoteok, usajobs
from backend.prospects import osm


def install_client(monkeypatch, module, handler):
    """Replace `module.httpx.Client` with a fake whose get/post call `handler`."""
    from _helpers import FakeHTTPClient

    monkeypatch.setattr(
        module.httpx, "Client", lambda **kw: FakeHTTPClient(handler, **kw)
    )


# --------------------------------------------------------------------------- #
# Greenhouse
# --------------------------------------------------------------------------- #
def test_greenhouse_maps_fields(monkeypatch, load_fixture):
    from _helpers import FakeResponse

    payload = load_fixture("greenhouse_jobs.json")

    def handler(method, url, kwargs):
        if "/example/" in url or "stripe" in url:
            return FakeResponse(json_data=payload)
        return FakeResponse(json_data={"jobs": []})

    # Point the first configured company at our fixture, silence the rest.
    monkeypatch.setattr(greenhouse, "GREENHOUSE_COMPANIES", ["example"])
    install_client(monkeypatch, greenhouse, handler)

    jobs = greenhouse.fetch()

    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "greenhouse"
    assert j.external_id == "example:123"
    assert j.company == "Example"
    assert j.title == "Software Engineer, New Grad"
    assert j.location_text == "London, UK"
    assert j.url == "https://boards.greenhouse.io/example/jobs/123"
    assert j.posted_at == "2026-08-01T12:00:00-04:00"
    # strip_html applied: raw <script> removed tag + contents
    assert j.description_full == "We are hiring a Software Engineer."
    assert "track()" not in j.description_full
    assert j.description_snippet == j.description_full[:300]


def test_greenhouse_non_200_yields_no_jobs(monkeypatch):
    from _helpers import FakeResponse

    monkeypatch.setattr(greenhouse, "GREENHOUSE_COMPANIES", ["example"])
    install_client(
        monkeypatch, greenhouse, lambda m, u, k: FakeResponse(status_code=503)
    )
    assert greenhouse.fetch() == []


def test_greenhouse_http_error_is_swallowed(monkeypatch):
    def handler(method, url, kwargs):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(greenhouse, "GREENHOUSE_COMPANIES", ["example"])
    install_client(monkeypatch, greenhouse, handler)
    assert greenhouse.fetch() == []


# --------------------------------------------------------------------------- #
# Lever
# --------------------------------------------------------------------------- #
def test_lever_maps_fields(monkeypatch, load_fixture):
    from _helpers import FakeResponse

    payload = load_fixture("lever_postings.json")
    monkeypatch.setattr(lever, "LEVER_COMPANIES", ["example"])
    install_client(
        monkeypatch, lever, lambda m, u, k: FakeResponse(json_data=payload)
    )

    jobs = lever.fetch()

    assert [j.external_id for j in jobs] == ["example:abc-123", "example:def-456"]
    j = jobs[0]
    assert j.source == "lever"
    assert j.company == "Example"
    assert j.title == "Junior Software Engineer"
    assert j.location_text == "Tel Aviv"
    assert j.url == "https://jobs.lever.co/example/abc-123"
    assert j.posted_at == "1690000000000"
    assert j.description_full.startswith("Plain-text description from Lever.")


def test_lever_http_error_is_swallowed(monkeypatch):
    def handler(method, url, kwargs):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(lever, "LEVER_COMPANIES", ["example"])
    install_client(monkeypatch, lever, handler)
    assert lever.fetch() == []


# --------------------------------------------------------------------------- #
# RemoteOK
# --------------------------------------------------------------------------- #
def test_remoteok_maps_fields_and_skips_legal_notice(monkeypatch, load_fixture):
    from _helpers import FakeResponse

    payload = load_fixture("remoteok_api.json")
    install_client(
        monkeypatch, remoteok, lambda m, u, k: FakeResponse(json_data=payload)
    )

    jobs = remoteok.fetch()

    # 3 elements in fixture, first is the legal notice (no id/position) -> 2 jobs
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "remoteok"
    assert j.external_id == "1009"
    assert j.title == "Backend Engineer"
    assert j.company == "RemoteCo"
    assert j.remote is True
    assert j.location_text == "Remote"          # empty string -> "Remote"
    assert j.description_full == "Fully remote backend role."


def test_remoteok_non_200_yields_no_jobs(monkeypatch):
    from _helpers import FakeResponse

    install_client(
        monkeypatch, remoteok, lambda m, u, k: FakeResponse(status_code=429)
    )
    assert remoteok.fetch() == []


# --------------------------------------------------------------------------- #
# Adzuna (key-gated)
# --------------------------------------------------------------------------- #
def test_adzuna_returns_empty_without_keys(monkeypatch):
    monkeypatch.setattr(adzuna, "ADZUNA_APP_ID", "")
    monkeypatch.setattr(adzuna, "ADZUNA_APP_KEY", "")
    # No client installed: if fetch tried to call out, _no_network would raise.
    assert adzuna.fetch() == []


def test_adzuna_maps_fields_with_keys(monkeypatch, load_fixture):
    from _helpers import FakeResponse

    payload = load_fixture("adzuna_search.json")
    monkeypatch.setattr(adzuna, "ADZUNA_APP_ID", "id")
    monkeypatch.setattr(adzuna, "ADZUNA_APP_KEY", "key")

    def handler(method, url, kwargs):
        if "/gb/" in url:
            return FakeResponse(json_data=payload)
        return FakeResponse(json_data={"results": []})

    install_client(monkeypatch, adzuna, handler)

    jobs = adzuna.fetch()
    assert len(jobs) == 1
    j = jobs[0]
    assert j.source == "adzuna"
    assert j.external_id == "88001"
    assert j.title == "Graduate Software Engineer"
    assert j.company == "BigCo Ltd"
    assert j.location_text == "Manchester, North West England"
    assert j.url == "https://www.adzuna.co.uk/jobs/details/88001"
    assert j.description_full == "Great grad role for a software engineer."


# --------------------------------------------------------------------------- #
# Reed (key-gated)
# --------------------------------------------------------------------------- #
def test_reed_returns_empty_without_key(monkeypatch):
    monkeypatch.setattr(reed, "REED_API_KEY", "")
    assert reed.fetch() == []


def test_reed_maps_fields_with_key(monkeypatch, load_fixture):
    from _helpers import FakeResponse

    payload = load_fixture("reed_search.json")
    monkeypatch.setattr(reed, "REED_API_KEY", "key")
    install_client(
        monkeypatch, reed, lambda m, u, k: FakeResponse(json_data=payload)
    )

    jobs = reed.fetch()
    assert all(j.source == "reed" for j in jobs)
    j = jobs[0]
    assert j.external_id == "77001"
    assert j.title == "Junior Software Developer"
    assert j.company == "ReedCo"
    assert j.location_text == "Bristol"
    assert j.url == "https://www.reed.co.uk/jobs/77001"
    assert j.description_full == "Junior developer wanted."


# --------------------------------------------------------------------------- #
# USAJOBS (key-gated)
# --------------------------------------------------------------------------- #
def test_usajobs_returns_empty_without_key(monkeypatch):
    monkeypatch.setattr(usajobs, "USAJOBS_API_KEY", "")
    monkeypatch.setattr(usajobs, "USAJOBS_USER_AGENT", "")
    assert usajobs.fetch() == []


def test_usajobs_maps_fields_with_key(monkeypatch, load_fixture):
    from _helpers import FakeResponse

    payload = load_fixture("usajobs_search.json")
    monkeypatch.setattr(usajobs, "USAJOBS_API_KEY", "key")
    monkeypatch.setattr(usajobs, "USAJOBS_USER_AGENT", "agent@example.com")
    install_client(
        monkeypatch, usajobs, lambda m, u, k: FakeResponse(json_data=payload)
    )

    jobs = usajobs.fetch()
    assert jobs, "expected at least one job"
    assert all(j.source == "usajobs" for j in jobs)
    j = jobs[0]
    assert j.external_id == "IT-2026-999001"
    assert j.title == "IT Specialist (Software Engineer)"
    assert j.company == "General Services Administration"
    assert j.location_text == "New York, New York"
    assert j.url == "https://www.usajobs.gov/job/999001"
    assert j.description_full == "Build and maintain federal software systems."


# --------------------------------------------------------------------------- #
# Prospects: OpenStreetMap / Overpass
# --------------------------------------------------------------------------- #
def test_osm_fetch_category_maps_nodes_and_ways(monkeypatch, load_fixture):
    from _helpers import FakeResponse

    payload = load_fixture("overpass_response.json")
    monkeypatch.setattr(
        osm.httpx, "post", lambda *a, **k: FakeResponse(json_data=payload)
    )

    results = osm.fetch_category([("amenity", "dentist")], 51.45, -2.58, 8)

    # 4 elements: unnamed node skipped, no-coords node skipped -> 2 results
    assert len(results) == 2
    node = results[0]
    assert node["osm_type"] == "node"
    assert node["osm_id"] == 111
    assert node["name"] == "Bright Smiles Dental"
    assert node["lat"] == 51.4545 and node["lon"] == -2.5879
    assert node["address"] == "12 Park Row Bristol BS1 5LJ"
    assert node["phone"] == "+44 117 000 0000"
    assert node["website"] == "https://brightsmiles.example"

    way = results[1]
    assert way["osm_type"] == "way"
    assert way["lat"] == 51.46 and way["lon"] == -2.59       # from `center`
    assert way["phone"] == "+44 117 111 1111"                # contact:phone fallback
    assert way["website"] == "https://centralphysio.example"  # contact:website fallback


def test_osm_fetch_category_http_error_returns_empty(monkeypatch):
    def raise_err(*a, **k):
        raise httpx.HTTPError("overpass down")

    monkeypatch.setattr(osm.httpx, "post", raise_err)
    assert osm.fetch_category([("amenity", "dentist")], 51.45, -2.58, 8) == []
