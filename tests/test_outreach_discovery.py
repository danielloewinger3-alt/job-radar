"""Discovery + contact collection: SSRF, robots, crawl limits, email
extraction, discovery accuracy / retryability, and the discovery endpoints."""

import pytest
from sqlmodel import SQLModel, select

import backend.outreach.models  # noqa: F401
from backend.models import Business
from backend.outreach import discovery, net
from backend.outreach import migrate as outreach_migrate
from backend.outreach.models import ContactEvidence, OutreachContact
from backend.outreach.robots import RobotsCache


# --------------------------------------------------------------------------- #
# Fixtures (small, duplicated per owned test file -- see corrections note #5)
# --------------------------------------------------------------------------- #
@pytest.fixture
def outreach_db(isolated_db):
    SQLModel.metadata.create_all(isolated_db)
    outreach_migrate.run(isolated_db)
    return isolated_db


@pytest.fixture
def oclient(outreach_db, client):
    return client


@pytest.fixture(autouse=True)
def _fast_net(monkeypatch):
    """No real sleeps; public DNS by default; fresh pacing per test."""
    monkeypatch.setattr(net, "_sleep", lambda s: None)
    monkeypatch.setattr(net, "resolve_host", lambda host: ["93.184.216.34"])
    net.reset_pacing()
    yield
    net.reset_pacing()


# --------------------------------------------------------------------------- #
# Fake httpx client for net.fetch
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, *, status=200, headers=None, body=b"", is_redirect=False):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.is_redirect = is_redirect

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        step = 65536
        for i in range(0, len(self._body), step) or [0]:
            yield self._body[i : i + step]


class _FakeClient:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def stream(self, method, url):
        self.calls.append(url)
        return self._handler(url)

    def close(self):
        pass


def _install(monkeypatch, handler):
    client = _FakeClient(handler)
    monkeypatch.setattr(net, "_default_client", lambda: client)
    return client


def _html(body_html="<html><body>hi</body></html>"):
    return _FakeResp(
        status=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body=body_html.encode("utf-8"),
    )


def _pages_handler(page_html, *, robots_status=404, robots_body=b""):
    """Serve a real (empty/404) robots.txt and ``page_html`` for every page."""

    def handler(url):
        if url.endswith("/robots.txt"):
            return _FakeResp(status=robots_status, headers={"content-type": "text/plain"}, body=robots_body)
        return _html(page_html)

    return handler


# =========================================================================== #
# assert_public_url -- SSRF
# =========================================================================== #
@pytest.mark.parametrize("url", [
    "ftp://example.com/x",
    "file:///etc/passwd",
    "gopher://example.com",
    "data:text/plain,hi",
])
def test_reject_forbidden_schemes(url):
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url(url)


def test_reject_url_credentials():
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url("http://user:pass@example.com/")


@pytest.mark.parametrize("host", ["localhost", "foo.localhost", "printer.local"])
def test_reject_local_hostnames_before_dns(host, monkeypatch):
    monkeypatch.setattr(net, "resolve_host", lambda h: (_ for _ in ()).throw(AssertionError("DNS called")))
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url(f"http://{host}/")


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
    "169.254.1.1", "0.0.0.0", "100.64.0.1",
])
def test_reject_literal_private_ipv4(ip):
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url(f"http://{ip}/")


def test_reject_metadata_ip_explicitly():
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url("http://169.254.169.254/latest/meta-data/")


@pytest.mark.parametrize("ip", ["[::1]", "[fc00::1]", "[fe80::1]"])
def test_reject_ipv6_private(ip):
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url(f"http://{ip}/")


@pytest.mark.parametrize("ip", ["[::ffff:10.0.0.1]", "[::ffff:127.0.0.1]", "[::ffff:169.254.169.254]"])
def test_reject_ipv4_mapped_ipv6_private(ip):
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url(f"http://{ip}/")


def test_reject_hostname_resolving_to_private(monkeypatch):
    monkeypatch.setattr(net, "resolve_host", lambda h: ["10.1.2.3"])
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url("http://sneaky.example/")


