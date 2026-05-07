"""
AV-specific models for the AV Request work type.

This file holds:
- AVRequestDetail (item-level): one row per AV WorkItem
- AVLineDetail (line-level): one row per AV WorkLine (single line per request)
- AVRequestPlan (per-request, versioned): the AV team's planning artifact
- AVScope (per-space, versioned): canonical doc with state machine
- AVAcknowledgment: per-dept ack of each scope version
- AVScopeIncorporatedRequest: link table populated at scope lock time

Space and SpaceDepartmentAssignment live in app/models/space.py since
Space is a top-level concept that may be reused by future work types.
"""
from __future__ import annotations

from datetime import datetime

from app import db


class AVRequestDetail(db.Model):
    """Item-level fields for an AV request. One row per WorkItem."""
    __tablename__ = "av_request_details"

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_av_request_details_work_item_id"),
        primary_key=True,
    )
    space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_av_request_details_space_id"),
        nullable=False,
        index=True,
    )

    # 'MUST_HAVE' | 'STRONG_PREFERENCE' | 'NICE_TO_HAVE'
    priority = db.Column(db.String(32), nullable=False)

    # 'HOURS_OF_CONTENT' | 'FULL_EVENT' | 'MULTIPLE_SLOTS'
    duration_model = db.Column(db.String(32), nullable=False)
    duration_hours = db.Column(db.Numeric(5, 2), nullable=True)
    duration_slots = db.Column(db.Integer, nullable=True)
    duration_notes = db.Column(db.Text, nullable=True)

    # 'NONE' | 'SOME'
    dept_sourced_gear_mode = db.Column(db.String(16), nullable=False, default="NONE")
    dept_sourced_gear_text = db.Column(db.Text, nullable=True)

    primary_contact_name = db.Column(db.String(256), nullable=False)
    primary_contact_email = db.Column(db.String(256), nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    work_item = db.relationship(
        "WorkItem",
        backref=db.backref("av_request_detail", uselist=False, cascade="all, delete-orphan"),
    )
    space = db.relationship("Space")


class AVLineDetail(db.Model):
    """Line-level fields. Exactly one row per AV WorkLine."""
    __tablename__ = "av_line_details"

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_av_line_details_work_line_id"),
        primary_key=True,
    )
    description = db.Column(db.Text, nullable=False)
    gear_specificity = db.Column(db.String(32), nullable=False)
    suggested_gear_text = db.Column(db.Text, nullable=True)
    routed_approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_av_line_details_routed_approval_group_id"),
        nullable=True,
        index=True,
    )

    work_line = db.relationship(
        "WorkLine",
        backref=db.backref("av_line_detail", uselist=False, cascade="all, delete-orphan"),
    )
    routed_approval_group = db.relationship("ApprovalGroup", foreign_keys=[routed_approval_group_id])


class AVRequestPlan(db.Model):
    """The AV team's planning artifact for an AV request. Versioned per request.

    Multiple revisions can exist per work_item; revision starts at 1 and
    increments. The latest revision is what's shown on the request detail
    page; older revisions are accessible via accordion.

    Publishing a Plan transitions the request's single WorkLine review
    to LOGGED status (no commitment about deliverable — see spec §5.1).
    """
    __tablename__ = "av_request_plans"

    id = db.Column(db.Integer, primary_key=True)
    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_av_request_plans_work_item_id"),
        nullable=False,
        index=True,
    )
    revision = db.Column(db.Integer, nullable=False)
    gear_spec = db.Column(db.Text, nullable=False)
    planning_notes = db.Column(db.Text, nullable=True)
    authored_by_user_id = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    work_item = db.relationship("WorkItem", backref=db.backref("av_plans", cascade="all, delete-orphan"))

    __table_args__ = (
        db.UniqueConstraint("work_item_id", "revision", name="uq_av_request_plans_work_item_revision"),
    )


