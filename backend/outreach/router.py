"""Outreach + prospect-discovery HTTP routes.

Mounted prefix-less by ``backend.main`` via ``backend.features.feature_routers``.
All request bodies forbid unknown fields; all *handled* errors use
``{"detail": {"code", "message"}}``. Automatic request-schema validation still
returns FastAPI's standard 422 shape -- the frontend handles both.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlmodel import select

from backend.config import ANTHROPIC_MODEL, BUSINESS_CATEGORIES, PROSPECT_AREAS
from backend.db import get_session
from backend.models import Business
from backend.outreach import discovery, drafting, mailto as mailto_mod, mailto_txn, net
from backend.outreach.models import (
    ACTIVE_STAGES,
    OUTREACH_STAGES,
    TERMINAL_STAGES,
    ContactEvidence,
    DiscoveryLog,
    OutreachAttempt,
    OutreachContact,
    OutreachEvent,
    OutreachSuppression,
    OutreachThread,
)
from backend.outreach.suppression import domain_matches, is_suppressed, normalize_domain, normalize_email

router = APIRouter()

_NOW = lambda: datetime.now(timezone.utc)  # noqa: E731
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _sanitize(value: str, limit: int = 2000) -> str:
    return _CONTROL_RE.sub(" ", value or "")[:limit]


def _require_area(area: str) -> dict:
    cfg = PROSPECT_AREAS.get(area)
    if cfg is None:
        raise _err(404, "not_found", "unknown area")
    return cfg


def _sources_of(context_json: str) -> list[str]:
    try:
        data = json.loads(context_json or "{}")
        srcs = data.get("sources", [])
        return [str(s) for s in srcs] if isinstance(srcs, list) else []
    except (ValueError, TypeError):
        return []


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
def _thread_out(session, t: OutreachThread) -> dict:
    biz = session.get(Business, t.business_id)
    contact = session.get(OutreachContact, t.selected_contact_id) if t.selected_contact_id else None
    return {
        "id": t.id,
        "business_id": t.business_id,
        "business_name": biz.name if biz else "",
        "stage": t.stage,
        "selected_contact_id": t.selected_contact_id,
        "selected_contact_email": contact.email if contact else None,
        "selected_contact_suppressed": bool(contact.suppressed) if contact else False,
        "subject": t.subject,
        "body": t.body,
        "has_draft": bool(t.subject and t.body),
        "approved_at": t.approved_at,
        "mailto_generated_at": t.mailto_generated_at,
        "contacted_at": t.contacted_at,
        "replied_at": t.replied_at,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "context_sources": _sources_of(t.context_json),
    }


def _thread_summary(session, t: OutreachThread) -> dict:
    biz = session.get(Business, t.business_id)
    contact = session.get(OutreachContact, t.selected_contact_id) if t.selected_contact_id else None
    return {
        "id": t.id,
        "business_id": t.business_id,
        "business_name": biz.name if biz else "",
        "stage": t.stage,
        "selected_contact_email": contact.email if contact else None,
        "approved_at": t.approved_at,
        "mailto_generated_at": t.mailto_generated_at,
        "contacted_at": t.contacted_at,
        "replied_at": t.replied_at,
        "updated_at": t.updated_at,
    }


def _contact_out(session, c: OutreachContact) -> dict:
    biz = session.get(Business, c.business_id)
    ev = session.execute(
        select(ContactEvidence)
        .where(ContactEvidence.contact_id == c.id)
        .order_by(ContactEvidence.found_at.asc(), ContactEvidence.id.asc())
    ).scalars().all()
    return {
        "id": c.id,
        "business_id": c.business_id,
        "business_name": biz.name if biz else "",
        "email": c.email,
        "email_normalized": c.email_normalized,
        "classification": c.classification,
        "method": c.method,
        "suppressed": bool(c.suppressed),
        "active": bool(c.active),
        "verified_website": c.verified_website,
        "stale_reason": c.stale_reason,
        "first_seen_at": c.first_seen_at,
        "deactivated_at": c.deactivated_at,
        "evidence": [
            {
                "source_url": e.source_url,
                "method": e.method,
                "classification_at_source": e.classification_at_source,
                "page_kind": e.page_kind,
                "found_at": e.found_at,
            }
            for e in ev
        ],
    }


def _event_out(e: OutreachEvent) -> dict:
    return {"id": e.id, "thread_id": e.thread_id, "kind": e.kind, "detail": e.detail, "created_at": e.created_at}


def _attempt_out(a: OutreachAttempt) -> dict:
    return {
        "id": a.id,
        "business_id": a.business_id,
        "email_normalized": a.email_normalized,
        "thread_id": a.thread_id,
        "created_at": a.created_at,
        "cleared_at": a.cleared_at,
        "cleared_reason": a.cleared_reason,
    }


def _suppression_out(s: OutreachSuppression) -> dict:
    return {
        "id": s.id,
        "kind": s.kind,
        "value": s.value,
        "origin": s.origin,
        "thread_id": s.thread_id,
        "note": s.note,
        "created_at": s.created_at,
    }


_DISCOVERY_COLS = (
    "official_website",
    "website_confidence",
    "contact_page_url",
    "discovery_status",
    "discovery_error",
    "discovery_attempts",
)


def _discovery_out(row) -> dict:
    status = row["discovery_status"] or ""
    attempts = row["discovery_attempts"] or 0
    attempted_at = row["discovery_attempted_at"]
    eligible = (
        discovery.retry_eligible_at(attempts, attempted_at)
        if status == "transient_failure"
        else None
    )
    return {
        "business_id": row["id"],
        "name": row["name"],
        "official_website": row["official_website"] or "",
        "website_confidence": row["website_confidence"] or "",
        "contact_page_url": row["contact_page_url"] or "",
        "discovery_status": status,
        "discovery_error": row["discovery_error"] or "",
        "discovery_attempts": attempts,
        "discovery_attempted_at": discovery.parse_ts(attempted_at),
        "discovery_at": discovery.parse_ts(row["discovery_at"]),
        "contacts_collected_at": discovery.parse_ts(row["contacts_collected_at"]),
        "retry_eligible_at": eligible,
    }


# --------------------------------------------------------------------------- #
# Shared validation
# --------------------------------------------------------------------------- #
def _load_contact_for_thread(session, thread: OutreachThread, contact_id: int) -> OutreachContact:
    contact = session.get(OutreachContact, contact_id)
    if contact is None:
        raise _err(404, "not_found", "contact not found")
    if contact.business_id != thread.business_id:
        raise _err(409, "contact_business_mismatch", "contact belongs to a different business")
    if not contact.active:
        raise _err(409, "contact_stale", "contact is stale after rediscovery; pick a current one")
    if not normalize_email(contact.email_normalized):
        raise _err(400, "validation_error", "contact email is missing or invalid")
    if contact.suppressed or is_suppressed(session, contact.email_normalized):
        raise _err(409, "contact_suppressed", "contact email or domain is suppressed")
    return contact


def _selected_contact_or_400(session, thread: OutreachThread) -> OutreachContact:
    if not thread.selected_contact_id:
        raise _err(400, "validation_error", "no contact selected for this thread")
    return _load_contact_for_thread(session, thread, thread.selected_contact_id)


def _load_website_text(url: str) -> str:
    """Best-effort fetch of the business's own site text for the draft prompt.
    Monkeypatched in tests. Returns '' on any failure -- never raises."""
    if not url:
        return ""
    try:
        res = net.fetch(url, kind="html", max_bytes=net.MAX_HTML_BYTES)
    except (net.FetchError, net.UnsafeUrlError):
        return ""
    return discovery.visible_text(res.text)[: drafting.MAX_WEBSITE_EVIDENCE_CHARS]


def _apply_suppression(session, kind: str, value: str) -> list[int]:
    """Mark every matching contact suppressed and cascade to their threads."""
    affected: list[int] = []
    now = _NOW()
    contacts = session.execute(select(OutreachContact)).scalars().all()
    for c in contacts:
        dom = c.email_normalized.split("@", 1)[1] if "@" in c.email_normalized else ""
        hit = (kind == "email" and value == c.email_normalized) or (
            kind == "domain" and dom and domain_matches(value, dom)
        )
        if not hit:
            continue
        c.suppressed = True
        threads = session.execute(
            select(OutreachThread).where(OutreachThread.selected_contact_id == c.id)
        ).scalars().all()
        for t in threads:
            if t.stage in ("identified", "drafted", "approved"):
                if t.stage == "approved":
                    t.stage = "drafted"
                t.approved_at = None
                t.updated_at = now
                session.add(
                    OutreachEvent(
                        thread_id=t.id,
                        kind="contact_suppressed",
                        detail=json.dumps({"contact_id": c.id}),
                    )
                )
                affected.append(t.id)
            elif t.stage not in TERMINAL_STAGES:
                session.add(
                    OutreachEvent(
                        thread_id=t.id,
                        kind="contact_suppressed",
                        detail=json.dumps({"contact_id": c.id, "historical": True}),
                    )
                )
                affected.append(t.id)
    return affected


def _recompute_suppressed_flags(session) -> None:
    supps = session.execute(select(OutreachSuppression.kind, OutreachSuppression.value)).all()
    for c in session.execute(select(OutreachContact)).scalars().all():
        dom = c.email_normalized.split("@", 1)[1] if "@" in c.email_normalized else ""
        still = any(
            (k == "email" and v == c.email_normalized) or (k == "domain" and dom and domain_matches(v, dom))
            for k, v in supps
        )
        c.suppressed = still


def _clear_attempts(session, thread_id: int, reason: str) -> int:
    rows = session.execute(
        select(OutreachAttempt).where(
            OutreachAttempt.thread_id == thread_id, OutreachAttempt.cleared_at.is_(None)
        )
    ).scalars().all()
    now = _NOW()
    for a in rows:
        a.cleared_at = now
        a.cleared_reason = reason
    return len(rows)


# =========================================================================== #
# Prospect discovery / contacts
# =========================================================================== #
class DiscoverIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(5, ge=1, le=10)


class RediscoverIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_ids: Optional[list[str]] = Field(None, max_length=500)
    statuses: Optional[list[str]] = Field(None, max_length=8)
    include_resolved: bool = False


class CollectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(5, ge=1, le=10)


class RecollectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_ids: list[str] = Field(..., min_length=1, max_length=500)


_REDISCOVER_STATUSES = {"unresolved", "unsafe", "transient_failure", "resolved"}


@router.post("/api/prospects/{area}/discover")
def discover(area: str, body: DiscoverIn):
    cfg = _require_area(area)
    with get_session() as session:
        return discovery.discover_area(session, area, cfg.get("label", area), body.limit)


@router.post("/api/prospects/{area}/rediscover")
def rediscover(area: str, body: RediscoverIn):
    _require_area(area)
    if not body.business_ids and not body.statuses:
        raise _err(400, "validation_error", "provide business_ids and/or statuses")
    if body.statuses:
        bad = [s for s in body.statuses if s not in _REDISCOVER_STATUSES]
        if bad:
            raise _err(400, "validation_error", f"unknown status: {', '.join(bad)}")
    with get_session() as session:
        return discovery.rediscover(
            session, area, body.business_ids or [], body.statuses or [], body.include_resolved
        )


@router.post("/api/prospects/{area}/contacts/collect")
def collect_contacts(area: str, body: CollectIn):
    _require_area(area)
    with get_session() as session:
        return discovery.collect_area(session, area, body.limit)


@router.post("/api/prospects/{area}/contacts/recollect")
def recollect_contacts(area: str, body: RecollectIn):
    _require_area(area)
    with get_session() as session:
        return discovery.recollect_businesses(session, area, body.business_ids)


@router.get("/api/prospects/{area}/contacts")
def list_contacts(area: str, business_id: str | None = None, include_suppressed: bool = False):
    _require_area(area)
    with get_session() as session:
        query = (
            select(OutreachContact)
            .join(Business, Business.id == OutreachContact.business_id)
            .where(Business.area_key == area)
        )
        if business_id:
            query = query.where(OutreachContact.business_id == business_id)
        if not include_suppressed:
            query = query.where(OutreachContact.suppressed == False)  # noqa: E712
        query = query.order_by(OutreachContact.business_id.asc(), OutreachContact.id.asc())
        contacts = session.execute(query).scalars().all()
        return {"contacts": [_contact_out(session, c) for c in contacts]}


@router.get("/api/prospects/{area}/discovery")
def discovery_status(area: str, status: str | None = None):
    _require_area(area)
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT id, name,
                       official_website, website_confidence, contact_page_url,
                       COALESCE(discovery_status,'') AS discovery_status,
                       discovery_error, COALESCE(discovery_attempts,0) AS discovery_attempts,
                       discovery_attempted_at, discovery_at, contacts_collected_at
                FROM business WHERE area_key = :a
                ORDER BY id ASC
                """
            ),
            {"a": area},
        ).mappings().all()

        counts = {"resolved": 0, "unresolved": 0, "unsafe": 0, "transient_failure": 0, "pending": 0}
        out = []
        for row in rows:
            st = row["discovery_status"] or ""
            counts["pending" if st == "" else st] = counts.get("pending" if st == "" else st, 0) + 1
            if status is None or st == status or (status == "pending" and st == ""):
                out.append(_discovery_out(row))
        return {"businesses": out, "counts": counts}