def test_reject_when_any_resolved_address_is_private(monkeypatch):
    monkeypatch.setattr(net, "resolve_host", lambda h: ["93.184.216.34", "192.168.0.9"])
    with pytest.raises(net.UnsafeUrlError):
        net.assert_public_url("http://mixed.example/")


def test_allow_public_host(monkeypatch):
    monkeypatch.setattr(net, "resolve_host", lambda h: ["93.184.216.34"])
    net.assert_public_url("https://example.com/path")  # no raise


def test_trust_env_is_false():
    assert net._client_kwargs()["trust_env"] is False
    assert net._client_kwargs()["follow_redirects"] is False
    assert net._client_kwargs()["timeout"] == 10.0


def test_known_limitations_names_dns_rebinding():
    assert "rebinding" in net.KNOWN_LIMITATIONS.lower()
    assert "impossible" in net.KNOWN_LIMITATIONS.lower()  # "...does not make SSRF impossible"


# =========================================================================== #
# fetch -- redirects / caps / content-type
# =========================================================================== #
def test_redirect_to_private_is_blocked(monkeypatch):
    def handler(url):
        if "start" in url:
            return _FakeResp(status=302, headers={"location": "http://10.0.0.5/"}, is_redirect=True)
        return _html()

    _install(monkeypatch, handler)
    with pytest.raises(net.UnsafeUrlError):
        net.fetch("https://start.example/", kind="html", max_bytes=net.MAX_HTML_BYTES)


def test_every_redirect_hop_revalidated(monkeypatch):
    hops = {"n": 0}

    def handler(url):
        hops["n"] += 1
        if hops["n"] == 1:
            return _FakeResp(status=302, headers={"location": "https://b.example/"}, is_redirect=True)
        if hops["n"] == 2:
            return _FakeResp(status=302, headers={"location": "http://10.9.9.9/"}, is_redirect=True)
        return _html()

    _install(monkeypatch, handler)
    with pytest.raises(net.UnsafeUrlError):
        net.fetch("https://a.example/", kind="html", max_bytes=net.MAX_HTML_BYTES)


def test_redirect_limit(monkeypatch):
    def handler(url):
        return _FakeResp(status=302, headers={"location": "https://loop.example/next"}, is_redirect=True)

    _install(monkeypatch, handler)
    with pytest.raises(net.FetchError) as ei:
        net.fetch("https://loop.example/", kind="html", max_bytes=net.MAX_HTML_BYTES)
    assert ei.value.code == "too_many_redirects"


def test_https_to_non_http_redirect_rejected(monkeypatch):
    def handler(url):
        return _FakeResp(status=302, headers={"location": "ftp://evil.example/"}, is_redirect=True)

    _install(monkeypatch, handler)
    with pytest.raises(net.FetchError) as ei:
        net.fetch("https://x.example/", kind="html", max_bytes=net.MAX_HTML_BYTES)
    assert ei.value.code == "bad_redirect_scheme"


def test_offhost_redirect_with_require_same_host(monkeypatch):
    def handler(url):
        if url == "https://acme.example/":
            return _FakeResp(status=301, headers={"location": "https://other.example/"}, is_redirect=True)
        return _html()

    _install(monkeypatch, handler)
    with pytest.raises(net.FetchError) as ei:
        net.fetch("https://acme.example/", kind="html", max_bytes=net.MAX_HTML_BYTES,
                  require_same_host=True, start_host="acme.example")
    assert ei.value.code.startswith("offhost_redirect")


def test_www_toggle_allowed_across_redirect(monkeypatch):
    def handler(url):
        if url == "https://acme.example/":
            return _FakeResp(status=301, headers={"location": "https://www.acme.example/"}, is_redirect=True)
        return _html("<html><title>Acme</title></html>")

    _install(monkeypatch, handler)
    res = net.fetch("https://acme.example/", kind="html", max_bytes=net.MAX_HTML_BYTES,
                    require_same_host=True, start_host="acme.example")
    assert res.final_url == "https://www.acme.example/"


def test_html_body_capped(monkeypatch):
    big = b"<html>" + b"x" * (2 * net.MAX_HTML_BYTES)

    def handler(url):
        return _FakeResp(status=200, headers={"content-type": "text/html"}, body=big)

    _install(monkeypatch, handler)
    res = net.fetch("https://big.example/", kind="html", max_bytes=net.MAX_HTML_BYTES)
    assert res.truncated is True
    assert len(res.text.encode("utf-8", "ignore")) <= net.MAX_HTML_BYTES + 4


