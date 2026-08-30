"""Outreach SQLModel tables.

Seven tables, all owned by this workstream. The four (now nine) additive
``business`` columns are NOT declared here -- ``backend.models.Business`` is
read-only for this workstream, so those columns are added by
``backend.outreach.migrate`` and read/written with raw SQL.

Startup imports this module via ``backend.features.import_feature_models`` so the
tables are registered on ``SQLModel.metadata`` before ``create_all()``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models import UTCDateTime, utcnow

# --------------------------------------------------------------------------- #
# Enumerable value sets (kept as plain tuples; validated in the router)
# --------------------------------------------------------------------------- #
OUTREACH_STAGES: tuple[str, ...] = (
    "identified",
    "drafted",
    "approved",
    "contacted",
    "replied",
    "meeting",
    "closed_won",
    "closed_lost",
)
TERMINAL_STAGES: tuple[str, ...] = ("closed_won", "closed_lost")
ACTIVE_STAGES: tuple[str, ...] = tuple(s for s in OUTREACH_STAGES if s not in TERMINAL_STAGES)

WEBSITE_CONFIDENCE_VALUES: tuple[str, ...] = (
    "",
    "osm",
    "guessed_verified",
    "companies_house",
    "manual",
)
DISCOVERY_STATUSES: tuple[str, ...] = (
    "",
    "resolved",
    "unresolved",
    "unsafe",
    "transient_failure",
)
CONTACT_CLASSIFICATIONS: tuple[str, ...] = ("generic", "role", "named")
CONTACT_METHODS: tuple[str, ...] = ("mailto", "visible_text")
CONTACT_STALE_REASONS: tuple[str, ...] = ("", "rediscovery", "not_refound", "manual")
SUPPRESSION_KINDS: tuple[str, ...] = ("email", "domain")
SUPPRESSION_ORIGINS: tuple[str, ...] = ("opt_out", "manual")
ATTEMPT_CLEARED_REASONS: tuple[str, ...] = ("", "reply", "reopen")

EVENT_KINDS: tuple[str, ...] = (
    "draft",
    "revise",
    "approve",
    "mailto_generated",
    "reply_logged",
    "stage_change",
    "opt_out",
    "reopen",
    "note",
    "contact_stale",
    "contact_suppressed",
    "suppression_created",
    "suppression_deleted",
)


class OutreachContact(SQLModel, table=True):
    """One row per (business, normalized email). Historical rows are never
    deleted; ``active`` is the "usable for outreach right now" flag."""

    __tablename__ = "outreachcontact"
    __table_args__ = (
        UniqueConstraint("business_id", "email_normalized", name="uq_contact_business_email"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: str = Field(index=True)  # -> business.id (by value; cross-package)
    email: str
    email_normalized: str = Field(index=True)
    classification: str = "generic"  # derived from strongest evidence
    method: str = "visible_text"  # derived from strongest evidence
    suppressed: bool = False
    active: bool = True
    verified_website: str = ""  # official_website value this address was last confirmed under
    stale_reason: str = ""  # '' | rediscovery | not_refound | manual
    first_seen_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    deactivated_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)


class ContactEvidence(SQLModel, table=True):
    """Append-only record of where each address was seen. Deduped per
    (contact, page, method) so re-crawls do not create duplicate rows."""

    __tablename__ = "contactevidence"
    __table_args__ = (
        UniqueConstraint("contact_id", "source_url", "method", name="uq_evidence_contact_url_method"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    contact_id: int = Field(index=True)
    business_id: str = Field(index=True)
    email_normalized: str = Field(index=True)
    source_url: str
    method: str  # mailto | visible_text
    classification_at_source: str = "generic"
    page_kind: str = "other"  # contact_page | other
    found_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class OutreachThread(SQLModel, table=True):
    __tablename__ = "outreachthread"

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: str = Field(index=True)
    stage: str = Field(default="identified", index=True)
    selected_contact_id: Optional[int] = Field(default=None)
    subject: str = ""
    body: str = ""
    context: str = ""  # last client-supplied notes (untrusted)
    context_json: str = ""  # server-side audit of the last draft's sources / caps
    approved_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    mailto_generated_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    contacted_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    replied_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class OutreachEvent(SQLModel, table=True):
    """Append-only history. ``thread_id`` is nullable so suppression
    create/delete audit rows can be recorded without a thread."""

    __tablename__ = "outreachevent"

    id: Optional[int] = Field(default=None, primary_key=True)
    thread_id: Optional[int] = Field(default=None, index=True)
    kind: str
    detail: str = ""  # JSON where structured; always sanitized
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class OutreachAttempt(SQLModel, table=True):
    """Immutable, append-only. Created only by a committed ``/mailto``
    transaction; cleared (never deleted) only by ``/reply`` or ``/reopen``.
    Deliberately NO uniqueness constraint on (business_id, email_normalized)."""

    __tablename__ = "outreachattempt"
    __table_args__ = (
        Index("ix_attempt_business_email", "business_id", "email_normalized"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: str = Field(index=True)
    email_normalized: str
    thread_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    cleared_at: Optional[datetime] = Field(default=None, sa_type=UTCDateTime)
    cleared_reason: str = ""  # '' | reply | reopen


class OutreachSuppression(SQLModel, table=True):
    __tablename__ = "outreachsuppression"
    __table_args__ = (
        UniqueConstraint("kind", "value", name="uq_suppression_kind_value"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str  # email | domain
    value: str = Field(index=True)  # normalized
    origin: str = "manual"  # opt_out | manual  (immutable)
    thread_id: Optional[int] = Field(default=None)  # set for opt_out-sourced rows
    note: str = ""
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class DiscoveryLog(SQLModel, table=True):
    """Append-only, one row per discovery / rediscovery / (re)collection
    attempt. Holds the candidate + evidence audit for guessed websites."""

    __tablename__ = "discoverylog"

    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: str = Field(index=True)
    attempted_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    outcome: str = ""  # resolved|unresolved|unsafe|transient_failure|reset|contacts_collected|contacts_recollect|contacts_skip
    method: str = ""  # osm | guessed | none
    candidates_json: str = ""
    evidence_json: str = ""
    error: str = ""  # sanitized code, <= 120 chars
