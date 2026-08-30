"""Website discovery + contact collection.

Same-site rule (no Public Suffix List dependency): a link is followed only when
its normalized host -- lowercased, one leading ``www.`` stripped -- exactly
equals the start host. No sibling subdomains, no off-site links.

Discovery outcomes per business:

* ``resolved``           -- official website verified; ``discovery_at`` (the
                            permanent "done" marker) is set, attempts reset to 0
* ``unresolved``         -- no plausible verified website; not auto-retried
* ``unsafe``             -- URL failed an SSRF check; needs manual ``/rediscover``
* ``transient_failure``  -- DNS/timeout/5xx/truncation; auto-retried after a
                            capped exponential backoff

Contacts are only ever crawled for a ``resolved`` business with a non-empty
``official_website``.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from sqlalchemy import text
from sqlmodel import select

from backend import config
from backend.models import Business
from backend.outreach import migrate, net
from backend.outreach.models import ContactEvidence, DiscoveryLog, OutreachContact, OutreachEvent, OutreachThread
from backend.outreach.robots import RobotsCache

# --------------------------------------------------------------------------- #
# Timestamps: one internal string format for every column this module writes,
# matching SQLAlchemy's SQLite DateTime storage so UTCDateTime columns round-trip.
# --------------------------------------------------------------------------- #
_TS_FMT = "%Y-%m-%d %H:%M:%S.%f"


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("T", " ").replace("Z", "")
    if "+" in raw[10:]:
        raw = raw[:raw.index("+", 10)]
    for fmt in (_TS_FMT, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Backoff for transient failures
# --------------------------------------------------------------------------- #
BACKOFF_BASE_SECONDS = 3600
BACKOFF_CAP_SECONDS = 86_400


def backoff_seconds(attempts: int) -> int:
    return min(BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0)), BACKOFF_CAP_SECONDS)


def retry_eligible_at(attempts: int, attempted_at: str | None) -> datetime | None:
    base = parse_ts(attempted_at)
    if base is None:
        return None
    return base + timedelta(seconds=backoff_seconds(attempts))


def _transient_backoff_elapsed(attempts: int, attempted_at: str | None, now: datetime) -> bool:
    eligible = retry_eligible_at(attempts or 0, attempted_at)
    return eligible is None or now >= eligible


# --------------------------------------------------------------------------- #
# Name tokenization for guessed websites
# --------------------------------------------------------------------------- #
_LEGAL_SUFFIXES = {
    "ltd", "limited", "llp", "llc", "inc", "plc", "co", "company", "group",
    "holdings", "gmbh", "sa", "sarl", "bv", "pty",
}
_GENERIC_STOPWORDS = {
    "services", "service", "solutions", "consulting", "consultancy", "associates",
    "partners", "agency", "studio", "the", "and", "of", "for", "your", "our",
}
_LOCATION_STOPWORDS = {
    "uk", "gb", "england", "scotland", "wales", "britain", "british",
}
_SOCIAL_HOST_MARKERS = (
    "facebook.", "fb.", "twitter.", "x.com", "instagram.", "linkedin.", "lnkd.in",
    "youtube.", "youtu.be", "tiktok.", "pinterest.", "yelp.", "yell.com",
    "tripadvisor.", "google.", "goo.gl", "bing.", "t.me",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def distinctive_tokens(name: str | None, area_label: str | None) -> list[str]:
    words = _TOKEN_RE.findall((name or "").lower())
    location = set(_TOKEN_RE.findall((area_label or "").lower())) | _LOCATION_STOPWORDS
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        if len(w) < 3 or w in _LEGAL_SUFFIXES or w in _GENERIC_STOPWORDS or w in location:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def candidate_hosts(tokens: list[str]) -> list[str]:
    if not tokens:
        return []
    joined = "".join(tokens)
    hyphen = "-".join(tokens)
    bases = [b for b in (joined, hyphen) if 1 <= len(b) <= 63]
    tlds = (".co.uk", ".com", ".org.uk")
    hosts: list[str] = []
    for base in bases:
        for tld in tlds:
            host = base + tld
            if host not in hosts:
                hosts.append(host)
    return hosts[:4]


def _host_label(host: str) -> str:
    first = net.norm_host(host).split(".")[0]
    return first.replace("-", "")


def strong_host_match(host: str, tokens: list[str]) -> bool:
    label = _host_label(host)
    matched = [t for t in tokens if t in label]
    if len(matched) >= 2:
        return True
    if len(tokens) == 1 and len(tokens[0]) >= 6 and tokens[0] in label:
        return True
    return False


_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_LD_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]{1,200})"')


def page_evidence(html: str, tokens: list[str]) -> list[str]:
    """Return the labels of the on-page signals that corroborate ``tokens``."""
    ev: list[str] = []
    lowered = html.lower()

    def _clean(fragment: str) -> str:
        return _TAG_RE.sub(" ", html_lib.unescape(fragment)).lower()

    title = " ".join(_clean(m) for m in _TITLE_RE.findall(html))
    if any(t in title for t in tokens):
        ev.append("title")

    heading = " ".join(_clean(m) for m in _H1_RE.findall(html))
    if any(t in heading for t in tokens):
        ev.append("h1")

    ld = " ".join(m.lower() for m in _LD_NAME_RE.findall(html))
    if any(t in ld for t in tokens):
        ev.append("schema_org_name")

    if "contact" in lowered or "about" in lowered:
        body = visible_text(html).lower()
        if any(t in body for t in tokens):
            ev.append("contact_about_body")

    return ev


# --------------------------------------------------------------------------- #
# HTML -> visible text + email extraction
# --------------------------------------------------------------------------- #
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style|noscript|template)\b[^>]*>.*?</\1>")
_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_HEAD_RE = re.compile(r"(?is)<head\b[^>]*>.*?</head>")
_HIDDEN_EL_RE = re.compile(
    r"(?is)<([a-z0-9]+)\b[^>]*(?:\bhidden\b|style\s*=\s*(?P<q>['\"])[^'\"]*"
    r"(?:display\s*:\s*none|visibility\s*:\s*hidden)[^'\"]*(?P=q))[^>]*>.*?</\1>"
)
_BLOCK_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h[1-6]>|</tr>")

_MAILTO_RE = re.compile(r'mailto:([^"\'>\s?<]+)', re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}")
_HREF_RE = re.compile(r'href\s*=\s*(?P<q>["\'])(?P<url>[^"\']+)(?P=q)', re.I)
_STRIP_EDGE = " \t\r\n.,;:!?)('\"<>[]{}|"

_GENERIC_LOCALPARTS = {
    "info", "hello", "contact", "enquiries", "inquiries", "hi", "mail", "office",
    "admin", "reception", "team", "general",
}
_ROLE_LOCALPARTS = {
    "sales", "support", "accounts", "billing", "hr", "careers", "jobs", "bookings",
    "reservations", "press", "marketing", "help", "service", "hello",
}
_CONTACT_PATH_RE = re.compile(
    r"(contact|kontakt|about|team|staff|people|our-people|get-in-touch|meet)", re.I
)
_CONTACT_HEADING_RE = re.compile(
    r"<h[1-3][^>]*>[^<]*(contact|about us|our team|meet the team|get in touch)", re.I
)


def visible_text(html: str) -> str:
    t = html or ""
    t = _COMMENT_RE.sub(" ", t)
    t = _HEAD_RE.sub(" ", t)
    t = _SCRIPT_STYLE_RE.sub(" ", t)
    t = _HIDDEN_EL_RE.sub(" ", t)
    t = _BLOCK_BREAK_RE.sub("\n", t)
    t = _TAG_RE.sub(" ", t)
    t = html_lib.unescape(t)
    return re.sub(r"[ \t]+", " ", t)


def classify_localpart(localpart: str) -> str:
    lp = localpart.lower()
    if lp in _GENERIC_LOCALPARTS:
        return "generic"
    if lp in _ROLE_LOCALPARTS:
        return "role"
    return "named"


def _valid_email(candidate: str) -> str | None:
    s = unicodedata.normalize("NFKC", candidate).strip(_STRIP_EDGE).lower()
    if s.count("@") != 1:
        return None
    local, _, domain = s.partition("@")
    if not local or not domain or "." not in domain:
        return None
    if any(ch.isspace() for ch in s):
        return None
    if len(local) > 64 or len(s) > 254:
        return None
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return None
    if any(not part for part in domain.split(".")):
        return None
    if not re.fullmatch(r"[a-z0-9.\-]+", domain):
        return None
    return s


def page_kind(url: str, html: str) -> str:
    path = urlsplit(url).path.lower()
    if _CONTACT_PATH_RE.search(path):
        return "contact_page"
    if _CONTACT_HEADING_RE.search(html or ""):
        return "contact_page"
    return "other"


def extract_emails(html: str, page_url: str) -> list[dict]:
    """Return ``[{email, email_normalized, classification, method, page_kind}]``.

    ``mailto:`` links are read from the raw HTML; text addresses are read only
    from visible content (scripts, comments, ``<head>``/``<meta>`` and hidden
    elements excluded). A ``named`` address from visible text is kept only on a
    contact/about/team page; ``mailto:`` links are kept regardless.
    """
    raw = html or ""
    kind = page_kind(page_url, raw)
    found: dict[str, dict] = {}

    def _add(candidate: str, method: str) -> None:
        norm = _valid_email(candidate)
        if norm is None:
            return
        cls = classify_localpart(norm.split("@", 1)[0])
        if cls == "named" and method != "mailto" and kind != "contact_page":
            return
        prev = found.get(norm)
        if prev is None or (prev["method"] != "mailto" and method == "mailto"):
            found[norm] = {
                "email": norm,
                "email_normalized": norm,
                "classification": cls,
                "method": method,
                "page_kind": kind,
            }

    for m in _MAILTO_RE.findall(raw):
        _add(m, "mailto")

    text_only = html_lib.unescape(unicodedata.normalize("NFKC", visible_text(raw)))
    for m in _EMAIL_RE.findall(text_only):
        _add(m, "visible_text")

    return list(found.values())


def _same_site_links(html: str, base_url: str, start_host: str) -> list[str]:
    out: list[str] = []
    for m in _HREF_RE.finditer(html or ""):
        href = m.group("url").strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme.lower() not in net.ALLOWED_SCHEMES:
            continue
        host = (parts.hostname or "").lower()
        if net.norm_host(host) != net.norm_host(start_host):
            continue
        if any(marker in host for marker in _SOCIAL_HOST_MARKERS):
            continue
        clean = absolute.split("#", 1)[0]
        if clean not in out:
            out.append(clean)
    return out


def _guess_contact_page(html: str, base_url: str) -> str:
    start_host = urlsplit(base_url).hostname or ""
    for link in _same_site_links(html, base_url, start_host):
        if _CONTACT_PATH_RE.search(urlsplit(link).path or ""):
            return link
    return ""


# --------------------------------------------------------------------------- #
# Crawl
# --------------------------------------------------------------------------- #
@dataclass
class CrawlOutput:
    pages: list[tuple[str, str]] = field(default_factory=list)  # (final_url, html)
    complete: bool = True  # False if any robots-skip / fetch error / truncation occurred


def crawl_site(start_url: str, robots: RobotsCache, max_pages: int) -> CrawlOutput:
    start_host = urlsplit(start_url).hostname or ""
    out = CrawlOutput()
    seen: set[str] = set()
    queue: list[str] = [start_url]

    while queue and len(out.pages) < max_pages:
        url = queue.pop(0)
        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)

        decision = robots.decision(url)
        if decision == "skip_site":
            return CrawlOutput(pages=[], complete=False)
        if decision == "disallow":
            continue

        try:
            res = net.fetch(
                url,
                kind="html",
                max_bytes=net.MAX_HTML_BYTES,
                require_same_host=True,
                start_host=start_host,
            )
        except (net.FetchError, net.UnsafeUrlError):
            out.complete = False
            continue

        if res.truncated:
            out.complete = False
            continue
        if net.norm_host(urlsplit(res.final_url).hostname) != net.norm_host(start_host):
            continue

        out.pages.append((res.final_url, res.text))

        # contact-ish links first so a small page budget still reaches them
        links = _same_site_links(res.text, res.final_url, start_host)
        links.sort(key=lambda u: 0 if _CONTACT_PATH_RE.search(urlsplit(u).path or "") else 1)
        for link in links:
            if link.split("#", 1)[0] not in seen:
                queue.append(link)

    return out


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
@dataclass
class Resolution:
    outcome: str  # resolved | unresolved | unsafe | transient_failure
    method: str  # osm | guessed | none
    official_website: str = ""
    website_confidence: str = ""
    contact_page_url: str = ""
    error: str = ""
    candidates: list = field(default_factory=list)
    evidence: list = field(default_factory=list)


_UNRESOLVED_FETCH_CODES = {
    "non_html_content", "http_400", "http_401", "http_403", "http_404", "http_410",
}


def _classify_fetch_error(code: str) -> str:
    if code.startswith("offhost_redirect"):
        return "unresolved"
    if code in _UNRESOLVED_FETCH_CODES:
        return "unresolved"
    return "transient_failure"  # timeout / connection / 5xx / 429 / redirects / etc.


def _resolve_from_osm(name: str, website: str) -> Resolution:
    url = website if website.lower().startswith(("http://", "https://")) else "https://" + website
    host = urlsplit(url).hostname or ""
    try:
        net.assert_public_url(url)
    except net.UnsafeUrlError as exc:
        return Resolution("unsafe", "osm", error=str(exc)[:120])
    try:
        res = net.fetch(url, kind="html", max_bytes=net.MAX_HTML_BYTES, require_same_host=True, start_host=host)
    except net.UnsafeUrlError as exc:
        return Resolution("unsafe", "osm", error=str(exc)[:120])
    except net.FetchError as exc:
        return Resolution(_classify_fetch_error(exc.code), "osm", error=exc.code[:120])
    if res.truncated:
        return Resolution("transient_failure", "osm", error="body_too_large")
    return Resolution(
        "resolved",
        "osm",
        official_website=res.final_url,
        website_confidence="osm",
        contact_page_url=_guess_contact_page(res.text, res.final_url),
    )


def _resolve_by_guess(name: str, area_label: str) -> Resolution:
    tokens = distinctive_tokens(name, area_label)
    if not tokens:
        return Resolution("unresolved", "none", error="insufficient_name_signal")

    tried: list[dict] = []
    passed: list[tuple[str, str, list]] = []  # (final_url, html, evidence)
    for host in candidate_hosts(tokens):
        url = "https://" + host
        try:
            net.assert_public_url(url)
        except net.UnsafeUrlError as exc:
            tried.append({"host": host, "reject": str(exc)[:60]})
            continue
        try:
            res = net.fetch(url, kind="html", max_bytes=net.MAX_HTML_BYTES, require_same_host=True, start_host=host)
        except (net.FetchError, net.UnsafeUrlError) as exc:
            tried.append({"host": host, "reject": getattr(exc, "code", str(exc))[:60]})
            continue
        if res.truncated:
            tried.append({"host": host, "reject": "body_too_large"})
            continue
        ev = page_evidence(res.text, tokens)
        if strong_host_match(host, tokens) and ev:
            passed.append((res.final_url, res.text, ev))
            tried.append({"host": host, "reject": None, "evidence": ev})
        else:
            tried.append(
                {"host": host, "reject": "weak_match", "host_ok": strong_host_match(host, tokens), "evidence": ev}
            )

    if len(passed) == 1:
        final_url, page_html, ev = passed[0]
        return Resolution(
            "resolved",
            "guessed",
            official_website=final_url,
            website_confidence="guessed_verified",
            contact_page_url=_guess_contact_page(page_html, final_url),
            candidates=tried,
            evidence=ev,
        )
    if len(passed) >= 2:
        return Resolution("unresolved", "guessed", error="guess_ambiguous", candidates=tried)
    return Resolution("unresolved", "guessed", error="guess_unverified", candidates=tried)


def resolve_business(business: Business, area_label: str) -> Resolution:
    website = (getattr(business, "website", "") or "").strip()
    if website:
        return _resolve_from_osm(business.name, website)
    return _resolve_by_guess(business.name, area_label)


# --------------------------------------------------------------------------- #
# Persistence helpers (raw SQL for the additive business columns)
# --------------------------------------------------------------------------- #
_BUSINESS_READABLE_COLS = frozenset(
    {name for name, _ in migrate._BUSINESS_COLUMNS}
    | {"id", "name", "website", "area_key", "category"}
)


def _business_col(session, business_id: str, col: str):
    # `col` is only ever a hard-coded literal from this module; the whitelist is
    # defence-in-depth so an accidental caller change can't inject SQL.
    if col not in _BUSINESS_READABLE_COLS:
        raise ValueError(f"disallowed business column: {col!r}")
    return session.execute(
        text(f"SELECT {col} FROM business WHERE id = :i"), {"i": business_id}
    ).scalar()


def _persist_resolution(session, business_id: str, res: Resolution) -> None:
    prev_attempts = _business_col(session, business_id, "discovery_attempts") or 0
    attempts = 0 if res.outcome == "resolved" else int(prev_attempts) + 1
    now = _now_str()

    fields: dict[str, object] = {
        "discovery_status": res.outcome,
        "discovery_error": (res.error or "")[:120],
        "discovery_attempted_at": now,
        "discovery_attempts": attempts,
    }
    if res.outcome == "resolved":
        fields["official_website"] = res.official_website
        fields["website_confidence"] = res.website_confidence
        fields["contact_page_url"] = res.contact_page_url or ""
        fields["discovery_at"] = now

    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    session.execute(
        text(f"UPDATE business SET {assignments} WHERE id = :id"),
        {**fields, "id": business_id},
    )
    session.add(
        DiscoveryLog(
            business_id=business_id,
            outcome=res.outcome,
            method=res.method,
            candidates_json=json.dumps(res.candidates)[:2000],
            evidence_json=json.dumps(res.evidence)[:1000],
            error=(res.error or "")[:120],
        )
    )


def invalidate_contact_on_threads(session, business_id: str, contact_id: int, *, reason: str, event_kind: str) -> int:
    threads = session.execute(
        select(OutreachThread).where(
            OutreachThread.business_id == business_id,
            OutreachThread.selected_contact_id == contact_id,
            OutreachThread.stage.in_(("identified", "drafted", "approved")),
        )
    ).scalars().all()
    for thread in threads:
        from_stage = thread.stage
        thread.selected_contact_id = None
        if thread.stage == "approved":
            thread.stage = "drafted"
        thread.approved_at = None
        thread.updated_at = datetime.now(timezone.utc)
        session.add(
            OutreachEvent(
                thread_id=thread.id,
                kind=event_kind,
                detail=json.dumps({"contact_id": contact_id, "from_stage": from_stage, "reason": reason}),
            )
        )
    return len(threads)


# --------------------------------------------------------------------------- #
# Contact ingestion
# --------------------------------------------------------------------------- #
_METHOD_RANK = {"mailto": 1, "visible_text": 0}
_PAGE_RANK = {"contact_page": 1, "other": 0}
_CLASS_RANK = {"named": 2, "role": 1, "generic": 0}


def _recompute_contact_strength(session, contact: OutreachContact) -> None:
    rows = session.execute(
        select(ContactEvidence).where(ContactEvidence.contact_id == contact.id)
    ).scalars().all()
    if not rows:
        return
    best = max(
        rows,
        key=lambda e: (
            _METHOD_RANK.get(e.method, 0),
            _PAGE_RANK.get(e.page_kind, 0),
            _CLASS_RANK.get(e.classification_at_source, 0),
            e.found_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
    )
    contact.method = best.method
    contact.classification = best.classification_at_source


def _ingest_page_contacts(session, business_id: str, site: str, pages: list[tuple[str, str]]) -> dict:
    stats = {"contacts_added": 0, "evidence_added": 0, "reactivated": 0, "found_norms": set()}
    for final_url, page_html in pages:
        for e in extract_emails(page_html, final_url):
            norm = e["email_normalized"]
            stats["found_norms"].add(norm)
            contact = session.execute(
                select(OutreachContact).where(
                    OutreachContact.business_id == business_id,
                    OutreachContact.email_normalized == norm,
                )
            ).scalar_one_or_none()

            if contact is None:
                contact = OutreachContact(
                    business_id=business_id,
                    email=e["email"],
                    email_normalized=norm,
                    classification=e["classification"],
                    method=e["method"],
                    active=True,
                    verified_website=site,
                )
                session.add(contact)
                session.flush()
                stats["contacts_added"] += 1
            else:
                if not contact.active:
                    stats["reactivated"] += 1
                contact.active = True
                contact.stale_reason = ""
                contact.deactivated_at = None
                contact.verified_website = site

            exists = session.execute(
                select(ContactEvidence.id).where(
                    ContactEvidence.contact_id == contact.id,
                    ContactEvidence.source_url == final_url,
                    ContactEvidence.method == e["method"],
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    ContactEvidence(
                        contact_id=contact.id,
                        business_id=business_id,
                        email_normalized=norm,
                        source_url=final_url,
                        method=e["method"],
                        classification_at_source=e["classification"],
                        page_kind=e["page_kind"],
                    )
                )
                session.flush()
                stats["evidence_added"] += 1

            _recompute_contact_strength(session, contact)
    return stats


# --------------------------------------------------------------------------- #
# Area-level operations
# --------------------------------------------------------------------------- #
def _select_discovery_candidates(session, area_key: str, limit: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT id, name, website,
                   COALESCE(discovery_status,'')   AS discovery_status,
                   COALESCE(discovery_attempts,0)  AS discovery_attempts,
                   discovery_attempted_at
            FROM business
            WHERE area_key = :a
              AND COALESCE(discovery_status,'') IN ('', 'transient_failure')
            ORDER BY COALESCE(discovery_attempted_at, '') ASC, id ASC
            LIMIT :lim
            """
        ),
        {"a": area_key, "lim": limit * 4},
    ).mappings().all()

    now = datetime.now(timezone.utc)
    picked: list[dict] = []
    for row in rows:
        status = row["discovery_status"] or ""
        if status == "":
            picked.append(dict(row))
        elif status == "transient_failure" and _transient_backoff_elapsed(
            row["discovery_attempts"] or 0, row["discovery_attempted_at"], now
        ):
            picked.append(dict(row))
        if len(picked) >= limit:
            break
    return picked