def test_robots_body_capped(monkeypatch):
    big = b"User-agent: *\n" + b"# pad\n" * net.MAX_ROBOTS_BYTES

    def handler(url):
        return _FakeResp(status=200, headers={"content-type": "text/plain"}, body=big)

    _install(monkeypatch, handler)
    res = net.fetch("https://big.example/robots.txt", kind="robots", max_bytes=net.MAX_ROBOTS_BYTES)
    assert res.truncated is True


@pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/octet-stream", "application/zip"])
def test_non_html_content_type_rejected(monkeypatch, ctype):
    def handler(url):
        return _FakeResp(status=200, headers={"content-type": ctype}, body=b"\x00\x01\x02")

    _install(monkeypatch, handler)
    with pytest.raises(net.FetchError) as ei:
        net.fetch("https://x.example/file", kind="html", max_bytes=net.MAX_HTML_BYTES)
    assert ei.value.code == "non_html_content"


def test_http_error_status_is_fetcherror(monkeypatch):
    def handler(url):
        return _FakeResp(status=503, headers={"content-type": "text/html"}, body=b"nope")

    _install(monkeypatch, handler)
    with pytest.raises(net.FetchError) as ei:
        net.fetch("https://x.example/", kind="html", max_bytes=net.MAX_HTML_BYTES)
    assert ei.value.code == "http_503"


# =========================================================================== #
# Per-origin pacing
# =========================================================================== #
def test_pacing_between_requests_to_same_origin(monkeypatch):
    slept = []
    monkeypatch.setattr(net, "_sleep", lambda s: slept.append(s))

    def handler(url):
        return _html()

    _install(monkeypatch, handler)
    net.reset_pacing()
    net.fetch("https://acme.example/a", kind="html", max_bytes=net.MAX_HTML_BYTES)
    net.fetch("https://acme.example/b", kind="html", max_bytes=net.MAX_HTML_BYTES)
    assert slept and slept[-1] > 0


def test_no_pacing_across_different_origins(monkeypatch):
    slept = []
    monkeypatch.setattr(net, "_sleep", lambda s: slept.append(s))
    _install(monkeypatch, lambda url: _html())
    net.reset_pacing()
    net.fetch("https://one.example/", kind="html", max_bytes=net.MAX_HTML_BYTES)
    net.fetch("https://two.example/", kind="html", max_bytes=net.MAX_HTML_BYTES)
    assert slept == []


# =========================================================================== #
# robots
# =========================================================================== #
def _robots_handler(monkeypatch, robots_status=200, robots_body=b"", robots_ctype="text/plain",
                    robots_exc=None):
    def handler(url):
        if url.endswith("/robots.txt"):
            if robots_exc is not None:
                raise robots_exc
            return _FakeResp(status=robots_status, headers={"content-type": robots_ctype}, body=robots_body)
        return _html()

    _install(monkeypatch, handler)


def test_robots_404_allows(monkeypatch):
    _robots_handler(monkeypatch, robots_status=404)
    assert RobotsCache().decision("https://acme.example/x") == "allow"


def test_robots_disallow_all(monkeypatch):
    _robots_handler(monkeypatch, robots_body=b"User-agent: *\nDisallow: /\n")
    assert RobotsCache().decision("https://acme.example/x") == "disallow"


def test_robots_selective(monkeypatch):
    _robots_handler(monkeypatch, robots_body=b"User-agent: *\nDisallow: /private\n")
    cache = RobotsCache()
    assert cache.decision("https://acme.example/private/x") == "disallow"
    assert cache.decision("https://acme.example/about") == "allow"


def test_robots_timeout_skips_site(monkeypatch):
    import httpx

    _robots_handler(monkeypatch, robots_exc=httpx.TimeoutException("slow"))
    assert RobotsCache().decision("https://acme.example/x") == "skip_site"


def test_robots_oversize_skips_site(monkeypatch):
    _robots_handler(monkeypatch, robots_body=b"# pad\n" * net.MAX_ROBOTS_BYTES)
    assert RobotsCache().decision("https://acme.example/x") == "skip_site"


