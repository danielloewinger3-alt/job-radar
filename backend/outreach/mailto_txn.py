"""Race-safe ``approved -> contacted`` mailto transition.

``OutreachAttempt`` deliberately has no uniqueness constraint, so a
check-then-insert under ordinary session semantics could let two concurrent
``/mailto`` requests both create an attempt. This helper does the whole
sequence -- reload thread, verify stage + ``approved_at``, reload + validate
the selected contact, check suppression, check for an uncleared attempt,
insert the immutable attempt, write the event, move the thread to
``contacted`` -- inside a single SQLite ``BEGIN IMMEDIATE`` transaction on a
raw DBAPI connection.

``BEGIN IMMEDIATE`` takes a RESERVED lock at once, so a second concurrent
caller blocks (``PRAGMA busy_timeout``) until the first commits, then re-runs
the duplicate check and returns ``duplicate_attempt``. Nothing is reported as
successful unless ``COMMIT`` succeeds.

``backend/db.py`` is read-only for this workstream, hence the self-contained
raw-connection approach here rather than a global engine event.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from backend import db as _db

BUSY_TIMEOUT_MS = 5000
_LOCK_RETRY_BUDGET_S = 6.0

_TS_FMT = "%Y-%m-%d %H:%M:%S.%f"  # matches SQLAlchemy's SQLite DateTime storage


class MailtoTxnError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def _suppressed(cur, email_normalized: str) -> bool:
    domain = email_normalized.split("@", 1)[1] if "@" in email_normalized else ""
    for kind, value in cur.execute("SELECT kind, value FROM outreachsuppression").fetchall():
        if kind == "email" and value == email_normalized:
            return True
        if kind == "domain" and domain and (domain == value or domain.endswith("." + value)):
            return True
    return False


def _run(cur, thread_id: int) -> dict:
    row = cur.execute(
        "SELECT business_id, stage, approved_at, selected_contact_id, subject, body "
        "FROM outreachthread WHERE id = ?",
        (thread_id,),
    ).fetchone()
    if row is None:
        raise MailtoTxnError("not_found")
    business_id, stage, approved_at, contact_id, subject, body = row

    if stage != "approved" or not approved_at:
        raise MailtoTxnError("approval_required")
    if not contact_id:
        raise MailtoTxnError("approval_required")

    contact = cur.execute(
        "SELECT email, email_normalized, business_id, active FROM outreachcontact WHERE id = ?",
        (contact_id,),
    ).fetchone()
    if contact is None:
        raise MailtoTxnError("contact_stale")
    email, email_norm, contact_business_id, active = contact
    if contact_business_id != business_id:
        raise MailtoTxnError("contact_business_mismatch")
    if not active:
        raise MailtoTxnError("contact_stale")
    if not email_norm:
        raise MailtoTxnError("contact_stale")

    if _suppressed(cur, email_norm):
        raise MailtoTxnError("contact_suppressed")

    dup = cur.execute(
        "SELECT 1 FROM outreachattempt "
        "WHERE cleared_at IS NULL AND ("
        "  thread_id = ? OR (business_id = ? AND email_normalized = ?)"
        ") LIMIT 1",
        (thread_id, business_id, email_norm),
    ).fetchone()
    if dup is not None:
        raise MailtoTxnError("duplicate_attempt")

    now = _now()
    cur.execute(
        "INSERT INTO outreachattempt "
        "(business_id, email_normalized, thread_id, created_at, cleared_at, cleared_reason) "
        "VALUES (?, ?, ?, ?, NULL, '')",
        (business_id, email_norm, thread_id, now),
    )
    attempt_id = cur.lastrowid
    cur.execute(
        "INSERT INTO outreachevent (thread_id, kind, detail, created_at) VALUES (?, 'mailto_generated', ?, ?)",
        (thread_id, json.dumps({"attempt_id": attempt_id, "email_normalized": email_norm}), now),
    )
    cur.execute(
        "UPDATE outreachthread "
        "SET stage = 'contacted', mailto_generated_at = ?, contacted_at = ?, updated_at = ? WHERE id = ?",
        (now, now, now, thread_id),
    )
    return {
        "thread_id": thread_id,
        "business_id": business_id,
        "email": email,
        "email_normalized": email_norm,
        "subject": subject or "",
        "body": body or "",
        "attempt_id": attempt_id,
        "stage": "contacted",
    }


def create_mailto_attempt(thread_id: int) -> dict:
    """Run the transition under ``BEGIN IMMEDIATE``. Returns a state dict on a
    committed success; raises :class:`MailtoTxnError` (``code``) otherwise."""
    deadline = time.monotonic() + _LOCK_RETRY_BUDGET_S
    while True:
        raw = _db.engine.raw_connection()
        prev_isolation = getattr(raw, "isolation_level", None)
        try:
            try:
                raw.isolation_level = None  # drive BEGIN/COMMIT ourselves
            except Exception:  # noqa: BLE001
                pass
            cur = raw.cursor()
            cur.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            try:
                cur.execute("BEGIN IMMEDIATE")
            except Exception as exc:  # noqa: BLE001 -- database is locked
                try:
                    cur.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
                if time.monotonic() < deadline and "lock" in str(exc).lower():
                    time.sleep(0.05)
                    continue
                raise MailtoTxnError("db_locked") from exc

            try:
                result = _run(cur, thread_id)
                cur.execute("COMMIT")
                return result
            except Exception:
                try:
                    cur.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
                raise
        finally:
            try:
                raw.isolation_level = prev_isolation
            except Exception:  # noqa: BLE001
                pass
            raw.close()