def discover_area(session, area_key: str, area_label: str, limit: int) -> dict:
    counts = {
        "attempted": 0,
        "resolved": 0,
        "unresolved": 0,
        "unsafe": 0,
        "transient_failure": 0,
        "skipped_backoff": 0,
    }
    net.reset_pacing()
    for row in _select_discovery_candidates(session, area_key, limit):
        counts["attempted"] += 1
        business = session.get(Business, row["id"])
        if business is None:
            continue
        res = resolve_business(business, area_label)
        _persist_resolution(session, business.id, res)
        counts[res.outcome] += 1
        session.commit()  # per-business: partial progress survives a later failure
    return counts


def rediscover(session, area_key: str, business_ids, statuses, include_resolved: bool) -> dict:
    where = ["area_key = :a"]
    params: dict[str, object] = {"a": area_key}
    if business_ids:
        placeholders = ", ".join(f":b{i}" for i in range(len(business_ids)))
        where.append(f"id IN ({placeholders})")
        params.update({f"b{i}": bid for i, bid in enumerate(business_ids)})
    if statuses:
        placeholders = ", ".join(f":s{i}" for i in range(len(statuses)))
        where.append(f"COALESCE(discovery_status,'') IN ({placeholders})")
        params.update({f"s{i}": s for i, s in enumerate(statuses)})

    ids = session.execute(
        text(f"SELECT id FROM business WHERE {' AND '.join(where)}"), params
    ).scalars().all()

    now = datetime.now(timezone.utc)
    reset = deactivated = threads_reset = 0
    explicit_ids = set(business_ids or [])
    for bid in ids:
        status = _business_col(session, bid, "discovery_status") or ""
        # A status-only sweep skips already-resolved rows unless include_resolved;
        # an explicitly-named business is always reset (its site may be wrong).
        if status == "resolved" and not include_resolved and bid not in explicit_ids:
            continue
        reset += 1

        fields = {
            "discovery_status": "",
            "discovery_error": "",
            "discovery_attempts": 0,
            "discovery_at": None,
        }
        if include_resolved:
            fields.update(
                {
                    "official_website": "",
                    "website_confidence": "",
                    "contact_page_url": "",
                    "contacts_collected_at": None,
                }
            )
        assignments = ", ".join(f"{k} = :{k}" for k in fields)
        session.execute(text(f"UPDATE business SET {assignments} WHERE id = :id"), {**fields, "id": bid})

        contacts = session.execute(
            select(OutreachContact).where(
                OutreachContact.business_id == bid, OutreachContact.active == True  # noqa: E712
            )
        ).scalars().all()
        for contact in contacts:
            contact.active = False
            contact.stale_reason = "rediscovery"
            contact.deactivated_at = now
            deactivated += 1
            threads_reset += invalidate_contact_on_threads(
                session, bid, contact.id, reason="rediscovery", event_kind="contact_stale"
            )

        session.add(DiscoveryLog(business_id=bid, outcome="reset", method="none"))

    session.commit()
    return {"reset": reset, "contacts_deactivated": deactivated, "threads_reset": threads_reset}