def test_robots_http_error_skips_site(monkeypatch):
    _robots_handler(monkeypatch, robots_status=500, robots_ctype="text/plain")
    assert RobotsCache().decision("https://acme.example/x") == "skip_site"


def test_robots_html_error_page_skips_site(monkeypatch):
    _robots_handler(monkeypatch, robots_body=b"<html><body>404 Not Found</body></html>", robots_ctype="text/html")
    assert RobotsCache().decision("https://acme.example/x") == "skip_site"


# =========================================================================== #
# crawl_site
# =========================================================================== #
def _site(monkeypatch, pages: dict, robots_body=b""):
    """pages: path -> html. robots served empty (allow-all) by default."""

    def handler(url):
        if url.endswith("/robots.txt"):
            return _FakeResp(status=200, headers={"content-type": "text/plain"}, body=robots_body)
        from urllib.parse import urlsplit

        p = urlsplit(url)
        key = p.path or "/"
        host = p.hostname
        if host not in ("acme.example", "www.acme.example"):
            return _FakeResp(status=404, headers={"content-type": "text/html"}, body=b"nope")
        html = pages.get(key)
        if html is None:
            return _FakeResp(status=404, headers={"content-type": "text/html"}, body=b"nope")
        return _FakeResp(status=200, headers={"content-type": "text/html"}, body=html.encode("utf-8"))

    _install(monkeypatch, handler)


def test_crawl_stops_at_max_pages(monkeypatch):
    links = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(20))
    pages = {"/": f"<html>{links}</html>"}
    for i in range(20):
        pages[f"/p{i}"] = "<html>leaf</html>"
    _site(monkeypatch, pages)
    out = discovery.crawl_site("https://acme.example/", RobotsCache(), 5)
    assert len(out.pages) == 5


def test_crawl_stays_on_exact_host(monkeypatch):
    pages = {
        "/": '<html><a href="https://blog.acme.example/x">blog</a>'
             '<a href="https://acme.example/about">about</a></html>',
        "/about": "<html>about</html>",
    }
    _site(monkeypatch, pages)
    out = discovery.crawl_site("https://acme.example/", RobotsCache(), 5)
    fetched = {p[0] for p in out.pages}
    assert "https://acme.example/about" in fetched
    assert not any("blog.acme.example" in u for u in fetched)


def test_crawl_ignores_social_links(monkeypatch):
    pages = {"/": '<html><a href="https://facebook.com/acme">fb</a></html>'}
    _site(monkeypatch, pages)
    out = discovery.crawl_site("https://acme.example/", RobotsCache(), 5)
    assert len(out.pages) == 1


def test_crawl_incomplete_on_fetch_error(monkeypatch):
    def handler(url):
        if url.endswith("/robots.txt"):
            return _FakeResp(status=200, headers={"content-type": "text/plain"}, body=b"")
        if url == "https://acme.example/":
            return _html('<html><a href="/broken">x</a></html>')
        raise __import__("httpx").ConnectError("down")

    _install(monkeypatch, handler)
    out = discovery.crawl_site("https://acme.example/", RobotsCache(), 5)
    assert out.complete is False
    assert len(out.pages) == 1


# =========================================================================== #
# extract_emails
# =========================================================================== #
def test_extract_normalizes_entities_and_unicode():
    html = "<html><body>Reach us at info&#64;acme.example today</body></html>"
    got = discovery.extract_emails(html, "https://acme.example/")
    assert {e["email_normalized"] for e in got} == {"info@acme.example"}


def test_extract_strips_trailing_punctuation():
    html = "<html><body>(hello@acme.example). end.</body></html>"
    got = discovery.extract_emails(html, "https://acme.example/")
    assert got[0]["email_normalized"] == "hello@acme.example"


def test_extract_rejects_malformed():
    html = "<html><body>foo@ @bar.example a b@x.example foo@bar</body></html>"
    assert discovery.extract_emails(html, "https://acme.example/") == []


def test_extract_dedups_case_insensitively():
    html = "<html><body>Info@Acme.Example info@acme.example</body></html>"
    got = discovery.extract_emails(html, "https://acme.example/")
    assert len(got) == 1


