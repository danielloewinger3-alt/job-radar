"""Application-tracker HTTP routes.

Contract notes:
* Every *successful* JSON response is wrapped ``{"schema_version": 1, ...}``.
* Deliberately-constructed error responses that carry structured fields use a
  stable top-level body (never nested under ``HTTPException.detail``): the
  duplicate-job 409 returns ``{"schema_version": 1, "code": "already_tracked",
  "tracked_application_id": ..., "archived": ...}``.
* Plain "not found" style errors use FastAPI's standard ``{"detail": "..."}``
  shape, and automatic request-validation failures keep FastAPI's standard 422
  shape. Clients must not depend on ``schema_version`` in error bodies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, select

from backend.db import get_session
from backend.models import CV, Application, Job, Project, utcnow
from backend.tracker.models import (
    EventKind,
    TrackedApplication,
    TrackedApplicationContact,
    TrackedApplicationEvent,
    TrackedApplicationProjectLink,
    TrackedStage,
)

router = APIRouter()

SCHEMA_VERSION = 1

_CALENDAR_TYPE_RANK = {"next_action": 0, "deadline": 1, "interview": 2}


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #
def _app_dict(row: TrackedApplication) -> dict:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "legacy_application_id": row.legacy_application_id,
        "pack_id": row.pack_id,
        "company": row.company,
        "role_title": row.role_title,
        "cv_id": row.cv_id,
        "stage": row.stage,
        "origin": row.origin,
        "application_date": row.application_date,
        "next_action": row.next_action,
        "next_action_due": row.next_action_due,
        "notes": row.notes,
        "archived": row.archived,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _event_dict(row: TrackedApplicationEvent) -> dict:
    return {
        "id": row.id,
        "tracked_application_id": row.tracked_application_id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "occurs_at": row.occurs_at,
        "from_stage": row.from_stage,
        "to_stage": row.to_stage,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _contact_dict(row: TrackedApplicationContact) -> dict:
    return {
        "id": row.id,
        "tracked_application_id": row.tracked_application_id,
        "name": row.name,
        "contact_role": row.contact_role,
        "email": row.email,
        "phone": row.phone,
        "company": row.company,
        "notes": row.notes,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _already_tracked(row: TrackedApplication) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "schema_version": SCHEMA_VERSION,
            "code": "already_tracked",
            "tracked_application_id": row.id,
            "archived": bool(row.archived),
        },
    )


def _find_by_job_id(session, job_id: str) -> Optional[TrackedApplication]:
    return session.exec(
        select(TrackedApplication).where(TrackedApplication.job_id == job_id)
    ).first()


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class TrackedApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: Optional[str] = None
    company: Optional[str] = None
    role_title: Optional[str] = None
    cv_id: Optional[int] = None
    legacy_application_id: Optional[int] = None
    stage: Optional[TrackedStage] = None
    origin: Optional[str] = None
    project_ids: Optional[list[int]] = None
    application_date: Optional[datetime] = None
    next_action: Optional[str] = None
    next_action_due: Optional[datetime] = None
    notes: Optional[str] = None


class TrackedApplicationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Optional[str] = None
    role_title: Optional[str] = None
    cv_id: Optional[int] = None
    origin: Optional[str] = None
    application_date: Optional[datetime] = None
    next_action: Optional[str] = None
    next_action_due: Optional[datetime] = None
    notes: Optional[str] = None
    archived: Optional[bool] = None


class StageChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_stage: TrackedStage
    note: Optional[str] = None


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EventKind
    title: Optional[str] = None
    body: Optional[str] = None
    occurs_at: Optional[datetime] = None


class EventPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    body: Optional[str] = None
    occurs_at: Optional[datetime] = None


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    contact_role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None


class ContactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    contact_role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None


class ProjectLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int


# --------------------------------------------------------------------------- #
# Collection + create
# --------------------------------------------------------------------------- #
@router.get("/api/tracked-applications")
def list_tracked_applications(
    stage: Optional[TrackedStage] = None,
    archived: bool = False,
    search: Optional[str] = None,
):
    with get_session() as session:
        query = select(TrackedApplication).where(
            TrackedApplication.archived == archived
        )
        if stage is not None:
            query = query.where(TrackedApplication.stage == stage.value)
        if search:
            needle = f"%{search.lower()}%"
            query = query.where(
                func.lower(TrackedApplication.company).like(needle)
                | func.lower(TrackedApplication.role_title).like(needle)
            )
        query = query.order_by(TrackedApplication.updated_at.desc())
        rows = session.exec(query).all()
        return {
            "schema_version": SCHEMA_VERSION,
            "tracked_applications": [_app_dict(r) for r in rows],
        }


@router.post("/api/tracked-applications", status_code=201)
def create_tracked_application(body: TrackedApplicationCreate):
    with get_session() as session:
        job = None
        if body.job_id is not None:
            job = session.get(Job, body.job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="unknown job_id")
            existing = _find_by_job_id(session, body.job_id)
            if existing is not None:
                return _already_tracked(existing)

        if job is None:
            if not (body.company or "").strip() or not (body.role_title or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail="company and role_title are required for a manual entry",
                )

        if body.cv_id is not None and session.get(CV, body.cv_id) is None:
            raise HTTPException(status_code=404, detail="unknown cv_id")
        if (
            body.legacy_application_id is not None
            and session.get(Application, body.legacy_application_id) is None
        ):
            raise HTTPException(
                status_code=404, detail="unknown legacy_application_id"
            )
        project_ids = list(body.project_ids or [])
        for pid in project_ids:
            if session.get(Project, pid) is None:
                raise HTTPException(
                    status_code=404, detail=f"unknown project_id {pid}"
                )

        if job is not None:
            company = job.company
            role_title = job.title
            origin = body.origin or "job"
        else:
            company = body.company.strip()
            role_title = body.role_title.strip()
            origin = body.origin or "manual"

        row = TrackedApplication(
            job_id=body.job_id,
            legacy_application_id=body.legacy_application_id,
            company=company,
            role_title=role_title,
            cv_id=body.cv_id,
            stage=(body.stage.value if body.stage else TrackedStage.interested.value),
            origin=origin,
            application_date=body.application_date,
            next_action=body.next_action or "",
            next_action_due=body.next_action_due,
            notes=body.notes or "",
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            if body.job_id is None:
                raise
            existing = session.exec(
                select(TrackedApplication).where(
                    TrackedApplication.job_id == body.job_id
                )
            ).first()
            if existing is None:
                raise
            return _already_tracked(existing)

        for pid in project_ids:
            session.add(
                TrackedApplicationProjectLink(
                    tracked_application_id=row.id, project_id=pid
                )
            )
        session.commit()
        session.refresh(row)
        return {
            "schema_version": SCHEMA_VERSION,
            "tracked_application": _app_dict(row),
        }


# --------------------------------------------------------------------------- #
# Calendar (declared before /{id} so the static segment wins)
# --------------------------------------------------------------------------- #
def _parse_tz_aware(label: str, raw: Optional[str]) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"{label} is not valid ISO-8601"
        )
    if dt.tzinfo is None:
        raise HTTPException(
            status_code=422, detail=f"{label} must be timezone-aware"
        )
    return dt


@router.get("/api/tracked-applications/calendar")
def calendar(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    include_archived: bool = False,
):
    lo = _parse_tz_aware("from", from_)
    hi = _parse_tz_aware("to", to)
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(status_code=422, detail="invalid range: from > to")

    with get_session() as session:
        apps = session.exec(select(TrackedApplication)).all()
        by_id = {a.id: a for a in apps}
        entries = []
        for a in apps:
            if a.archived and not include_archived:
                continue
            if a.next_action_due is not None:
                entries.append(
                    {
                        "type": "next_action",
                        "tracked_application_id": a.id,
                        "event_id": None,
                        "company": a.company,
                        "role_title": a.role_title,
                        "stage": a.stage,
                        "title": a.next_action,
                        "when": a.next_action_due,
                    }
                )
        events = session.exec(
            select(TrackedApplicationEvent).where(
                TrackedApplicationEvent.kind.in_(["deadline", "interview"])
            )
        ).all()
        for e in events:
            if e.occurs_at is None:
                continue
            a = by_id.get(e.tracked_application_id)
            if a is None:
                continue
            if a.archived and not include_archived:
                continue
            entries.append(
                {
                    "type": e.kind,
                    "tracked_application_id": a.id,
                    "event_id": e.id,
                    "company": a.company,
                    "role_title": a.role_title,
                    "stage": a.stage,
                    "title": e.title,
                    "when": e.occurs_at,
                }
            )

        def _in_range(w: datetime) -> bool:
            if lo is not None and w < lo:
                return False
            if hi is not None and w > hi:
                return False
            return True

        entries = [e for e in entries if _in_range(e["when"])]
        entries.sort(
            key=lambda e: (
                e["when"],
                _CALENDAR_TYPE_RANK[e["type"]],
                e["tracked_application_id"],
                e["event_id"] if e["event_id"] is not None else -1,
                e["title"] or "",
            )
        )
        return {"schema_version": SCHEMA_VERSION, "entries": entries}


# --------------------------------------------------------------------------- #
# Single resource
# --------------------------------------------------------------------------- #
def _get_app_or_404(session, tracked_application_id: int) -> TrackedApplication:
    row = session.get(TrackedApplication, tracked_application_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tracked application not found")
    return row


@router.get("/api/tracked-applications/{tracked_application_id}")
def get_tracked_application(tracked_application_id: int):
    with get_session() as session:
        row = _get_app_or_404(session, tracked_application_id)
        events = session.exec(
            select(TrackedApplicationEvent)
            .where(
                TrackedApplicationEvent.tracked_application_id
                == tracked_application_id
            )
            .order_by(TrackedApplicationEvent.created_at)
        ).all()
        contacts = session.exec(
            select(TrackedApplicationContact)
            .where(
                TrackedApplicationContact.tracked_application_id
                == tracked_application_id
            )
            .order_by(TrackedApplicationContact.created_at)
        ).all()
        links = session.exec(
            select(TrackedApplicationProjectLink).where(
                TrackedApplicationProjectLink.tracked_application_id
                == tracked_application_id
            )
        ).all()
        return {
            "schema_version": SCHEMA_VERSION,
            "tracked_application": _app_dict(row),
            "events": [_event_dict(e) for e in events],
            "contacts": [_contact_dict(c) for c in contacts],
            "project_ids": [link.project_id for link in links],
        }


@router.patch("/api/tracked-applications/{tracked_application_id}")
def patch_tracked_application(tracked_application_id: int, body: TrackedApplicationPatch):
    updates = body.model_dump(exclude_unset=True)
    with get_session() as session:
        row = _get_app_or_404(session, tracked_application_id)
        if "cv_id" in updates and updates["cv_id"] is not None:
            if session.get(CV, updates["cv_id"]) is None:
                raise HTTPException(status_code=404, detail="unknown cv_id")
        for key, value in updates.items():
            setattr(row, key, value)
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "schema_version": SCHEMA_VERSION,
            "tracked_application": _app_dict(row),
        }


@router.delete("/api/tracked-applications/{tracked_application_id}")
def archive_tracked_application(tracked_application_id: int):
    with get_session() as session:
        row = _get_app_or_404(session, tracked_application_id)
        if not row.archived:
            row.archived = True
            row.updated_at = utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
        return {
            "schema_version": SCHEMA_VERSION,
            "tracked_application": _app_dict(row),
            "archived": True,
        }


@router.post("/api/tracked-applications/{tracked_application_id}/stage")
def change_stage(tracked_application_id: int, body: StageChangeIn):
    with get_session() as session:
        row = _get_app_or_404(session, tracked_application_id)
        target = body.to_stage.value
        if row.stage == target:
            return {
                "schema_version": SCHEMA_VERSION,
                "unchanged": True,
                "tracked_application": _app_dict(row),
                "event": None,
            }
        previous = row.stage
        row.stage = target
        row.updated_at = utcnow()
        event = TrackedApplicationEvent(
            tracked_application_id=row.id,
            kind=EventKind.stage_change.value,
            body=body.note or "",
            from_stage=previous,
            to_stage=target,
        )
        session.add(row)
        session.add(event)
        session.commit()
        session.refresh(row)
        session.refresh(event)
        return {
            "schema_version": SCHEMA_VERSION,
            "unchanged": False,
            "tracked_application": _app_dict(row),
            "event": _event_dict(event),
        }


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
@router.post(
    "/api/tracked-applications/{tracked_application_id}/events", status_code=201
)
def create_event(tracked_application_id: int, body: EventCreate):
    if body.kind == EventKind.stage_change:
        raise HTTPException(
            status_code=400,
            detail="stage_change events are created via the stage endpoint",
        )
    with get_session() as session:
        _get_app_or_404(session, tracked_application_id)
        event = TrackedApplicationEvent(
            tracked_application_id=tracked_application_id,
            kind=body.kind.value,
            title=body.title or "",
            body=body.body or "",
            occurs_at=body.occurs_at,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return {"schema_version": SCHEMA_VERSION, "event": _event_dict(event)}


def _get_event_or_404(session, tracked_application_id: int, event_id: int):
    event = session.get(TrackedApplicationEvent, event_id)
    if event is None or event.tracked_application_id != tracked_application_id:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@router.patch(
    "/api/tracked-applications/{tracked_application_id}/events/{event_id}"
)
def patch_event(tracked_application_id: int, event_id: int, body: EventPatch):
    updates = body.model_dump(exclude_unset=True)
    with get_session() as session:
        event = _get_event_or_404(session, tracked_application_id, event_id)
        for key, value in updates.items():
            if key in ("title", "body") and value is None:
                value = ""
            setattr(event, key, value)
        event.updated_at = utcnow()
        session.add(event)
        session.commit()
        session.refresh(event)
        return {"schema_version": SCHEMA_VERSION, "event": _event_dict(event)}


@router.delete(
    "/api/tracked-applications/{tracked_application_id}/events/{event_id}"
)
def delete_event(tracked_application_id: int, event_id: int):
    with get_session() as session:
        event = _get_event_or_404(session, tracked_application_id, event_id)
        session.delete(event)
        session.commit()
        return {
            "schema_version": SCHEMA_VERSION,
            "deleted": True,
            "event_id": event_id,
        }


# --------------------------------------------------------------------------- #
# Contacts
# --------------------------------------------------------------------------- #
@router.post(
    "/api/tracked-applications/{tracked_application_id}/contacts", status_code=201
)
def create_contact(tracked_application_id: int, body: ContactCreate):
    with get_session() as session:
        _get_app_or_404(session, tracked_application_id)
        contact = TrackedApplicationContact(
            tracked_application_id=tracked_application_id,
            name=body.name,
            contact_role=body.contact_role or "",
            email=body.email or "",
            phone=body.phone or "",
            company=body.company or "",
            notes=body.notes or "",
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)
        return {"schema_version": SCHEMA_VERSION, "contact": _contact_dict(contact)}


def _get_contact_or_404(session, tracked_application_id: int, contact_id: int):
    contact = session.get(TrackedApplicationContact, contact_id)
    if contact is None or contact.tracked_application_id != tracked_application_id:
        raise HTTPException(status_code=404, detail="contact not found")
    return contact


@router.patch(
    "/api/tracked-applications/{tracked_application_id}/contacts/{contact_id}"
)
def patch_contact(tracked_application_id: int, contact_id: int, body: ContactPatch):
    updates = body.model_dump(exclude_unset=True)
    with get_session() as session:
        contact = _get_contact_or_404(session, tracked_application_id, contact_id)
        for key, value in updates.items():
            if value is not None:
                setattr(contact, key, value)
        contact.updated_at = utcnow()
        session.add(contact)
        session.commit()
        session.refresh(contact)
        return {"schema_version": SCHEMA_VERSION, "contact": _contact_dict(contact)}


@router.delete(
    "/api/tracked-applications/{tracked_application_id}/contacts/{contact_id}"
)
def delete_contact(tracked_application_id: int, contact_id: int):
    with get_session() as session:
        contact = _get_contact_or_404(session, tracked_application_id, contact_id)
        session.delete(contact)
        session.commit()
        return {
            "schema_version": SCHEMA_VERSION,
            "deleted": True,
            "contact_id": contact_id,
        }


# --------------------------------------------------------------------------- #
# Project links
# --------------------------------------------------------------------------- #
@router.get("/api/tracked-applications/{tracked_application_id}/projects")
def list_project_links(tracked_application_id: int):
    with get_session() as session:
        _get_app_or_404(session, tracked_application_id)
        links = session.exec(
            select(TrackedApplicationProjectLink).where(
                TrackedApplicationProjectLink.tracked_application_id
                == tracked_application_id
            )
        ).all()
        out = []
        for link in links:
            project = session.get(Project, link.project_id)
            out.append(
                {
                    "project_id": link.project_id,
                    "title": project.title if project is not None else None,
                }
            )
        return {"schema_version": SCHEMA_VERSION, "projects": out}


@router.post("/api/tracked-applications/{tracked_application_id}/projects")
def link_project(tracked_application_id: int, body: ProjectLinkIn):
    with get_session() as session:
        _get_app_or_404(session, tracked_application_id)
        if session.get(Project, body.project_id) is None:
            raise HTTPException(status_code=404, detail="unknown project_id")
        existing = session.exec(
            select(TrackedApplicationProjectLink).where(
                TrackedApplicationProjectLink.tracked_application_id
                == tracked_application_id,
                TrackedApplicationProjectLink.project_id == body.project_id,
            )
        ).first()
        if existing is not None:
            return {
                "schema_version": SCHEMA_VERSION,
                "linked": True,
                "already_linked": True,
            }
        session.add(
            TrackedApplicationProjectLink(
                tracked_application_id=tracked_application_id,
                project_id=body.project_id,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return {
                "schema_version": SCHEMA_VERSION,
                "linked": True,
                "already_linked": True,
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "linked": True,
            "already_linked": False,
        }


@router.delete(
    "/api/tracked-applications/{tracked_application_id}/projects/{project_id}"
)
def unlink_project(tracked_application_id: int, project_id: int):
    with get_session() as session:
        _get_app_or_404(session, tracked_application_id)
        link = session.exec(
            select(TrackedApplicationProjectLink).where(
                TrackedApplicationProjectLink.tracked_application_id
                == tracked_application_id,
                TrackedApplicationProjectLink.project_id == project_id,
            )
        ).first()
        if link is not None:
            session.delete(link)
            session.commit()
        return {"schema_version": SCHEMA_VERSION, "deleted": True}
