"""robots.txt handling.

Decision values:

* ``"allow"``      -- crawling this URL is permitted
* ``"disallow"``   -- this path is disallowed (skip the URL, not the site)
* ``"skip_site"``  -- do not crawl this site at all

A 404 for ``/robots.txt`` means no rules were published and crawling may
proceed. Any other failure -- non-404 HTTP status, timeout, oversize/truncated
body, unsafe/off-host redirect, or a body that does not parse as robots rules
(e.g. an HTML error page) -- is treated as ``skip_site``, never as allow-all.
"""

from __future__ import annotations

from urllib import robotparser
from urllib.parse import urlsplit

from backend.outreach import net

_HTML_SNIFF = ("<html", "<!doctype", "<head", "<body")


class RobotsCache:
    """Per-crawl cache of robots decisions, keyed by scheme://netloc."""

    def __init__(self) -> None:
        self._by_origin: dict[str, object] = {}

    def decision(self, url: str) -> str:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        entry = self._by_origin.get(origin)
        if entry is None:
            entry = self._load(origin)
            self._by_origin[origin] = entry

        if entry == "skip_site":
            return "skip_site"
        if entry == "allow_all":
            return "allow"

        assert isinstance(entry, robotparser.RobotFileParser)
        return "allow" if entry.can_fetch(net.PUBLIC_UA, url) else "disallow"

    def _load(self, origin: str) -> object:
        robots_url = origin + "/robots.txt"
        try:
            res = net.fetch(robots_url, kind="robots", max_bytes=net.MAX_ROBOTS_BYTES)
        except net.FetchError as exc:
            return "allow_all" if exc.code == "http_404" else "skip_site"
        except net.UnsafeUrlError:
            return "skip_site"

        if res.truncated:
            return "skip_site"

        head = res.text[:2048].lower()
        if any(marker in head for marker in _HTML_SNIFF):
            return "skip_site"  # HTML error page served as robots.txt -> unsafe to interpret

        try:
            parser = robotparser.RobotFileParser()
            parser.parse(res.text.splitlines())
        except Exception:  # noqa: BLE001 -- any parse failure is skip_site
            return "skip_site"
        return parser
