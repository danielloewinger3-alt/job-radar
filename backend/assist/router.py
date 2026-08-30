"""Assist / application-pack HTTP routes.

Contract:
* Successful JSON responses are wrapped ``{"schema_version": 1, ...}``.
* Deliberately-constructed errors that carry structured fields use a stable
  top-level body with a ``code`` (never nested under ``HTTPException.detail``).
* Plain not-found errors use FastAPI's standard ``{"detail": "..."}`` shape.
* Pack retrieval, answer editing, review and autofill export never require API
  keys; only generation and AI-assisted revision are key-gated.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlmodel import select

from backend import config
from backend.cv_text import extract_text as extract_cv_text
from backend.db import get_session
from backend.models import CV, Job, Profile, Project, utcnow
from backend.assist import pack as pack_mod
from backend.assist.limits import ANSWER_MAX_CHARS
from backend.assist.models import ApplicationPack
from backend.assist.pack import (
    QUESTION_BANK,
    QUESTION_BANK_BY_KEY,
    content_fingerprint,
    missing_ai_keys,
)
from backend.projectfiles.models import ProjectFile
from backend.tracker.models import TrackedApplication

router = APIRouter()

SCHEMA_VERSION = 1


class _CorruptPack(Exception):
    pass


def _loads(raw: str, expect_list: bool = False):
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise _CorruptPack()
    if expect_list and not isinstance(value, list):
        raise _CorruptPack()
    return value


def _corrupt_response(pack_id: int) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "schema_version": SCHEMA_VERSION,
            "code": "pack_data_corrupt",
            "pack_id": pack_id,
        },
    )


def _ai_unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "schema_version": SCHEMA_VERSION,
            "code": "ai_unavailable",
            "disabled": True,
            "missing_keys": missing_ai_keys(),
        },
    )


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class PackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cv_id: Optional[int] = None
    project_ids: Optional[list[int]] = None
    project_file_ids: Optional[list[int]] = None
    job_description: Optional[str] = None
    regenerate: bool = False


class AnswerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class ReviseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_notes: Optional[str] = None


# --------------------------------------------------------------------------- #
# Reference resolution + serialisation
# --------------------------------------------------------------------------- #
def _resolve_refs(session, cv_id, project_ids, project_file_ids) -> dict:
    cv_ref = None
    if cv_id is not None:
        present = session.get(CV, cv_id) is not None
        cv_ref = {"cv_id": cv_id, "status": "present" if present else "missing"}
    projects = []
    for pid in project_ids or []:
        present = session.get(Project, pid) is not None
        projects.append(
            {"project_id": pid, "status": "present" if present else "missing"}
        )
    files = []
    for fid in project_file_ids or []:
        f = session.get(ProjectFile, fid)
        files.append(
            {
                "file_id": fid,
                "status": "present" if f is not None else "missing",
                "ai_context_enabled": bool(f.ai_context_enabled) if f else False,
            }
        )
    return {"cv": cv_ref, "projects": projects, "project_files": files}


def _enrich_answers(answers: list[dict]) -> list[dict]:
    out = []
    for a in answers:
        q = QUESTION_BANK_BY_KEY.get(a.get("key"), {})
        out.append(
            {
                **a,
                "label": q.get("label", ""),
                "category": q.get("category", ""),
                "answer_kind": a.get("answer_kind", q.get("answer_kind", "standard")),
                "autofill_exportable": q.get("autofill_exportable", False),
            }
        )
    return out


def _pack_body(session, pack: ApplicationPack) -> dict:
    answers = _loads(pack.answers_json, expect_list=True)
    ctx = _loads(pack.context_summary_json)
    project_ids = _loads(pack.project_ids_json, expect_list=True)
    project_file_ids = _loads(pack.project_file_ids_json, expect_list=True)
    current_fp = content_fingerprint(pack.cover_letter, answers)
    review_valid = bool(
        pack.reviewed
        and pack.reviewed_fingerprint
        and pack.reviewed_fingerprint == current_fp
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "pack": {
            "pack_id": pack.id,
            "tracked_application_id": pack.tracked_application_id,
            "version": pack.version,
            "supersedes_pack_id": pack.supersedes_pack_id,
            "cover_letter": pack.cover_letter,
            "answers": _enrich_answers(answers),
            "context_summary": ctx,
            "cv_id": pack.cv_id,
            "project_ids": project_ids,
            "project_file_ids": project_file_ids,
            "generated_model": pack.generated_model,
            "generated_at": pack.generated_at,
            "reviewed": pack.reviewed,
            "reviewed_at": pack.reviewed_at,
            "review_valid": review_valid,
            "content_fingerprint": pack.content_fingerprint,
            "reviewed_fingerprint": pack.reviewed_fingerprint,
            "created_at": pack.created_at,
            "updated_at": pack.updated_at,
        },
        "references": _resolve_refs(
            session, pack.cv_id, project_ids, project_file_ids
        ),
    }


def _job_like_for(session, app: TrackedApplication, job_description: str = ""):
    job = session.get(Job, app.job_id) if app.job_id else None
    desc = job_description or (job.description_full if job is not None else "")
    return pack_mod._JobLike(app.role_title, app.company, desc)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/api/assist/questions")
def list_questions():
    return {"schema_version": SCHEMA_VERSION, "questions": QUESTION_BANK}


@router.post(
    "/api/tracked-applications/{tracked_application_id}/pack", status_code=201
)
def create_pack(tracked_application_id: int, body: PackCreate):
    if missing_ai_keys():
        return _ai_unavailable()
    with get_session() as session:
        app = session.get(TrackedApplication, tracked_application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="tracked application not found")

        cv = None
        if body.cv_id is not None:
            cv = session.get(CV, body.cv_id)
            if cv is None:
                raise HTTPException(status_code=404, detail="unknown cv_id")

        project_ids = list(body.project_ids or [])
        for pid in project_ids:
            if session.get(Project, pid) is None:
                raise HTTPException(
                    status_code=404, detail=f"unknown project_id {pid}"
                )

        context_files = []
        for fid in list(body.project_file_ids or []):
            f = session.get(ProjectFile, fid)
            if f is None or f.project_id not in project_ids:
                return JSONResponse(
                    status_code=409,
                    content={
                        "schema_version": SCHEMA_VERSION,
                        "code": "file_context_ineligible",
                        "file_id": fid,
                        "detail": "file not found or not in a selected project",
                    },
                )
            if not f.ai_context_enabled or f.extract_status not in ("ok", "truncated"):
                return JSONResponse(
                    status_code=409,
                    content={
                        "schema_version": SCHEMA_VERSION,
                        "code": "file_context_ineligible",
                        "file_id": fid,
                        "detail": "file is not enabled/readable for AI context",
                    },
                )
            context_files.append(f)

        current = (
            session.get(ApplicationPack, app.pack_id) if app.pack_id else None
        )
        if current is not None and not body.regenerate:
            return JSONResponse(
                status_code=409,
                content={
                    "schema_version": SCHEMA_VERSION,
                    "code": "pack_exists",
                    "pack_id": current.id,
                },
            )

        cv_text = ""
        if cv is not None:
            cv_text = extract_cv_text(config.UPLOAD_DIR / cv.filename) or ""
        profile = session.get(Profile, 1) or Profile(id=1)
        model_label = config.ANTHROPIC_MODEL

        cover_letter, answers, summary = pack_mod.build_pack(
            company=app.company,
            role_title=app.role_title,
            cv_text=cv_text,
            profile=profile,
            context_files=context_files,
            job_description=body.job_description or "",
            model_label=model_label,
        )
        fingerprint = content_fingerprint(cover_letter, answers)
        version = (current.version + 1) if current is not None else 1

        new_pack = ApplicationPack(
            tracked_application_id=app.id,
            version=version,
            supersedes_pack_id=current.id if current is not None else None,
            cover_letter=cover_letter,
            answers_json=json.dumps(answers),
            context_summary_json=json.dumps(summary),
            cv_id=body.cv_id,
            project_ids_json=json.dumps(project_ids),
            project_file_ids_json=json.dumps(list(body.project_file_ids or [])),
            generated_model=model_label,
            generated_at=utcnow(),
            reviewed=False,
            reviewed_at=None,
            reviewed_fingerprint=None,
            content_fingerprint=fingerprint,
        )
        session.add(new_pack)
        try:
            session.flush()
            app.pack_id = new_pack.id
            app.updated_at = utcnow()
            session.add(app)
            session.commit()
            session.refresh(new_pack)
        except Exception:
            session.rollback()
            return JSONResponse(
                status_code=500,
                content={
                    "schema_version": SCHEMA_VERSION,
                    "code": "pack_persist_failed",
                },
            )
        try:
            return _pack_body(session, new_pack)
        except _CorruptPack:
            return _corrupt_response(new_pack.id)


@router.get("/api/tracked-applications/{tracked_application_id}/pack")
def get_current_pack(tracked_application_id: int):
    with get_session() as session:
        app = session.get(TrackedApplication, tracked_application_id)
        if app is None or app.pack_id is None:
            raise HTTPException(
                status_code=404, detail="no pack for this application"
            )
        pack = session.get(ApplicationPack, app.pack_id)
        if pack is None:
            raise HTTPException(
                status_code=404, detail="no pack for this application"
            )
        try:
            return _pack_body(session, pack)
        except _CorruptPack:
            return _corrupt_response(pack.id)


@router.get("/api/packs/{pack_id}")
def get_pack(pack_id: int):
    with get_session() as session:
        pack = session.get(ApplicationPack, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="pack not found")
        try:
            return _pack_body(session, pack)
        except _CorruptPack:
            return _corrupt_response(pack_id)


@router.patch("/api/packs/{pack_id}/answers/{key}")
def edit_answer(pack_id: int, key: str, body: AnswerEdit):
    q = QUESTION_BANK_BY_KEY.get(key)
    if q is None:
        raise HTTPException(status_code=404, detail="unknown answer key")
    if len(body.value) > ANSWER_MAX_CHARS:
        raise HTTPException(status_code=422, detail="value too long")
    with get_session() as session:
        pack = session.get(ApplicationPack, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="pack not found")
        try:
            answers = _loads(pack.answers_json, expect_list=True)
        except _CorruptPack:
            return _corrupt_response(pack_id)

        entry = next((a for a in answers if a.get("key") == key), None)
        if entry is None:
            entry = {"key": key, "answer_kind": q["answer_kind"]}
            answers.append(entry)
        entry["value"] = body.value
        entry["source"] = "user_supplied"
        entry["answer_kind"] = q["answer_kind"]
        entry["edited_by_user"] = True
        entry["provenance"] = {"kind": "user_edit", "at": pack_mod.now_iso()}
        if q["answer_kind"] == "declared_answer":
            entry["status"] = "sourced" if body.value.strip() else "needs_input"
        else:
            entry["status"] = "sourced" if body.value.strip() else "needs_input"

        pack.answers_json = json.dumps(answers)
        pack.content_fingerprint = content_fingerprint(pack.cover_letter, answers)
        pack.reviewed = False
        pack.reviewed_at = None
        pack.reviewed_fingerprint = None
        pack.updated_at = utcnow()
        session.add(pack)
        session.commit()
        session.refresh(pack)
        try:
            return _pack_body(session, pack)
        except _CorruptPack:
            return _corrupt_response(pack_id)


@router.post("/api/packs/{pack_id}/revise")
def revise_pack(pack_id: int, body: ReviseIn):
    if missing_ai_keys():
        return _ai_unavailable()
    with get_session() as session:
        pack = session.get(ApplicationPack, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="pack not found")
        try:
            answers = _loads(pack.answers_json, expect_list=True)
        except _CorruptPack:
            return _corrupt_response(pack_id)
        app = session.get(TrackedApplication, pack.tracked_application_id)
        job_like = (
            _job_like_for(session, app) if app is not None
            else pack_mod._JobLike("", "", "")
        )
        revised = pack_mod.revise_with_feedback(
            pack.cover_letter, body.feedback, job_like
        )
        pack.cover_letter = revised
        pack.content_fingerprint = content_fingerprint(revised, answers)
        pack.reviewed = False
        pack.reviewed_at = None
        pack.reviewed_fingerprint = None
        pack.updated_at = utcnow()
        session.add(pack)
        session.commit()
        session.refresh(pack)
        try:
            return _pack_body(session, pack)
        except _CorruptPack:
            return _corrupt_response(pack_id)


@router.post("/api/packs/{pack_id}/review")
def review_pack(pack_id: int, body: Optional[ReviewIn] = None):
    with get_session() as session:
        pack = session.get(ApplicationPack, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="pack not found")
        try:
            answers = _loads(pack.answers_json, expect_list=True)
        except _CorruptPack:
            return _corrupt_response(pack_id)
        fingerprint = content_fingerprint(pack.cover_letter, answers)
        pack.content_fingerprint = fingerprint
        pack.reviewed = True
        pack.reviewed_at = utcnow()
        pack.reviewed_fingerprint = fingerprint
        if body is not None and body.reviewer_notes is not None:
            pack.reviewer_notes = body.reviewer_notes
        pack.updated_at = utcnow()
        session.add(pack)
        session.commit()
        session.refresh(pack)
        try:
            return _pack_body(session, pack)
        except _CorruptPack:
            return _corrupt_response(pack_id)


@router.get("/api/packs/{pack_id}/autofill")
def pack_autofill(pack_id: int):
    with get_session() as session:
        pack = session.get(ApplicationPack, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="pack not found")
        try:
            answers = _loads(pack.answers_json, expect_list=True)
            project_ids = _loads(pack.project_ids_json, expect_list=True)
            project_file_ids = _loads(pack.project_file_ids_json, expect_list=True)
        except _CorruptPack:
            return _corrupt_response(pack_id)

        by_key = {a.get("key"): a for a in answers}
        current_fp = content_fingerprint(pack.cover_letter, answers)
        review_valid = bool(
            pack.reviewed
            and pack.reviewed_fingerprint
            and pack.reviewed_fingerprint == current_fp
        )

        fields = []
        for q in QUESTION_BANK:
            if not q["autofill_exportable"]:
                continue
            a = by_key.get(q["key"], {})
            src = a.get("source", "none")
            raw_val = (a.get("value") or "")
            prov = a.get("provenance")

            if q["answer_kind"] == "declared_answer":
                if src == "user_supplied" and raw_val.strip():
                    out = (raw_val, "user_supplied", "sourced", prov)
                else:
                    out = ("", "none", "needs_input", None)
            else:  # standard
                if src == "user_supplied" and raw_val.strip():
                    out = (raw_val, "user_supplied", "sourced", prov)
                elif src == "profile" and raw_val.strip():
                    out = (raw_val, "profile", "sourced", prov)
                elif src == "generated" and raw_val.strip():
                    out = (raw_val, "generated", "generated_suggestion", prov)
                else:
                    out = ("", "none", "needs_input", None)

            value, source, status, provenance = out
            fields.append(
                {
                    "key": q["key"],
                    "label": q["label"],
                    "value": value,
                    "type": q["type"],
                    "autocomplete": q["autocomplete"],
                    "source": source,
                    "answer_kind": q["answer_kind"],
                    "status": status,
                    "provenance": provenance,
                    "sensitive": q["category"] == "contact",
                }
            )

        attachments = []
        disclaimers = [
            "Generated suggestions are drafts, not verified facts -- review before submitting.",
            "Declared answers (work authorisation, salary, start date, etc.) are "
            "exported only when you entered them yourself.",
        ]
        if not review_valid:
            disclaimers.insert(
                0,
                "This pack has not been human-reviewed, or its content changed "
                "after review.",
            )

        cv = session.get(CV, pack.cv_id) if pack.cv_id else None
        if pack.cv_id and cv is None:
            disclaimers.append(
                "The CV previously attached to this pack no longer exists."
            )
        elif cv is not None:
            attachments.append(
                {
                    "kind": "cv",
                    "cv_id": cv.id,
                    "filename": cv.original_name,
                    "download_url": f"/api/cvs/{cv.id}/file",
                    "content_type": "application/pdf",
                    "autofill": True,
                    "status": "present",
                }
            )
        elif pack.cv_id is None:
            disclaimers.append("No CV is attached to this pack.")

        for fid in project_file_ids:
            f = session.get(ProjectFile, fid)
            if f is None:
                continue
            attachments.append(
                {
                    "kind": "project_file",
                    "project_id": f.project_id,
                    "file_id": f.id,
                    "filename": f.original_name,
                    "download_url": (
                        f"/api/projects/{f.project_id}/files/{f.id}/download"
                    ),
                    "content_type": "application/octet-stream",
                    "autofill": False,
                    "status": "present",
                }
            )
        if any(x["kind"] == "project_file" for x in attachments):
            disclaimers.append(
                "Attachments other than the CV must not be auto-attached to "
                "file inputs."
            )
        if any(f["status"] == "needs_input" for f in fields):
            disclaimers.append(
                "Some answers are blank and marked needs_input; complete them "
                "in Job Radar first."
            )

        return {
            "schema_version": SCHEMA_VERSION,
            "pack_id": pack.id,
            "tracked_application_id": pack.tracked_application_id,
            "version": pack.version,
            "reviewed": review_valid,
            "reviewed_at": pack.reviewed_at,
            "generated_model": pack.generated_model,
            "generated_at": pack.generated_at,
            "cover_letter": pack.cover_letter,
            "fields": fields,
            "attachments": attachments,
            "references": _resolve_refs(
                session, pack.cv_id, project_ids, project_file_ids
            ),
            "disclaimers": disclaimers,
        }