class AVScope(db.Model):
    """Canonical AV plan for a Space. Versioned. State machine.

    States: DRAFT → OPEN_FOR_INPUT → LOCKED (or SUPERSEDED).
    See spec §4.6 for full state diagram and transition rules.

    Only the latest version per space is "live"; older versions
    stay in their terminal state for history.
    """
    __tablename__ = "av_scopes"

    id = db.Column(db.Integer, primary_key=True)
    space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_av_scopes_space_id"),
        nullable=False,
        index=True,
    )
    version = db.Column(db.Integer, nullable=False)

    # 'DRAFT' | 'OPEN_FOR_INPUT' | 'LOCKED' | 'SUPERSEDED'
    state = db.Column(db.String(32), nullable=False, default="DRAFT")

    scope_text = db.Column(db.Text, nullable=False)
    changes_since_previous_text = db.Column(db.Text, nullable=True)
    authored_by_user_id = db.Column(db.String(64), nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    published_at = db.Column(db.DateTime, nullable=True)
    locked_at = db.Column(db.DateTime, nullable=True)
    locked_by_user_id = db.Column(db.String(64), nullable=True)
    force_locked = db.Column(db.Boolean, nullable=False, default=False)
    force_lock_reason = db.Column(db.Text, nullable=True)
    superseded_at = db.Column(db.DateTime, nullable=True)
    superseded_by_scope_id = db.Column(
        db.Integer,
        db.ForeignKey("av_scopes.id", name="fk_av_scopes_superseded_by_scope_id"),
        nullable=True,
    )

    space = db.relationship("Space")
    superseded_by = db.relationship("AVScope", remote_side=[id])

    __table_args__ = (
        db.UniqueConstraint("space_id", "version", name="uq_av_scopes_space_version"),
        db.Index("ix_av_scopes_space_state", "space_id", "state"),
    )


class AVAcknowledgment(db.Model):
    """Per-department acknowledgment of an AVScope version.

    Created automatically (one per assigned dept) when a scope transitions
    to OPEN_FOR_INPUT. Mutable while parent scope is OPEN_FOR_INPUT;
    frozen once parent leaves that state (LOCKED or SUPERSEDED).

    See spec §4.7 for lifecycle.
    """
    __tablename__ = "av_acknowledgments"

    id = db.Column(db.Integer, primary_key=True)
    scope_id = db.Column(
        db.Integer,
        db.ForeignKey("av_scopes.id", name="fk_av_acknowledgments_scope_id"),
        nullable=False,
        index=True,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_av_acknowledgments_department_id"),
        nullable=False,
        index=True,
    )

    # 'PENDING' | 'NO_CONCERNS' | 'CONCERNS'
    state = db.Column(db.String(32), nullable=False, default="PENDING")

    acknowledged_by_user_id = db.Column(db.String(64), nullable=True)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    concern_text = db.Column(db.Text, nullable=True)

    scope = db.relationship("AVScope", backref=db.backref("acknowledgments", cascade="all, delete-orphan"))
    department = db.relationship("Department")

    __table_args__ = (
        db.UniqueConstraint("scope_id", "department_id", name="uq_av_acknowledgments_scope_dept"),
        db.Index("ix_av_acknowledgments_dept_state", "department_id", "state"),
    )


class AVScopeIncorporatedRequest(db.Model):
    """Snapshot link table — which AV requests were folded into each
    scope version at lock time.

    Populated by the lock action. See spec §4.8 and §8.4.
    """
    __tablename__ = "av_scope_incorporated_requests"

    scope_id = db.Column(
        db.Integer,
        db.ForeignKey("av_scopes.id", name="fk_av_scope_incorporated_requests_scope_id"),
        primary_key=True,
    )
    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_av_scope_incorporated_requests_work_item_id"),
        primary_key=True,
    )
    incorporated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    scope = db.relationship("AVScope", backref=db.backref("incorporated_requests", cascade="all, delete-orphan"))
    work_item = db.relationship("WorkItem", backref=db.backref("av_scope_incorporations", cascade="all, delete-orphan"))