# =========================================================================== #
# Outreach threads
# =========================================================================== #
class ThreadCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_id: str = Field(..., max_length=128)


class SelectContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_id: int


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_id: Optional[int] = None
    notes: Optional[str] = Field(None, max_length=4000)


class ReviseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str = Field(..., min_length=1, max_length=4000)


class StageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field("", max_length=2000)


class OptOutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["email", "domain"]
    note: str = Field("", max_length=500)


class SuppressionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["email", "domain"]
    value: str = Field(..., max_length=320)
    note: str = Field("", max_length=500)


def _active_thread_for(session, business_id: str) -> OutreachThread | None:
    return session.execute(
        select(OutreachThread).where(
            OutreachThread.business_id == business_id,
            OutreachThread.stage.notin_(TERMINAL_STAGES),
        )
    ).scalars().first()


@router.post("/api/outreach/threads")
def create_thread(body: ThreadCreateIn):
    with get_session() as session:
        if session.get(Business, body.business_id) is None:
            raise _err(404, "not_found", "unknown business")
        if _active_thread_for(session, body.business_id) is not None:
            raise _err(409, "active_thread_exists", "an active thread already exists for this business")
        thread = OutreachThread(business_id=body.business_id, stage="identified")
        session.add(thread)
        session.commit()
        session.refresh(thread)
        return _thread_out(session, thread)


