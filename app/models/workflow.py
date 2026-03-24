"""
Workflow engine models: Work types, approval groups, work items, lines, reviews, comments, audit.

These models power the generic workflow system that supports budget requests,
contracts, supply orders, and future work types.
"""
from __future__ import annotations

from datetime import datetime

from app import db
from .constants import (
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_PENDING,
    REVIEW_STATUS_PENDING,
    ROUTING_STRATEGY_DIRECT,
)


class ApprovalGroup(db.Model):
    __tablename__ = "approval_groups"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)  # TECH, HOTEL, etc.
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class WorkType(db.Model):
    """Registry of modules that use the workflow engine (BUDGET now, CONTRACT later)."""
    __tablename__ = "work_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)  # BUDGET, CONTRACT, etc.
    name = db.Column(db.String(128), nullable=False)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class WorkTypeConfig(db.Model):
    """Configuration for each work type - controls routing, UI, and behavior."""
    __tablename__ = "work_type_configs"

    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", name="fk_work_type_configs_work_type_id"),
        primary_key=True,
    )

    # URL slug for routes (e.g., "budget", "contracts", "supply")
    url_slug = db.Column(db.String(32), unique=True, nullable=False, index=True)

    # Public ID prefix (e.g., "BUD", "CON", "SUP")
    public_id_prefix = db.Column(db.String(8), nullable=False)

    # Line detail table discriminator
    line_detail_type = db.Column(db.String(32), nullable=False)

    # Routing strategy: "expense_account", "contract_type", "category", "direct"
    routing_strategy = db.Column(db.String(32), nullable=False, default=ROUTING_STRATEGY_DIRECT)

    # Default approval group when routing_strategy="direct"
    default_approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_work_type_configs_default_approval_group_id"),
        nullable=True,
    )

    # Feature flags
    supports_supplementary = db.Column(db.Boolean, nullable=False, default=True)
    supports_fixed_costs = db.Column(db.Boolean, nullable=False, default=False)

    # Display labels
    item_singular = db.Column(db.String(32), nullable=False, default="Request")
    item_plural = db.Column(db.String(32), nullable=False, default="Requests")
    line_singular = db.Column(db.String(32), nullable=False, default="Line")
    line_plural = db.Column(db.String(32), nullable=False, default="Lines")

    work_type = db.relationship("WorkType", backref=db.backref("config", uselist=False))
    default_approval_group = db.relationship("ApprovalGroup", foreign_keys=[default_approval_group_id])


class WorkPortfolio(db.Model):
    """
    Canonical container for a department's work for a given event cycle and work type.
    This powers landing pages like /<event>/<department>/<work_type>.
    """
    __tablename__ = "work_portfolios"

    id = db.Column(db.Integer, primary_key=True)

    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", name="fk_work_portfolios_work_type_id"),
        nullable=False,
        index=True,
    )

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_work_portfolios_event_cycle_id"),
        nullable=False,
        index=True,
    )

    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_work_portfolios_department_id"),
        nullable=False,
        index=True,
    )

    # Soft archive
    is_archived = db.Column(db.Boolean, nullable=False, default=False, index=True)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)
    archived_by_user_id = db.Column(db.String(64), nullable=True, index=True)
    archived_reason = db.Column(db.Text, nullable=True)

    # Sequence counter for deterministic public IDs (e.g., SMF27-TECHOPS-BUD-1, BUD-2, etc.)
    next_public_id_seq = db.Column(db.Integer, nullable=False, default=1)

    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    work_type = db.relationship("WorkType")
    event_cycle = db.relationship("EventCycle")
    department = db.relationship("Department")

    work_items = db.relationship(
        "WorkItem",
        backref="portfolio",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="WorkItem.created_at.asc()",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "work_type_id", "event_cycle_id", "department_id",
            name="uq_work_portfolio_type_event_department",
        ),
    )


class UserRole(db.Model):
    __tablename__ = "user_roles"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.String(64),
        db.ForeignKey("users.id", name="fk_user_roles_user_id"),
        nullable=False,
        index=True,
    )

    role_code = db.Column(db.String(32), nullable=False, index=True)

    # Optional scope: work type (BUDGET, CONTRACT, etc.)
    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", name="fk_user_roles_work_type_id"),
        nullable=True,
        index=True,
    )

    # Optional scope: approval group (primarily for APPROVER roles)
    approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_user_roles_approval_group_id"),
        nullable=True,
        index=True,
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    work_type = db.relationship("WorkType", foreign_keys=[work_type_id])
    approval_group = db.relationship("ApprovalGroup", foreign_keys=[approval_group_id])

    __table_args__ = (
        # NOTE: This unique constraint has NULL semantics issue - SQL treats NULL != NULL.
        # A user could technically have duplicate (user_id, role_code) rows if work_type_id
        # and/or approval_group_id are NULL. In production PostgreSQL, add partial unique indexes:
        #   CREATE UNIQUE INDEX ix_user_roles_global_unique ON user_roles (user_id, role_code)
        #       WHERE work_type_id IS NULL AND approval_group_id IS NULL;
        #   CREATE UNIQUE INDEX ix_user_roles_worktype_unique ON user_roles (user_id, role_code, work_type_id)
        #       WHERE work_type_id IS NOT NULL AND approval_group_id IS NULL;
        #   CREATE UNIQUE INDEX ix_user_roles_approvalgroup_unique ON user_roles (user_id, role_code, approval_group_id)
        #       WHERE work_type_id IS NULL AND approval_group_id IS NOT NULL;
        # For SQLite dev, enforce uniqueness at application layer.
        db.UniqueConstraint(
            "user_id", "role_code", "work_type_id", "approval_group_id",
            name="uq_user_role_scoped_once",
        ),
    )


