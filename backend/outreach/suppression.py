"""Email / domain suppression: normalization and matching.

Suppression rows are created ONLY by an explicit opt-out or an explicit manual
add (both audited). Matching is exact-or-subdomain for domains -- never a
substring test.
"""

from __future__ import annotations

import unicodedata

from sqlmodel import select

from backend.outreach.models import OutreachSuppression


def normalize_email(raw: str | None) -> str | None:
    s = unicodedata.normalize("NFKC", (raw or "")).strip().lower()
    if not s or s.count("@") != 1:
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
    return s


def normalize_domain(raw: str | None) -> str | None:
    s = unicodedata.normalize("NFKC", (raw or "")).strip().lower()
    s = s.lstrip("@").strip(".")
    if not s or "@" in s or "/" in s or any(ch.isspace() for ch in s) or "." not in s:
        return None
    if any(not part for part in s.split(".")):
        return None
    return s


def domain_matches(suppressed_domain: str, email_domain: str) -> bool:
    """Exact host or a true subdomain -- never a substring."""
    return email_domain == suppressed_domain or email_domain.endswith("." + suppressed_domain)


def is_suppressed(session, email_normalized: str | None) -> bool:
    if not email_normalized or "@" not in email_normalized:
        return False
    email_domain = email_normalized.split("@", 1)[1]
    rows = session.execute(
        select(OutreachSuppression.kind, OutreachSuppression.value)
    ).all()
    for kind, value in rows:
        if kind == "email" and value == email_normalized:
            return True
        if kind == "domain" and domain_matches(value, email_domain):
            return True
    return False
