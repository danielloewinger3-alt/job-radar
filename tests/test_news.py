"""backend.news adapter tests - RSS parsing with httpx stubbed."""

import httpx

from backend import news


def _stub_get(monkeypatch, load_fixture, *, status_code=200):
    xml = load_fixture("rss_sample.xml").encode("utf-8")
    from _helpers import FakeResponse

    monkeypatch.setattr(
        news.httpx,
        "get",
        lambda *a, **k: FakeResponse(status_code=status_code, content=xml),
    )


def test_fetch_feed_maps_entries(monkeypatch, load_fixture):
    _stub_get(monkeypatch, load_fixture)

    articles = news._fetch_feed("tech", "Sample", "https://example.com/rss")

    # 3 items in fixture; the third has an empty <title> and is dropped.
    assert len(articles) == 2

    first = articles[0]
    assert first["category"] == "tech"
    assert first["source"] == "Sample"
    assert first["title"] == "First headline about technology"
    assert first["link"] == "https://example.com/articles/first"
    assert first["published_at"] == "2026-08-27T10:00:00+00:00"
    # summary: HTML stripped, <script> gone (feedparser sanitises), entities resolved
    assert "Summary one with" in first["summary"]
    assert "bad()" not in first["summary"]

    second = articles[1]
    assert second["title"] == "Second headline"
    assert second["published_at"] is None          # no pubDate/updated in fixture


def test_fetch_feed_non_200_returns_empty(monkeypatch, load_fixture):
    _stub_get(monkeypatch, load_fixture, status_code=500)
    assert news._fetch_feed("tech", "Sample", "https://example.com/rss") == []


def test_fetch_feed_http_error_returns_empty(monkeypatch):
    def raise_err(*a, **k):
        raise httpx.HTTPError("dns fail")

    monkeypatch.setattr(news.httpx, "get", raise_err)
    assert news._fetch_feed("tech", "Sample", "https://example.com/rss") == []


def test_fetch_category_unknown_returns_empty(monkeypatch, load_fixture):
    _stub_get(monkeypatch, load_fixture)
    assert news.fetch_category("does-not-exist") == []


def test_fetch_category_dedupes_links_across_feeds(monkeypatch, load_fixture):
    # tech has 4 feeds; all return the same fixture -> deduped by link.
    _stub_get(monkeypatch, load_fixture)
    articles = news.fetch_category("tech")
    links = [a["link"] for a in articles]
    assert links == sorted(set(links), key=links.index)   # no duplicates
    assert set(links) == {
        "https://example.com/articles/first",
        "https://example.com/articles/second",
    }