def test_extract_ignores_scripts_comments_meta_hidden():
    html = """
    <html><head><meta name="x" content="meta@acme.example"></head>
    <body>
      <script>var a='script@acme.example';</script>
      <!-- comment@acme.example -->
      <div style="display:none">hidden@acme.example</div>
      <p>visible: hello@acme.example</p>
    </body></html>
    """
    got = {e["email_normalized"] for e in discovery.extract_emails(html, "https://acme.example/contact")}
    assert got == {"hello@acme.example"}


def test_extract_mailto_link_always_collected():
    html = '<html><body><a href="mailto:jane.doe@acme.example">email</a></body></html>'
    got = discovery.extract_emails(html, "https://acme.example/")  # not a contact page
    assert got[0]["email_normalized"] == "jane.doe@acme.example"
    assert got[0]["method"] == "mailto"


def test_named_from_visible_text_only_on_contact_page():
    html = "<html><body>jane.doe@acme.example</body></html>"
    assert discovery.extract_emails(html, "https://acme.example/pricing") == []
    got = discovery.extract_emails(html, "https://acme.example/contact")
    assert got and got[0]["classification"] == "named"


@pytest.mark.parametrize("local,expected", [
    ("info", "generic"), ("hello", "generic"), ("sales", "role"),
    ("support", "role"), ("accounts", "role"), ("jane.doe", "named"),
])
def test_classify_localpart(local, expected):
    assert discovery.classify_localpart(local) == expected


# =========================================================================== #
# Guessed-website token strength
# =========================================================================== #
def test_no_distinctive_tokens_gives_no_guess():
    assert discovery.distinctive_tokens("Bristol Services Group Ltd", "Bristol") == []


def test_legal_and_generic_stripped():
    assert discovery.distinctive_tokens("Bright Smiles Dental Ltd.", "Bristol") == ["bright", "smiles", "dental"]


def test_strong_host_match_needs_two_tokens_or_one_long():
    assert discovery.strong_host_match("brightsmiles.co.uk", ["bright", "smiles"]) is True
    assert discovery.strong_host_match("smiles.co.uk", ["bright", "smiles"]) is False
    assert discovery.strong_host_match("kensington.co.uk", ["kensington"]) is True  # single, >=6 chars
    assert discovery.strong_host_match("abc.co.uk", ["abc"]) is False


# =========================================================================== #
# resolve_business
# =========================================================================== #
def _biz(session, bid="osm:node:1", name="Bright Smiles Dental", website="", area="bristol"):
    b = Business(id=bid, area_key=area, category="dentist", name=name, lat=51.4, lon=-2.5, website=website)
    session.add(b)
    session.commit()
    return b


def test_resolve_osm_website_resolved(db_session, outreach_db, monkeypatch):
    _install(monkeypatch, lambda url: _html("<html><title>Bright Smiles</title></html>"))
    b = _biz(db_session, website="https://brightsmiles.example")
    res = discovery.resolve_business(b, "Bristol")
    assert res.outcome == "resolved" and res.website_confidence == "osm"
    assert res.official_website == "https://brightsmiles.example/"


def test_resolve_osm_offhost_redirect_unresolved(db_session, outreach_db, monkeypatch):
    def handler(url):
        if url == "https://brightsmiles.example/":
            return _FakeResp(status=301, headers={"location": "https://elsewhere.example/"}, is_redirect=True)
        return _html()

    _install(monkeypatch, handler)
    b = _biz(db_session, website="https://brightsmiles.example")
    res = discovery.resolve_business(b, "Bristol")
    assert res.outcome == "unresolved" and res.error.startswith("offhost_redirect")


def test_resolve_osm_timeout_is_transient(db_session, outreach_db, monkeypatch):
    import httpx

    def handler(url):
        raise httpx.TimeoutException("slow")

    _install(monkeypatch, handler)
    b = _biz(db_session, website="https://brightsmiles.example")
    res = discovery.resolve_business(b, "Bristol")
    assert res.outcome == "transient_failure" and res.error == "timeout"


