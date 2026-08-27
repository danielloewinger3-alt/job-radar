"""API paths for news, prospects and github - happy paths + 404 / 400 / error."""

import httpx


# --------------------------------------------------------------------------- #
# News
# --------------------------------------------------------------------------- #
def test_news_categories(client):
    r = client.get("/api/news/categories")
    assert r.status_code == 200
    keys = {c["key"] for c in r.json()}
    assert {"world", "tech", "business"} <= keys


def test_news_unknown_category_is_404(client):
    r = client.get("/api/news", params={"category": "sports"})
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown category"


def test_news_category_happy(client, monkeypatch):
    import backend.main as backend_main

    monkeypatch.setattr(
        backend_main.news_module,
        "fetch_category",
        lambda key: [{"category": key, "title": "T", "link": "L", "source": "S",
                      "published_at": None, "summary": ""}],
    )
    r = client.get("/api/news", params={"category": "tech"})
    assert r.status_code == 200
    assert r.json()["articles"][0]["category"] == "tech"


# --------------------------------------------------------------------------- #
# Prospects
# --------------------------------------------------------------------------- #
def test_prospect_areas(client):
    r = client.get("/api/prospects/areas")
    assert r.status_code == 200
    body = r.json()
    assert any(a["key"] == "bristol" for a in body["areas"])
    assert body["areas"][0]["total_businesses"] == 0
    assert "sectors" in body and "categories" in body


def test_list_businesses_unknown_area_is_404(client):
    r = client.get("/api/prospects/nowhere/businesses")
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown area"


def test_scan_unknown_area_is_404(client):
    r = client.post("/api/prospects/nowhere/scan", json={"categories": ["dentist"]})
    assert r.status_code == 404


def test_scan_unknown_category_is_400(client):
    r = client.post("/api/prospects/bristol/scan", json={"categories": ["unicorn_wrangler"]})
    assert r.status_code == 400
    assert "unknown categories" in r.json()["detail"]


def test_scan_happy_path_with_stubbed_discover(client, monkeypatch):
    import backend.main as backend_main

    monkeypatch.setattr(
        backend_main.prospect_scan, "discover",
        lambda area_key, categories: {c: 0 for c in categories},
    )
    r = client.post("/api/prospects/bristol/scan", json={"categories": ["dentist", "gym"]})
    assert r.status_code == 200
    assert r.json() == {"new_businesses": {"dentist": 0, "gym": 0}, "total_new": 0}


def test_analyze_unknown_area_is_404(client):
    r = client.post("/api/prospects/nowhere/analyze", json={"limit": 5})
    assert r.status_code == 404


def test_analyze_without_anthropic_key_is_400(client, monkeypatch):
    import backend.main as backend_main

    monkeypatch.setattr(backend_main, "ANTHROPIC_API_KEY", "")
    r = client.post("/api/prospects/bristol/analyze", json={"limit": 5})
    assert r.status_code == 400
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
def test_github_repos_unconfigured(client):
    # conftest forces GITHUB_TOKEN / GITHUB_USERNAME empty before import.
    r = client.get("/api/github/repos")
    assert r.status_code == 200
    assert r.json() == {"configured": False, "repos": []}


def test_github_repos_configured_maps_response(client, monkeypatch):
    import backend.main as backend_main
    from _helpers import FakeResponse

    monkeypatch.setattr(backend_main, "GITHUB_USERNAME", "octocat")
    monkeypatch.setattr(backend_main, "GITHUB_TOKEN", "")
    payload = [
        {"name": "repo-a", "description": "A", "html_url": "http://a", "private": False,
         "language": "Python", "stargazers_count": 3, "pushed_at": "2026-08-01T00:00:00Z"},
        {"not_a_repo": True},
    ]
    monkeypatch.setattr(
        backend_main.httpx, "get", lambda *a, **k: FakeResponse(json_data=payload)
    )
    r = client.get("/api/github/repos")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert [x["name"] for x in body["repos"]] == ["repo-a"]
    assert body["repos"][0]["stars"] == 3


def test_github_repos_network_error_is_reported(client, monkeypatch):
    import backend.main as backend_main

    monkeypatch.setattr(backend_main, "GITHUB_USERNAME", "octocat")

    def raise_err(*a, **k):
        raise httpx.HTTPError("no net")

    monkeypatch.setattr(backend_main.httpx, "get", raise_err)
    r = client.get("/api/github/repos")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["repos"] == []
    assert "error" in body