@router.get("/api/outreach/threads")
def list_threads(
    stage: str | None = None,
    business_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    if stage is not None and stage not in OUTREACH_STAGES:
        raise _err(400, "validation_error", "unknown stage")
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with get_session() as session:
        query = select(OutreachThread)
        if stage is not None:
            query = query.where(OutreachThread.stage == stage)
        if business_id is not None:
            query = query.where(OutreachThread.business_id == business_id)
        total = len(session.execute(query).scalars().all())
        rows = session.execute(
            query.order_by(OutreachThread.updated_at.desc(), OutreachThread.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
        return {"threads": [_thread_summary(session, t) for t in rows], "total": total}


def _get_thread_or_404(session, thread_id: int) -> OutreachThread:
    thread = session.get(OutreachThread, thread_id)
    if thread is None:
        raise _err(404, "not_found", "thread not found")
    return thread


@router.get("/api/outreach/threads/{thread_id}")
def get_thread(thread_id: int):
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        contact = (
            session.get(OutreachContact, thread.selected_contact_id)
            if thread.selected_contact_id
            else None
        )
        events = session.execute(
            select(OutreachEvent)
            .where(OutreachEvent.thread_id == thread_id)
            .order_by(OutreachEvent.id.asc())
        ).scalars().all()
        attempts = session.execute(
            select(OutreachAttempt)
            .where(OutreachAttempt.thread_id == thread_id)
            .order_by(OutreachAttempt.id.asc())
        ).scalars().all()
        out = _thread_out(session, thread)
        out["contact"] = _contact_out(session, contact) if contact else None
        out["events"] = [_event_out(e) for e in events]
        out["attempts"] = [_attempt_out(a) for a in attempts]
        return out


@router.get("/api/outreach/threads/{thread_id}/events")
def list_events(thread_id: int):
    with get_session() as session:
        _get_thread_or_404(session, thread_id)
        events = session.execute(
            select(OutreachEvent)
            .where(OutreachEvent.thread_id == thread_id)
            .order_by(OutreachEvent.id.asc())
        ).scalars().all()
        return {"events": [_event_out(e) for e in events]}


@router.post("/api/outreach/threads/{thread_id}/select-contact")
def select_contact(thread_id: int, body: SelectContactIn):
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        if thread.stage not in ("identified", "drafted", "approved"):
            raise _err(409, "invalid_stage_transition", "cannot change contact at this stage")
        contact = _load_contact_for_thread(session, thread, body.contact_id)
        was = thread.stage
        thread.selected_contact_id = contact.id
        if thread.stage == "approved":
            thread.stage = "drafted"
            thread.approved_at = None
            session.add(
                OutreachEvent(
                    thread_id=thread_id,
                    kind="stage_change",
                    detail=json.dumps({"from": was, "to": "drafted", "reason": "contact_changed"}),
                )
            )
        else:
            session.add(
                OutreachEvent(
                    thread_id=thread_id, kind="note", detail=json.dumps({"selected_contact_id": contact.id})
                )
            )
        thread.updated_at = _NOW()
        session.commit()
        session.refresh(thread)
        return _thread_out(session, thread)


def _business_website(session, business_id: str) -> str:
    return session.execute(
        text("SELECT COALESCE(official_website,'') FROM business WHERE id = :i"), {"i": business_id}
    ).scalar() or ""


def _run_draft(session, thread: OutreachThread, contact: OutreachContact, notes: str | None, *, feedback: str | None):
    biz = session.get(Business, thread.business_id)
    cfg = None
    for area_key, area_cfg in PROSPECT_AREAS.items():
        if biz and biz.area_key == area_key:
            cfg = area_cfg
            break
    area_label = cfg.get("label", biz.area_key if biz else "") if cfg else (biz.area_key if biz else "")
    category = BUSINESS_CATEGORIES.get(biz.category, {}).get("label", biz.category) if biz else ""
    website_url = _business_website(session, thread.business_id)
    website_text = _load_website_text(website_url)

    result = drafting.generate_draft(
        business_name=biz.name if biz else thread.business_id,
        business_category=category,
        area_label=area_label,
        contact_email=contact.email,
        website_text=website_text,
        website_url=website_url,
        notes=notes,
        prior_subject=thread.subject or None if feedback else None,
        prior_body=thread.body or None if feedback else None,
        feedback=feedback,
    )
    body_text = result.body
    if drafting.COURTESY_OPT_OUT not in body_text:
        body_text = body_text.rstrip() + "\n\n" + drafting.COURTESY_OPT_OUT

    thread.subject = result.subject
    thread.body = body_text
    thread.context = _sanitize(notes or "", 4000)
    thread.context_json = json.dumps(
        {
            "sources": result.sources,
            "website_url": website_url,
            "client_notes_included": bool(notes),
            "caps": {
                "website_evidence_chars": drafting.MAX_WEBSITE_EVIDENCE_CHARS,
                "notes_chars": drafting.MAX_NOTES_CHARS,
                "feedback_chars": drafting.MAX_FEEDBACK_CHARS,
            },
            "model": ANTHROPIC_MODEL,
            "generated_at": _NOW().isoformat(),
        }
    )
    thread.approved_at = None
    thread.stage = "drafted"
    thread.updated_at = _NOW()


@router.post("/api/outreach/threads/{thread_id}/draft")
def draft(thread_id: int, body: DraftIn):
    if not drafting.drafting_enabled():
        raise _err(400, "drafting_disabled", "drafting is disabled: ANTHROPIC_API_KEY is not configured")
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        if thread.stage not in ("identified", "drafted"):
            raise _err(409, "invalid_stage_transition", "draft is only allowed from identified/drafted")
        if body.contact_id is not None:
            contact = _load_contact_for_thread(session, thread, body.contact_id)
            thread.selected_contact_id = contact.id
        contact = _selected_contact_or_400(session, thread)
        try:
            _run_draft(session, thread, contact, body.notes, feedback=None)
        except drafting.DraftUnavailable as exc:
            raise _err(502, "ai_unusable_response", "the drafting model returned an unusable response") from exc
        session.add(OutreachEvent(thread_id=thread_id, kind="draft", detail=json.dumps({"contact_id": contact.id})))
        session.commit()
        session.refresh(thread)
        return _thread_out(session, thread)


@router.post("/api/outreach/threads/{thread_id}/revise")
def revise(thread_id: int, body: ReviseIn):
    if not drafting.drafting_enabled():
        raise _err(400, "drafting_disabled", "drafting is disabled: ANTHROPIC_API_KEY is not configured")
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        if thread.stage not in ("drafted", "approved"):
            raise _err(409, "invalid_stage_transition", "revise is only allowed from drafted/approved")
        contact = _selected_contact_or_400(session, thread)
        try:
            _run_draft(session, thread, contact, thread.context or None, feedback=body.feedback)
        except drafting.DraftUnavailable as exc:
            raise _err(502, "ai_unusable_response", "the drafting model returned an unusable response") from exc
        session.add(
            OutreachEvent(thread_id=thread_id, kind="revise", detail=json.dumps({"feedback_len": len(body.feedback)}))
        )
        session.commit()
        session.refresh(thread)
        return _thread_out(session, thread)


@router.post("/api/outreach/threads/{thread_id}/approve")
def approve(thread_id: int):
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        if thread.stage != "drafted":
            raise _err(409, "invalid_stage_transition", "approve is only allowed from drafted")
        if not (thread.subject and thread.body):
            raise _err(400, "empty_draft", "draft has no subject/body to approve")
        _selected_contact_or_400(session, thread)  # re-validates active + suppression + ownership
        thread.approved_at = _NOW()
        thread.stage = "approved"
        thread.updated_at = _NOW()
        session.add(OutreachEvent(thread_id=thread_id, kind="approve", detail=""))
        session.commit()
        session.refresh(thread)
        return _thread_out(session, thread)


_MAILTO_STATUS = {
    "not_found": (404, "thread not found"),
    "approval_required": (409, "an approved current draft is required"),
    "contact_stale": (409, "selected contact is stale; pick a current one"),
    "contact_business_mismatch": (409, "selected contact belongs to a different business"),
    "contact_suppressed": (409, "selected contact email or domain is suppressed"),
    "duplicate_attempt": (409, "an uncleared outreach attempt already exists for this business/email"),
    "db_locked": (409, "could not acquire a write lock; retry"),
}


@router.post("/api/outreach/threads/{thread_id}/mailto")
def mailto(thread_id: int):
    try:
        res = mailto_txn.create_mailto_attempt(thread_id)
    except mailto_txn.MailtoTxnError as exc:
        status, message = _MAILTO_STATUS.get(exc.code, (409, exc.code))
        raise _err(status, exc.code, message) from exc

    url = mailto_mod.build_mailto_url(res["email"], res["subject"], res["body"])
    with get_session() as session:
        thread = session.get(OutreachThread, thread_id)
        return {"mailto_url": url, "thread": _thread_out(session, thread)}


@router.post("/api/outreach/threads/{thread_id}/reply")
def reply(thread_id: int, body: ReplyIn = ReplyIn()):
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        if thread.stage != "contacted":
            raise _err(409, "invalid_stage_transition", "reply is only allowed from contacted")
        thread.stage = "replied"
        thread.replied_at = _NOW()
        thread.updated_at = _NOW()
        _clear_attempts(session, thread_id, "reply")
        session.add(
            OutreachEvent(
                thread_id=thread_id,
                kind="reply_logged",
                detail=json.dumps({"note": _sanitize(body.note, 2000)}),  # stored verbatim, never parsed
            )
        )
        session.commit()
        session.refresh(thread)
        return _thread_out(session, thread)


_ADMIN_STAGE_TRANSITIONS = {
    ("replied", "meeting"),
    ("meeting", "closed_won"),
    ("meeting", "closed_lost"),
}


@router.post("/api/outreach/threads/{thread_id}/stage")
def set_stage(thread_id: int, body: StageIn):
    if body.stage not in OUTREACH_STAGES:
        raise _err(400, "validation_error", "unknown stage")
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        cur = thread.stage
        allowed = (cur, body.stage) in _ADMIN_STAGE_TRANSITIONS or (
            body.stage == "closed_lost" and cur in ACTIVE_STAGES
        )
        if not allowed:
            raise _err(
                409,
                "invalid_stage_transition",
                "the /stage endpoint only performs administrative transitions",
            )
        thread.stage = body.stage
        thread.updated_at = _NOW()
        session.add(
            OutreachEvent(thread_id=thread_id, kind="stage_change", detail=json.dumps({"from": cur, "to": body.stage}))
        )
        session.commit()
        session.refresh(thread)
        return _thread_out(session, thread)


@router.post("/api/outreach/threads/{thread_id}/reopen")
def reopen(thread_id: int):
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        if thread.stage not in TERMINAL_STAGES:
            raise _err(409, "invalid_stage_transition", "reopen is only allowed from a closed stage")
        from_stage = thread.stage
        thread.stage = "drafted"
        thread.approved_at = None
        thread.updated_at = _NOW()
        _clear_attempts(session, thread_id, "reopen")
        session.add(
            OutreachEvent(thread_id=thread_id, kind="reopen", detail=json.dumps({"from": from_stage}))
        )
        session.commit()
        session.refresh(thread)
        return _thread_out(session, thread)


@router.post("/api/outreach/threads/{thread_id}/opt-out")
def opt_out(thread_id: int, body: OptOutIn):
    with get_session() as session:
        thread = _get_thread_or_404(session, thread_id)
        contact = (
            session.get(OutreachContact, thread.selected_contact_id)
            if thread.selected_contact_id
            else None
        )
        if contact is None:
            raise _err(400, "validation_error", "thread has no selected contact to opt out")
        email_norm = normalize_email(contact.email_normalized)
        if not email_norm:
            raise _err(400, "validation_error", "selected contact email is invalid")
        value = email_norm if body.scope == "email" else normalize_domain(email_norm.split("@", 1)[1])
        if not value:
            raise _err(400, "validation_error", "could not derive a suppression value")

        existing = session.execute(
            select(OutreachSuppression).where(
                OutreachSuppression.kind == body.scope, OutreachSuppression.value == value
            )
        ).scalars().first()
        if existing is None:
            supp = OutreachSuppression(
                kind=body.scope,
                value=value,
                origin="opt_out",
                thread_id=thread_id,
                note=_sanitize(body.note, 500),
            )
            session.add(supp)
            session.flush()
        else:
            supp = existing

        affected = _apply_suppression(session, body.scope, value)
        thread.stage = "closed_lost"
        thread.updated_at = _NOW()
        session.add(OutreachEvent(thread_id=thread_id, kind="opt_out", detail=json.dumps({"scope": body.scope, "value": value})))
        session.add(OutreachEvent(thread_id=thread_id, kind="stage_change", detail=json.dumps({"to": "closed_lost", "reason": "opt_out"})))
        session.commit()
        session.refresh(thread)
        return {
            "suppression": _suppression_out(supp),
            "thread": _thread_out(session, thread),
            "affected_threads": sorted(set(affected)),
        }


@router.post("/api/outreach/suppressions")
def create_suppression(body: SuppressionCreateIn):
    value = normalize_email(body.value) if body.kind == "email" else normalize_domain(body.value)
    if not value:
        raise _err(400, "validation_error", f"invalid {body.kind} value")
    with get_session() as session:
        existing = session.execute(
            select(OutreachSuppression).where(
                OutreachSuppression.kind == body.kind, OutreachSuppression.value == value
            )
        ).scalars().first()
        if existing is None:
            supp = OutreachSuppression(kind=body.kind, value=value, origin="manual", note=_sanitize(body.note, 500))
            session.add(supp)
            session.flush()
        else:
            supp = existing
        _apply_suppression(session, body.kind, value)
        session.add(
            OutreachEvent(
                thread_id=None,
                kind="suppression_created",
                detail=json.dumps({"kind": body.kind, "value": value, "origin": "manual"}),
            )
        )
        session.commit()
        session.refresh(supp)
        return _suppression_out(supp)


@router.get("/api/outreach/suppressions")
def list_suppressions(kind: str | None = None, q: str | None = None):
    with get_session() as session:
        query = select(OutreachSuppression)
        if kind is not None:
            query = query.where(OutreachSuppression.kind == kind)
        if q:
            query = query.where(OutreachSuppression.value.contains(q))
        rows = session.execute(query.order_by(OutreachSuppression.id.asc())).scalars().all()
        return {"suppressions": [_suppression_out(s) for s in rows]}


@router.delete("/api/outreach/suppressions/{suppression_id}")
def delete_suppression(suppression_id: int):
    with get_session() as session:
        supp = session.get(OutreachSuppression, suppression_id)
        if supp is None:
            raise _err(404, "not_found", "suppression not found")
        detail = {
            "origin": supp.origin,
            "kind": supp.kind,
            "value": supp.value,
            "was_opt_out": supp.origin == "opt_out",
            "thread_id": supp.thread_id,
        }
        session.delete(supp)
        session.flush()
        _recompute_suppressed_flags(session)  # does NOT restore approved_at anywhere
        session.add(OutreachEvent(thread_id=supp.thread_id, kind="suppression_deleted", detail=json.dumps(detail)))
        session.commit()
        return {"deleted": suppression_id}


@router.get("/api/outreach/pipeline")
def pipeline():
    with get_session() as session:
        threads = session.execute(select(OutreachThread)).scalars().all()
        stages = {s: 0 for s in OUTREACH_STAGES}
        for t in threads:
            stages[t.stage] = stages.get(t.stage, 0) + 1
        ordered = sorted(threads, key=lambda t: (t.updated_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        return {"stages": stages, "threads": [_thread_summary(session, t) for t in ordered]}