def _resolved_sites(session, area_key: str, limit: int, *, only_uncollected: bool) -> list[tuple[str, str]]:
    clause = "AND contacts_collected_at IS NULL" if only_uncollected else ""
    rows = session.execute(
        text(
            f"""
            SELECT id, official_website FROM business
            WHERE area_key = :a
              AND COALESCE(discovery_status,'') = 'resolved'
              AND COALESCE(official_website,'') <> ''
              {clause}
            ORDER BY COALESCE(discovery_at,'') ASC, id ASC
            LIMIT :lim
            """
        ),
        {"a": area_key, "lim": limit},
    ).all()
    return [(r[0], r[1]) for r in rows]


def collect_area(session, area_key: str, limit: int) -> dict:
    counts = {
        "attempted": 0,
        "contacts_added": 0,
        "evidence_added": 0,
        "sites_skipped": 0,
        "pages_fetched": 0,
    }
    robots = RobotsCache()
    net.reset_pacing()

    for business_id, site in _resolved_sites(session, area_key, limit, only_uncollected=True):
        counts["attempted"] += 1
        if robots.decision(site) == "skip_site":
            counts["sites_skipped"] += 1
            session.add(DiscoveryLog(business_id=business_id, outcome="contacts_skip", method="none", error="robots_skip"))
            session.commit()
            continue

        crawl = crawl_site(site, robots, config.OUTREACH_CRAWL_MAX_PAGES)
        counts["pages_fetched"] += len(crawl.pages)
        if not crawl.pages and not crawl.complete:
            counts["sites_skipped"] += 1
            session.add(
                DiscoveryLog(business_id=business_id, outcome="contacts_skip", method="none", error="crawl_incomplete")
            )
            session.commit()
            continue

        stats = _ingest_page_contacts(session, business_id, site, crawl.pages)
        counts["contacts_added"] += stats["contacts_added"]
        counts["evidence_added"] += stats["evidence_added"]

        session.execute(
            text("UPDATE business SET contacts_collected_at = :t WHERE id = :i"),
            {"t": _now_str(), "i": business_id},
        )
        session.add(
            DiscoveryLog(
                business_id=business_id,
                outcome="contacts_collected",
                method="none",
                evidence_json=json.dumps({"pages": len(crawl.pages), "complete": crawl.complete})[:1000],
            )
        )
        session.commit()  # per-business
    return counts


