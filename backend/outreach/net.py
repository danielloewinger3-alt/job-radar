"""SSRF-aware HTTP fetch layer for the outreach crawler.

Guarantees (see ``KNOWN_LIMITATIONS`` for what is NOT guaranteed):

* only ``http`` / ``https``; URL credentials rejected
* ``localhost`` / ``*.local`` / ``*.localhost`` rejected before DNS
* every literal or resolved address must be globally routable
  (``ipaddress.*.is_global``), with IPv4-mapped IPv6 unwrapped first and the
  cloud metadata address (169.254.169.254) rejected explicitly
* redirects are followed manually, at most ``MAX_REDIRECTS``, and every hop is
  re-validated; a non-http(s) redirect target is refused
* response bodies are streamed and hard-capped; nothing unbounded is buffered
* ``trust_env=False`` -- ``HTTP(S)_PROXY`` / ``NO_PROXY`` cannot redirect traffic
* one request per origin per ``crawl_delay()`` seconds (robots + pages alike)
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from backend import config

REQUEST_TIMEOUT = 10.0
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 1_048_576  # 1 MiB
MAX_ROBOTS_BYTES = 262_144  # 256 KiB
ALLOWED_SCHEMES = ("http", "https")
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
PUBLIC_UA = "JobRadarOutreachBot/1.0 (+respects robots.txt; contact via the site)"

METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

KNOWN_LIMITATIONS = (
    "DNS rebinding is a residual risk: a hostname is resolved and every "
    "returned address is validated, but the subsequent HTTP connection is not "
    "pinned to a validated address, so a hostile resolver could answer with a "
    "public address for validation and a private one for the connection. This "
    "layer reduces SSRF exposure; it does not make SSRF impossible."
)


class UnsafeUrlError(Exception):
    """Raised when a URL / redirect target fails a safety check."""


class FetchError(Exception):
    """Raised for a transport/HTTP/shape failure. ``code`` is a short,
    sanitized token safe to persist and surface (never a raw exception)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class FetchResult:
    final_url: str
    status: int
    content_type: str
    text: str
    truncated: bool


# --------------------------------------------------------------------------- #
# Pacing -- one request per origin per crawl_delay() seconds. Monkeypatch
# ``_sleep`` in tests so no real time passes.
# --------------------------------------------------------------------------- #
_last_request_at: dict[str, float] = {}


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def crawl_delay() -> float:
    return max(1.0, float(config.OUTREACH_CRAWL_DELAY_SECONDS))


def reset_pacing() -> None:
    _last_request_at.clear()


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def pace(origin: str) -> None:
    """Block until at least ``crawl_delay()`` seconds have elapsed since the last
    request to ``origin``."""
    prev = _last_request_at.get(origin)
    now = time.monotonic()
    if prev is not None:
        wait = crawl_delay() - (now - prev)
        if wait > 0:
            _sleep(wait)
    _last_request_at[origin] = time.monotonic()


# --------------------------------------------------------------------------- #
# Host / address validation
# --------------------------------------------------------------------------- #
def canonical_url(url: str) -> str:
    """Give a URL an explicit ``/`` path when it has none, matching what an HTTP
    client sends on the wire (and keeping ``final_url`` stable)."""
    sp = urlsplit(url)
    if not sp.path:
        return urlunsplit((sp.scheme, sp.netloc, "/", sp.query, sp.fragment))
    return url


def norm_host(host: str | None) -> str:
    """Lowercase and strip exactly one leading ``www.`` -- the only variation
    treated as "the same site"."""
    h = (host or "").strip().lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def resolve_host(host: str) -> list[str]:
    """Return every address ``host`` resolves to. Monkeypatched in tests."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def _addr_is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if str(ip) in METADATA_IPS:
        return False
    return bool(ip.is_global)


def assert_public_url(url: str) -> None:
    """Raise :class:`UnsafeUrlError` unless ``url`` is a plain http(s) URL whose
    host resolves only to globally-routable addresses."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"bad_scheme:{scheme or 'none'}")
    if parts.username or parts.password:
        raise UnsafeUrlError("url_credentials")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeUrlError("no_host")
    if host in METADATA_IPS:
        raise UnsafeUrlError("metadata_ip")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeUrlError("local_hostname")

    literal = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass

    if literal is not None:
        if not _addr_is_public(host):
            raise UnsafeUrlError("non_global_ip")
        return

    try:
        addrs = resolve_host(host)
    except OSError as exc:  # DNS failure
        raise UnsafeUrlError("dns_error") from exc
    if not addrs:
        raise UnsafeUrlError("dns_no_result")
    for addr in addrs:
        if not _addr_is_public(addr):
            raise UnsafeUrlError("non_global_resolved_ip")


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #
def _client_kwargs() -> dict:
    return {
        "trust_env": False,  # ignore HTTP(S)_PROXY / NO_PROXY entirely
        "timeout": REQUEST_TIMEOUT,
        "follow_redirects": False,  # we follow + re-validate manually
        "headers": {"User-Agent": PUBLIC_UA},
    }


def _default_client() -> httpx.Client:
    return httpx.Client(**_client_kwargs())


def _content_type_ok(ctype: str, kind: str) -> bool:
    if kind == "html":
        return any(ctype == c for c in HTML_CONTENT_TYPES)
    # robots: any text/* is acceptable at this layer; HTML-as-robots is caught
    # by the parser in robots.py.
    return ctype.startswith("text/")


def fetch(
    url: str,
    *,
    kind: str,
    max_bytes: int,
    require_same_host: bool = False,
    start_host: str | None = None,
) -> FetchResult:
    """Fetch ``url`` with full SSRF validation on every hop.

    ``kind`` is ``"html"`` or ``"robots"`` (drives the content-type gate).
    Raises :class:`UnsafeUrlError` or :class:`FetchError`.
    """
    if kind not in ("html", "robots"):
        raise ValueError(f"bad kind: {kind!r}")

    current = canonical_url(url)
    anchor = norm_host(start_host if start_host is not None else urlsplit(current).hostname)
    client = _default_client()
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            current = canonical_url(current)
            assert_public_url(current)
            if require_same_host and norm_host(urlsplit(current).hostname) != anchor:
                raise FetchError(f"offhost_redirect:{norm_host(urlsplit(current).hostname)}")

            pace(_origin(current))
            try:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location", "")
                        if not loc:
                            raise FetchError("bad_redirect")
                        nxt = urljoin(current, loc)
                        if urlsplit(nxt).scheme.lower() not in ALLOWED_SCHEMES:
                            raise FetchError("bad_redirect_scheme")
                        current = nxt
                        continue

                    status = resp.status_code
                    if status >= 300:
                        raise FetchError(f"http_{status}")

                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not _content_type_ok(ctype, kind):
                        raise FetchError("non_html_content" if kind == "html" else "robots_unparseable")

                    buf = bytearray()
                    truncated = False
                    for chunk in resp.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) >= max_bytes:
                            del buf[max_bytes:]
                            truncated = True
                            break
                    return FetchResult(
                        final_url=current,
                        status=status,
                        content_type=ctype,
                        text=bytes(buf).decode("utf-8", errors="replace"),
                        truncated=truncated,
                    )
            except httpx.TimeoutException as exc:
                raise FetchError("timeout") from exc
            except httpx.ConnectError as exc:
                raise FetchError("connection_error") from exc
            except httpx.HTTPError as exc:  # any other transport failure
                raise FetchError("transport_error") from exc

        raise FetchError("too_many_redirects")
    finally:
        client.close()