class WorkItem(db.Model):
    __tablename__ = "work_items"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=True, index=True)

    portfolio_id = db.Column(
        db.Integer,
        db.ForeignKey("work_portfolios.id", name="fk_work_items_portfolio_id"),
        nullable=False,
        index=True,
    )

    request_kind = db.Column(
        db.String(16),
        nullable=False,
        default=REQUEST_KIND_PRIMARY,
        index=True,
    )

    # Optional reason/description for this request (primarily used for supplementals)
    reason = db.Column(db.String(256), nullable=True)

    status = db.Column(db.String(32), nullable=False, default=WORK_ITEM_STATUS_DRAFT, index=True)

    # Submission lifecycle
    submitted_at = db.Column(db.DateTime, nullable=True, index=True)
    submitted_by_user_id = db.Column(db.String(64), nullable=True, index=True)

    # Review lifecycle (admin checkout start)
    review_started_at = db.Column(db.DateTime, nullable=True, index=True)
    review_started_by_user_id = db.Column(db.String(64), nullable=True, index=True)

    # Finalization lifecycle
    finalized_note = db.Column(db.Text, nullable=True)
    finalized_at = db.Column(db.DateTime, nullable=True, index=True)
    finalized_by_user_id = db.Column(db.String(64), nullable=True, index=True)

    # Dispatch lifecycle (for dispatch queue workflow)
    dispatched_at = db.Column(db.DateTime, nullable=True, index=True)
    dispatched_by_user_id = db.Column(db.String(64), nullable=True, index=True)

    # Checkout / check-in locking
    checked_out_by_user_id = db.Column(db.String(64), nullable=True, index=True)
    checked_out_at = db.Column(db.DateTime, nullable=True, index=True)
    checked_out_expires_at = db.Column(db.DateTime, nullable=True, index=True)

    # NEEDS_INFO tracking
    needs_info_requested_at = db.Column(db.DateTime, nullable=True, index=True)
    needs_info_requested_by_user_id = db.Column(db.String(64), nullable=True, index=True)

    # Email batching flag
    pending_notification = db.Column(db.Boolean, nullable=False, default=False)

    # Soft archive (rare; normally archive portfolios instead)
    is_archived = db.Column(db.Boolean, nullable=False, default=False, index=True)
    archived_at = db.Column(db.DateTime, nullable=True, index=True)
    archived_by_user_id = db.Column(db.String(64), nullable=True, index=True)
    archived_reason = db.Column(db.Text, nullable=True)

    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    lines = db.relationship(
        "WorkLine",
        backref="work_item",
        cascade="all, delete-orphan",
        order_by="WorkLine.line_number.asc()",
        lazy=True,
    )

    comments = db.relationship(
        "WorkItemComment",
        backref="work_item",
        cascade="all, delete-orphan",
        order_by="WorkItemComment.created_at.asc()",
        lazy=True,
    )

    audit_events = db.relationship(
        "WorkItemAuditEvent",
        backref="work_item",
        cascade="all, delete-orphan",
        order_by="WorkItemAuditEvent.created_at.asc()",
        lazy=True,
    )

    __table_args__ = (
        db.Index("ix_work_items_portfolio_kind", "portfolio_id", "request_kind"),
        # Composite index for portfolio landing pages filtering by status
        db.Index("ix_work_items_portfolio_status", "portfolio_id", "status"),
    )


