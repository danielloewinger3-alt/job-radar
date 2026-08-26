import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser
import httpx

from backend.config import NEWS_CATEGORIES
from backend.util import strip_html

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-radar-news/1.0)"}


def _fetch_feed(category_key: str, name: str, url: str) -> list[dict]:
    try:
        resp = httpx.get(url, timeout=12, follow_redirects=True, headers=HEADERS)
        if resp.status_code != 200:
            return []
        parsed = feedparser.parse(resp.content)
    except httpx.HTTPError:
        return []

    articles = []
    for entry in parsed.entries[:20]:
        title = (entry.get("title") or "").strip()
        link = entry.get("link") or ""
        if not title or not link:
            continue

        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        published_at = datetime.fromtimestamp(time.mktime(struct), tz=timezone.utc).isoformat() if struct else None

        summary = strip_html(entry.get("summary") or entry.get("description") or "")[:280]
        articles.append({
            "category": category_key,
            "title": title,
            "link": link,
            "source": name,
            "published_at": published_at,
            "summary": summary,
        })
    return articles


def _fetch_many(feed_jobs: list[tuple[str, str, str]]) -> list[dict]:
    """feed_jobs: list of (category_key, feed_name, url). Fetches all concurrently —
    RSS feeds are independent I/O, and doing 10+ of them sequentially would make the
    endpoint painfully slow."""
    seen_links = set()
    articles = []
    with ThreadPoolExecutor(max_workers=max(1, len(feed_jobs))) as executor:
        futures = [executor.submit(_fetch_feed, *job) for job in feed_jobs]
        for future in as_completed(futures):
            for article in future.result():
                if article["link"] in seen_links:
                    continue
                seen_links.add(article["link"])
                articles.append(article)
    articles.sort(key=lambda a: a["published_at"] or "", reverse=True)
    return articles


def fetch_category(category_key: str) -> list[dict]:
    category = NEWS_CATEGORIES.get(category_key)
    if not category:
        return []
    jobs = [(category_key, feed["name"], feed["url"]) for feed in category["feeds"]]
    return _fetch_many(jobs)


def fetch_all() -> list[dict]:
    jobs = [
        (category_key, feed["name"], feed["url"])
        for category_key, category in NEWS_CATEGORIES.items()
        for feed in category["feeds"]
    ]
    return _fetch_many(jobs)
