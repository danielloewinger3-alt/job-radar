"""Project-file HTTP routes.

Successful JSON responses are wrapped ``{"schema_version": 1, ...}``. Errors use
FastAPI's standard ``{"detail": "..."}`` shape (clients must not depend on
``schema_version`` in error bodies). Downloads are always
``application/octet-stream`` + ``attachment`` + ``nosniff`` and resolve only via
the stored UUID filename.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlmodel import func, select

from backend import config
from backend.db import get_session
from backend.models import Project, utcnow
from backend.projectfiles import extract as extract_mod
from backend.projectfiles import signatures
from backend.projectfiles.models import ProjectFile

router = APIRouter()

SCHEMA_VERSION = 1

_CHUNK = 1024 * 1024
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Serialises the recheck-then-persist critical section. Correct for this
# single-process local SQLite deployment; a multi-process deployment would also
# need a BEGIN IMMEDIATE hook in backend/db.py (read-only here). The post-commit
# IntegrityError-free design plus this lock keeps concurrent uploads honest.
_UPLOAD_LOCK = threading.Lock()

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off", ""}


class FilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    ai_context_enabled: Optional[bool] = None


def _projectfiles_dir() -> Path:
    return Path(config.PROJECTFILES_DIR)


def _serialize(f: ProjectFile) -> dict:
    return {
        "file_id": f.id,
        "project_id": f.project_id,
        "original_name": f.original_name,
        "extension": f.extension,
        "byte_size": f.byte_size,
        "sha256": f.sha256,
        "description": f.description,
        "ai_context_enabled": f.ai_context_enabled,
        "ai_readable": f.extension in config.AI_READABLE_EXTENSIONS,
        "extract_status": f.extract_status,
        "created_at": f.created_at,
        "updated_at": f.updated_at,
    }


def _sanitize_filename(raw: Optional[str]) -> str:
    name = (raw or "").strip()
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="malformed filename")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="malformed filename")
    if _CONTROL_RE.search(name):
        raise HTTPException(status_code=400, detail="malformed filename")
    if Path(name).name != name:
        raise HTTPException(status_code=400, detail="malformed filename")
    return name


def _parse_bool(raw: Optional[str], field: str) -> bool:
    if raw is None:
        return False
    v = str(raw).strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise HTTPException(status_code=400, detail=f"malformed {field}")


def _safe_unlink(path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _content_disposition(name: str) -> str:
    ascii_fallback = _CONTROL_RE.sub("", name).encode("ascii", "ignore").decode("ascii")
    ascii_fallback = ascii_fallback.replace('"', "").replace("\\", "").strip()
    if not ascii_fallback:
        ascii_fallback = "file"
    quoted = urllib.parse.quote(name, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"


def _get_project_or_404(session, project_id: int) -> None:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")


def _get_file_or_404(session, project_id: int, file_id: int) -> ProjectFile:
    f = session.get(ProjectFile, file_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="file not found")
    return f


async def _stream_to_temp(upload: UploadFile, dest_dir: Path, max_bytes: int, ext: str):
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Keep the real extension so path-sniffing parsers (openpyxl, python-docx,
    # python-pptx) accept the temp file during pre-lock extraction.
    fd, tmp_path = tempfile.mkstemp(dir=str(dest_dir), prefix=".upload-", suffix=ext)
    digest = hashlib.sha256()
    total = 0
    head = b""
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="file too large")
                digest.update(chunk)
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                out.write(chunk)
    except BaseException:
        _safe_unlink(tmp_path)
        raise
    return tmp_path, total, digest.hexdigest(), head


def _recheck_limits(session, project_id: int, new_bytes: int) -> None:
    """Re-read every quota against live totals. Call inside ``_UPLOAD_LOCK``
    immediately before promoting/persisting the blob."""
    count = session.exec(
        select(func.count())
        .select_from(ProjectFile)
        .where(ProjectFile.project_id == project_id)
    ).one()
    if count + 1 > config.MAX_PROJECT_FILES_PER_PROJECT:
        raise HTTPException(status_code=409, detail="project file-count limit reached")

    project_bytes = session.exec(
        select(func.coalesce(func.sum(ProjectFile.byte_size), 0)).where(
            ProjectFile.project_id == project_id
        )
    ).one()
    if project_bytes + new_bytes > config.MAX_PROJECT_FILES_PER_PROJECT_BYTES:
        raise HTTPException(status_code=413, detail="project byte limit reached")

    global_bytes = session.exec(
        select(func.coalesce(func.sum(ProjectFile.byte_size), 0))
    ).one()
    if global_bytes + new_bytes > config.MAX_PROJECT_FILES_TOTAL_BYTES:
        raise HTTPException(status_code=413, detail="global byte limit reached")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/api/projects/{project_id}/files")
def list_files(project_id: int):
    with get_session() as session:
        _get_project_or_404(session, project_id)
        rows = session.exec(
            select(ProjectFile)
            .where(ProjectFile.project_id == project_id)
            .order_by(ProjectFile.created_at)
        ).all()
        return {
            "schema_version": SCHEMA_VERSION,
            "files": [_serialize(r) for r in rows],
        }


@router.post("/api/projects/{project_id}/files", status_code=201)
async def upload_file(
    project_id: int,
    file: UploadFile = File(...),
    description: Optional[str] = Form(default=None),
    ai_context_enabled: Optional[str] = Form(default=None),
):
    with get_session() as session:
        _get_project_or_404(session, project_id)

    name = _sanitize_filename(file.filename)
    ext = Path(name).suffix.lower()
    if not ext or ext not in config.PROJECT_FILE_EXTENSIONS:
        raise HTTPException(status_code=415, detail="file type not allowed")
    want_ai = _parse_bool(ai_context_enabled, "ai_context_enabled")

    dest_dir = _projectfiles_dir()
    tmp_path, total, digest, head = await _stream_to_temp(
        file, dest_dir, config.MAX_PROJECT_FILE_BYTES, ext
    )

    promoted = False
    try:
        if not signatures.signature_ok(ext, head):
            _safe_unlink(tmp_path)
            raise HTTPException(status_code=415, detail="content does not match extension")

        # Validation + text extraction happen BEFORE the write lock so the lock
        # holds only the recheck + atomic promote + insert (milliseconds).
        # OOXML: ZIP magic is necessary but not sufficient. A structure mismatch
        # is NOT a 415 -- the file is stored as a minimally-validated opaque blob.
        if not signatures.ooxml_structure_ok(Path(tmp_path), ext):
            status, text = "error", ""
        elif ext in config.AI_READABLE_EXTENSIONS:
            status, text = extract_mod.extract_text(
                Path(tmp_path), ext, config.PROJECT_FILE_TEXT_EXTRACT_MAX_BYTES
            )
        else:
            status, text = "unsupported", ""

        effective_ai = bool(
            want_ai
            and ext in config.AI_READABLE_EXTENSIONS
            and status in ("ok", "truncated")
        )

        with _UPLOAD_LOCK:
            with get_session() as session:
                _get_project_or_404(session, project_id)
                _recheck_limits(session, project_id, total)

                stored_name = uuid.uuid4().hex + ext
                final_path = dest_dir / stored_name
                os.replace(tmp_path, final_path)
                promoted = True

                row = ProjectFile(
                    project_id=project_id,
                    original_name=name,
                    stored_name=stored_name,
                    extension=ext,
                    byte_size=total,
                    sha256=digest,
                    description=description or "",
                    ai_context_enabled=effective_ai,
                    extract_status=status,
                    extracted_text=text,
                )
                session.add(row)
                try:
                    session.commit()
                    session.refresh(row)
                except Exception:
                    session.rollback()
                    _safe_unlink(final_path)
                    raise HTTPException(
                        status_code=500, detail="failed to persist file"
                    )
                return {"schema_version": SCHEMA_VERSION, "file": _serialize(row)}
    finally:
        if not promoted:
            _safe_unlink(tmp_path)


@router.get("/api/projects/{project_id}/files/{file_id}")
def get_file(project_id: int, file_id: int):
    with get_session() as session:
        f = _get_file_or_404(session, project_id, file_id)
        text = f.extracted_text if f.extract_status in ("ok", "truncated") else ""
        return {
            "schema_version": SCHEMA_VERSION,
            "file": _serialize(f),
            "extracted_text": text,
            "extract_status": f.extract_status,
        }


@router.get("/api/projects/{project_id}/files/{file_id}/download")
def download_file(project_id: int, file_id: int):
    with get_session() as session:
        f = _get_file_or_404(session, project_id, file_id)
        original_name = f.original_name
        stored_name = f.stored_name

    base = _projectfiles_dir()
    path = base / stored_name
    if not _within(path, base) or not path.is_file():
        raise HTTPException(status_code=404, detail="file blob missing")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": _content_disposition(original_name),
        },
    )


@router.patch("/api/projects/{project_id}/files/{file_id}")
def patch_file(project_id: int, file_id: int, body: FilePatch):
    updates = body.model_dump(exclude_unset=True)
    with get_session() as session:
        f = _get_file_or_404(session, project_id, file_id)
        if updates.get("ai_context_enabled") is True:
            ai_readable = f.extension in config.AI_READABLE_EXTENSIONS
            if not ai_readable or f.extract_status not in ("ok", "truncated"):
                raise HTTPException(
                    status_code=409, detail="file is not AI-readable"
                )
        if "description" in updates and updates["description"] is not None:
            f.description = updates["description"]
        if "ai_context_enabled" in updates and updates["ai_context_enabled"] is not None:
            f.ai_context_enabled = updates["ai_context_enabled"]
        f.updated_at = utcnow()
        session.add(f)
        session.commit()
        session.refresh(f)
        return {"schema_version": SCHEMA_VERSION, "file": _serialize(f)}


@router.delete("/api/projects/{project_id}/files/{file_id}")
def delete_file(project_id: int, file_id: int):
    with get_session() as session:
        f = _get_file_or_404(session, project_id, file_id)
        stored_name = f.stored_name
        session.delete(f)
        session.commit()
    base = _projectfiles_dir()
    path = base / stored_name
    if _within(path, base):
        _safe_unlink(path)
    return {"schema_version": SCHEMA_VERSION, "deleted": True, "file_id": file_id}