def test_resolve_osm_private_after_resolve_is_unsafe(db_session, outreach_db, monkeypatch):
    monkeypatch.setattr(net, "resolve_host", lambda h: ["10.0.0.9"])
    b = _biz(db_session, website="https://brightsmiles.example")
    res = discovery.resolve_business(b, "Bristol")
    assert res.outcome == "unsafe"


def test_resolve_guess_verified(db_session, outreach_db, monkeypatch):
    def handler(url):
        if url == "https://brightsmilesdental.co.uk/":
            return _html("<html><title>Bright Smiles Dental — Bristol</title><h1>Bright Smiles Dental</h1></html>")
        return _FakeResp(status=404, headers={"content-type": "text/html"}, body=b"no")

    _install(monkeypatch, handler)
    b = _biz(db_session, name="Bright Smiles Dental", website="")
    res = discovery.resolve_business(b, "Bristol")
    assert res.outcome == "resolved" and res.website_confidence == "guessed_verified"


def test_resolve_guess_weak_evidence_unresolved(db_session, outreach_db, monkeypatch):
    def handler(url):
        return _html("<html><title>Welcome</title></html>")  # no name tokens on page

    _install(monkeypatch, handler)
    b = _biz(db_session, name="Bright Smiles Dental", website="")
    res = discovery.resolve_business(b, "Bristol")
    assert res.outcome == "unresolved" and res.error == "guess_unverified"


def test_resolve_guess_ambiguous_unresolved(db_session, outreach_db, monkeypatch):
    def handler(url):
        return _html("<html><title>Bright Smiles Dental</title><h1>Bright Smiles Dental</h1></html>")

    _install(monkeypatch, handler)
    b = _biz(db_session, name="Bright Smiles Dental", website="")
    res = discovery.resolve_business(b, "Bristol")
    assert res.outcome == "unresolved" and res.error == "guess_ambiguous"


def test_resolve_insufficient_name_signal(db_session, outreach_db):
    b = _biz(db_session, name="Services Group Ltd", website="")
    res = discovery.resolve_business(b, "Bristol")
    assert res.outcome == "unresolved" and res.error == "insufficient_name_signal"


# =========================================================================== #
# discover_area persistence + backoff
# =========================================================================== #
def test_discover_area_sets_discovery_at_only_on_resolved(db_session, outreach_db, monkeypatch):
    _install(monkeypatch, lambda url: _html("<html><title>Bright Smiles</title></html>"))
    _biz(db_session, bid="b1", name="Bright Smiles Dental", website="https://a.example")
    _biz(db_session, bid="b2", name="Services Group Ltd", website="")  # -> unresolved

    counts = discovery.discover_area(db_session, "bristol", "Bristol", 10)
    assert counts["resolved"] == 1 and counts["unresolved"] == 1

    rows = {r[0]: r for r in db_session.execute(
        __import__("sqlalchemy").text(
            "SELECT id, discovery_status, discovery_at, discovery_attempts FROM business"
        )
    ).all()}
    assert rows["b1"][1] == "resolved" and rows["b1"][2] is not None and rows["b1"][3] == 0
    assert rows["b2"][1] == "unresolved" and rows["b2"][2] is None and rows["b2"][3] == 1


def test_transient_failure_not_retried_before_backoff(db_session, outreach_db, monkeypatch):
    import httpx

    _install(monkeypatch, lambda url: (_ for _ in ()).throw(httpx.TimeoutException("x")))
    _biz(db_session, bid="b1", name="Bright Smiles Dental", website="https://a.example")

    c1 = discovery.discover_area(db_session, "bristol", "Bristol", 10)
    assert c1["transient_failure"] == 1
    c2 = discovery.discover_area(db_session, "bristol", "Bristol", 10)
    assert c2["attempted"] == 0  # backoff not elapsed


def test_resolved_and_unsafe_never_auto_retried(db_session, outreach_db, monkeypatch):
    _install(monkeypatch, lambda url: _html("<html><title>Bright Smiles</title></html>"))
    _biz(db_session, bid="b1", name="Bright Smiles Dental", website="https://a.example")
    discovery.discover_area(db_session, "bristol", "Bristol", 10)
    again = discovery.discover_area(db_session, "bristol", "Bristol", 10)
    assert again["attempted"] == 0