class WorkLine(db.Model):
    __tablename__ = "work_lines"

    id = db.Column(db.Integer, primary_key=True)

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_work_lines_work_item_id"),
        nullable=False,
        index=True,
    )

    line_number = db.Column(db.Integer, nullable=False)

    status = db.Column(db.String(32), nullable=False, default=WORK_LINE_STATUS_PENDING, index=True)

    # Final approved amount (admin final), in cents
    approved_amount_cents = db.Column(db.Integer, nullable=True)

    # Public-facing reviewer note/rationale (current/overall)
    reviewer_note = db.Column(db.Text, nullable=True)

    # Status change tracking
    status_changed_at = db.Column(db.DateTime, nullable=True, index=True)
    status_changed_by_user_id = db.Column(db.String(64), nullable=True, index=True)

    # Quick filters for dashboards
    needs_requester_action = db.Column(db.Boolean, nullable=False, default=False, index=True)

    # Optional: helps list views (e.g., approval group vs admin final)
    current_review_stage = db.Column(db.String(32), nullable=True, index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    updated_by_user_id = db.Column(db.String(64), nullable=True, index=True)

    __table_args__ = (
        db.UniqueConstraint("work_item_id", "line_number", name="uq_work_line_number_per_item"),
        # Composite index for dashboard queries filtering by status and review stage
        db.Index("ix_work_lines_status_review_stage", "status", "current_review_stage"),
    )

    audit_events = db.relationship(
        "WorkLineAuditEvent",
        backref="work_line",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="WorkLineAuditEvent.created_at.asc()",
    )

    comments = db.relationship(
        "WorkLineComment",
        backref="work_line",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="WorkLineComment.created_at.asc()",
    )

    reviews = db.relationship(
        "WorkLineReview",
        backref="work_line",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="WorkLineReview.created_at.asc()",
    )


class WorkLineAuditEvent(db.Model):
    __tablename__ = "work_line_audit_events"

    id = db.Column(db.Integer, primary_key=True)

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_work_line_audit_work_line_id"),
        nullable=False,
        index=True,
    )

    event_type = db.Column(db.String(64), nullable=False, index=True)  # FIELD_CHANGE, STATUS_CHANGE, etc.
    field_name = db.Column(db.String(64), nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)


class WorkItemAuditEvent(db.Model):
    """Audit event for work item level actions (finalize, unfinalize, etc.)."""
    __tablename__ = "work_item_audit_events"

    id = db.Column(db.Integer, primary_key=True)

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_work_item_audit_work_item_id"),
        nullable=False,
        index=True,
    )

    event_type = db.Column(db.String(64), nullable=False, index=True)  # FINALIZE, UNFINALIZE, STATUS_CHANGE, SUBMIT, DISPATCH, etc.
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    reason = db.Column(db.Text, nullable=True)
    snapshot = db.Column(db.JSON, nullable=True)  # Flexible JSON for event-specific data

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)


class WorkLineComment(db.Model):
    __tablename__ = "work_line_comments"

    id = db.Column(db.Integer, primary_key=True)

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_work_line_comments_work_line_id"),
        nullable=False,
        index=True,
    )

    visibility = db.Column(db.String(16), nullable=False, default="PUBLIC", index=True)
    body = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)


class WorkItemComment(db.Model):
    """Comment on a work item (request-level, not line-level)."""
    __tablename__ = "work_item_comments"

    id = db.Column(db.Integer, primary_key=True)

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_work_item_comments_work_item_id"),
        nullable=False,
        index=True,
    )

    visibility = db.Column(db.String(16), nullable=False, default="PUBLIC", index=True)
    body = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)


class WorkLineReview(db.Model):
    """Decision record for a WorkLine at a particular review stage."""
    __tablename__ = "work_line_reviews"

    id = db.Column(db.Integer, primary_key=True)

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_work_line_reviews_work_line_id"),
        nullable=False,
        index=True,
    )

    stage = db.Column(db.String(32), nullable=False, index=True)

    # For stage=APPROVAL_GROUP this should typically be set (budget routing).
    # For stage=ADMIN_FINAL, keep NULL.
    approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_work_line_reviews_approval_group_id"),
        nullable=True,
        index=True,
    )

    status = db.Column(db.String(32), nullable=False, default=REVIEW_STATUS_PENDING, index=True)

    # Layer-approved amount (approval group recommendation or admin final)
    approved_amount_cents = db.Column(db.Integer, nullable=True)

    note = db.Column(db.Text, nullable=True)

    decided_at = db.Column(db.DateTime, nullable=True, index=True)
    decided_by_user_id = db.Column(db.String(64), nullable=True, index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)

    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    approval_group = db.relationship("ApprovalGroup", foreign_keys=[approval_group_id])

    __table_args__ = (
        # NOTE: This unique constraint has NULL semantics issue - SQL treats NULL != NULL.
        # For ADMIN_FINAL stage where approval_group_id is NULL, duplicates are technically possible.
        # In production PostgreSQL, add partial unique indexes:
        #   CREATE UNIQUE INDEX ix_wlr_admin_final_unique ON work_line_reviews (work_line_id, stage)
        #   WHERE approval_group_id IS NULL;
        # For SQLite dev, enforce uniqueness at application layer.
        db.UniqueConstraint("work_line_id", "stage", "approval_group_id", name="uq_work_line_review_per_stage"),
        # Composite index for approval queue queries
        db.Index("ix_work_line_reviews_stage_status", "stage", "status"),
    )