def recollect_businesses(session, area_key: str, business_ids: list[str]) -> dict:
    counts = {
        "attempted": 0,
        "contacts_added": 0,
        "contacts_reactivated": 0,
        "evidence_added": 0,
        "sites_skipped": 0,
        "incomplete_crawls": 0,
        "skipped_not_resolved": 0,
    }
    robots = RobotsCache()
    net.reset_pacing()
    now = datetime.now(timezone.utc)

    for business_id in business_ids:
        row = session.execute(
            text(
                """SELECT COALESCE(discovery_status,'') AS s, COALESCE(official_website,'') AS w
                   FROM business WHERE id = :i AND area_key = :a"""
            ),
            {"i": business_id, "a": area_key},
        ).mappings().first()
        if row is None:
            continue  # not in this area / unknown id -- silently ignored
        if row["s"] != "resolved" or not row["w"]:
            counts["skipped_not_resolved"] += 1
            continue

        counts["attempted"] += 1
        site = row["w"]

        if robots.decision(site) == "skip_site":
            counts["sites_skipped"] += 1
            session.add(
                DiscoveryLog(business_id=business_id, outcome="contacts_recollect", method="none", error="robots_skip")
            )
            session.commit()
            continue

        crawl = crawl_site(site, robots, config.OUTREACH_CRAWL_MAX_PAGES)

        before_active = session.execute(
            select(OutreachContact).where(
                OutreachContact.business_id == business_id, OutreachContact.active == True  # noqa: E712
            )
        ).scalars().all()

        stats = _ingest_page_contacts(session, business_id, site, crawl.pages)
        counts["contacts_added"] += stats["contacts_added"]
        counts["evidence_added"] += stats["evidence_added"]
        counts["contacts_reactivated"] += stats["reactivated"]

        # Deactivate previously-active contacts NOT re-found -- ONLY when the
        # crawl actually completed (no robots-skip, no fetch error, no
        # truncation) and produced pages. Absence from an incomplete crawl is
        # never treated as "gone".
        if crawl.complete and crawl.pages:
            for contact in before_active:
                if contact.email_normalized not in stats["found_norms"]:
                    contact.active = False
                    contact.stale_reason = "not_refound"
                    contact.deactivated_at = now
                    invalidate_contact_on_threads(
                        session, business_id, contact.id, reason="not_refound", event_kind="contact_stale"
                    )
        else:
            # partial crawl: any contacts found were still ingested/reactivated,
            # but absence is not proof of removal, so nothing is deactivated.
            counts["incomplete_crawls"] += 1

        session.execute(
            text("UPDATE business SET contacts_collected_at = :t WHERE id = :i"),
            {"t": _now_str(), "i": business_id},
        )
        session.add(
            DiscoveryLog(
                business_id=business_id,
                outcome="contacts_recollect",
                method="none",
                evidence_json=json.dumps({"pages": len(crawl.pages), "complete": crawl.complete})[:1000],
            )
        )
        session.commit()  # per-business
    return counts