# =========================================================================== #
# Endpoints
# =========================================================================== #
def _seed_business(oclient, bid="osm:node:1", name="Bright Smiles Dental", website="https://a.example"):
    from backend.db import get_session

    with get_session() as s:
        s.add(Business(id=bid, area_key="bristol", category="dentist", name=name,
                       lat=51.4, lon=-2.5, website=website))
        s.commit()


def test_discover_endpoint_happy(oclient, monkeypatch):
    _install(monkeypatch, lambda url: _html("<html><title>Bright Smiles</title></html>"))
    _seed_business(oclient)
    r = oclient.post("/api/prospects/bristol/discover", json={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] == 1 and body["attempted"] == 1


def test_discover_unknown_area_404(oclient):
    r = oclient.post("/api/prospects/nowhere/discover", json={})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize("limit", [0, 11])
def test_discover_limit_out_of_range_422(oclient, limit):
    r = oclient.post("/api/prospects/bristol/discover", json={"limit": limit})
    assert r.status_code == 422


def test_collect_only_crawls_resolved(oclient, monkeypatch):
    _install(monkeypatch, _pages_handler(
        '<html><body>Email <a href="mailto:info@a.example">us</a></body></html>'
    ))
    _seed_business(oclient)
    # not resolved yet -> nothing collected
    r0 = oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})
    assert r0.json()["attempted"] == 0

    oclient.post("/api/prospects/bristol/discover", json={"limit": 5})
    r1 = oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})
    assert r1.json()["attempted"] == 1 and r1.json()["contacts_added"] == 1

    # second sweep: contacts_collected_at set -> not re-crawled
    r2 = oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})
    assert r2.json()["attempted"] == 0


def test_collect_zero_contacts_marks_collected(oclient, monkeypatch):
    _install(monkeypatch, _pages_handler("<html><body>no email here</body></html>"))
    _seed_business(oclient)
    oclient.post("/api/prospects/bristol/discover", json={"limit": 5})
    r1 = oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})
    assert r1.json()["contacts_added"] == 0
    r2 = oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})
    assert r2.json()["attempted"] == 0  # zero-contact result is stable, not auto-retried


def test_collect_robots_skip_stays_retryable(oclient, monkeypatch):
    import httpx

    def handler(url):
        if url.endswith("/robots.txt"):
            raise httpx.TimeoutException("slow")
        return _html("<html><body>info@a.example</body></html>")

    _install(monkeypatch, handler)
    _seed_business(oclient)
    oclient.post("/api/prospects/bristol/discover", json={"limit": 5})
    r1 = oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})
    assert r1.json()["sites_skipped"] == 1
    r2 = oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})
    assert r2.json()["attempted"] == 1  # still eligible


def test_list_contacts_and_discovery_status(oclient, monkeypatch):
    _install(monkeypatch, _pages_handler(
        '<html><body><a href="mailto:info@a.example">e</a></body></html>'
    ))
    _seed_business(oclient)
    oclient.post("/api/prospects/bristol/discover", json={"limit": 5})
    oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})

    rc = oclient.get("/api/prospects/bristol/contacts")
    assert rc.status_code == 200
    c = rc.json()["contacts"][0]
    assert c["active"] is True and c["email_normalized"] == "info@a.example"
    assert c["evidence"] and c["evidence"][0]["method"] == "mailto"

    rd = oclient.get("/api/prospects/bristol/discovery")
    assert rd.status_code == 200
    assert rd.json()["counts"]["resolved"] == 1

    assert oclient.get("/api/prospects/nope/contacts").status_code == 404
    assert oclient.get("/api/prospects/nope/discovery").status_code == 404


def test_rediscover_invalidates_contacts_but_keeps_history(oclient, monkeypatch):
    _install(monkeypatch, _pages_handler(
        '<html><body><a href="mailto:info@a.example">e</a></body></html>'
    ))
    _seed_business(oclient)
    oclient.post("/api/prospects/bristol/discover", json={"limit": 5})
    oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})

    r = oclient.post("/api/prospects/bristol/rediscover", json={"business_ids": ["osm:node:1"]})
    assert r.status_code == 200
    assert r.json()["reset"] == 1 and r.json()["contacts_deactivated"] == 1

    from backend.db import get_session

    with get_session() as s:
        contacts = s.execute(select(OutreachContact)).scalars().all()
        assert len(contacts) == 1 and contacts[0].active is False
        assert contacts[0].stale_reason == "rediscovery"
        assert s.execute(select(ContactEvidence)).scalars().all()  # evidence retained
        status = s.execute(
            __import__("sqlalchemy").text("SELECT discovery_status FROM business WHERE id='osm:node:1'")
        ).scalar()
        assert status == ""


def test_rediscover_then_discover_then_recollect_reactivates(oclient, monkeypatch):
    """Correction #1: recollection only works on a re-resolved business."""
    _install(monkeypatch, _pages_handler(
        '<html><body><a href="mailto:info@a.example">e</a></body></html>'
    ))
    _seed_business(oclient)
    oclient.post("/api/prospects/bristol/discover", json={"limit": 5})
    oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})
    oclient.post("/api/prospects/bristol/rediscover", json={"business_ids": ["osm:node:1"]})

    # recollect while unresolved -> refused (skipped_not_resolved)
    r_bad = oclient.post("/api/prospects/bristol/contacts/recollect", json={"business_ids": ["osm:node:1"]})
    assert r_bad.json()["skipped_not_resolved"] == 1 and r_bad.json()["attempted"] == 0

    oclient.post("/api/prospects/bristol/discover", json={"limit": 5})  # re-resolve
    r_ok = oclient.post("/api/prospects/bristol/contacts/recollect", json={"business_ids": ["osm:node:1"]})
    assert r_ok.json()["attempted"] == 1 and r_ok.json()["contacts_reactivated"] == 1

    from backend.db import get_session

    with get_session() as s:
        c = s.execute(select(OutreachContact)).scalars().one()
        assert c.active is True and c.stale_reason == ""
        # evidence deduped: still exactly one row for the same (contact,url,method)
        assert len(s.execute(select(ContactEvidence)).scalars().all()) == 1


def test_recollect_deactivates_not_refound_only_when_complete(oclient, monkeypatch):
    """Correction #2: successful absence deactivates; incomplete-crawl absence does not."""
    state = {"emails": '<a href="mailto:a@x.example">a</a><a href="mailto:b@x.example">b</a>'}

    def handler(url):
        if url.endswith("/robots.txt"):
            return _FakeResp(status=200, headers={"content-type": "text/plain"}, body=b"")
        return _html(f"<html><body>{state['emails']}</body></html>")

    _install(monkeypatch, handler)
    _seed_business(oclient)
    oclient.post("/api/prospects/bristol/discover", json={"limit": 5})
    oclient.post("/api/prospects/bristol/contacts/collect", json={"limit": 5})

    # incomplete crawl (robots now times out) with only 'a' present -> 'b' NOT deactivated
    state["emails"] = '<a href="mailto:a@x.example">a</a>'
    import httpx

    def handler_incomplete(url):
        if url.endswith("/robots.txt"):
            raise httpx.TimeoutException("slow")
        return _html(f"<html><body>{state['emails']}</body></html>")

    _install(monkeypatch, handler_incomplete)
    oclient.post("/api/prospects/bristol/contacts/recollect", json={"business_ids": ["osm:node:1"]})
    from backend.db import get_session

    with get_session() as s:
        by_email = {c.email_normalized: c for c in s.execute(select(OutreachContact)).scalars().all()}
        assert by_email["b@x.example"].active is True  # incomplete -> not deactivated

    # now a complete crawl with only 'a' -> 'b' deactivated as not_refound
    _install(monkeypatch, handler)
    oclient.post("/api/prospects/bristol/contacts/recollect", json={"business_ids": ["osm:node:1"]})
    with get_session() as s:
        by_email = {c.email_normalized: c for c in s.execute(select(OutreachContact)).scalars().all()}
        assert by_email["a@x.example"].active is True
        assert by_email["b@x.example"].active is False
        assert by_email["b@x.example"].stale_reason == "not_refound"


def test_rediscover_requires_selector(oclient):
    r = oclient.post("/api/prospects/bristol/rediscover", json={})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "validation_error"
