from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Numeric

from app import db

# ============================================================
# Constants
# ============================================================

# Comment visibility
COMMENT_VISIBILITY_PUBLIC = "PUBLIC"
COMMENT_VISIBILITY_ADMIN = "ADMIN"

# Work item statuses (request header)
WORK_ITEM_STATUS_DRAFT = "DRAFT"
WORK_ITEM_STATUS_SUBMITTED = "SUBMITTED"
WORK_ITEM_STATUS_UNDER_REVIEW = "UNDER_REVIEW"
WORK_ITEM_STATUS_FINALIZED = "FINALIZED"
WORK_ITEM_STATUS_UNAPPROVED = "UNAPPROVED"  # reopened after finalize
WORK_ITEM_STATUS_NEEDS_INFO = "NEEDS_INFO"  # awaiting requester response
WORK_ITEM_STATUS_PAUSED = "PAUSED"  # supplementary blocked while primary is unfinalized

# Work line statuses (overall current state)
WORK_LINE_STATUS_PENDING = "PENDING"
WORK_LINE_STATUS_NEEDS_INFO = "NEEDS_INFO"
WORK_LINE_STATUS_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
WORK_LINE_STATUS_APPROVED = "APPROVED"
WORK_LINE_STATUS_REJECTED = "REJECTED"

# Review stages
REVIEW_STAGE_APPROVAL_GROUP = "APPROVAL_GROUP"
REVIEW_STAGE_ADMIN_FINAL = "ADMIN_FINAL"

# Review decision statuses
REVIEW_STATUS_PENDING = "PENDING"
REVIEW_STATUS_NEEDS_INFO = "NEEDS_INFO"
REVIEW_STATUS_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
REVIEW_STATUS_APPROVED = "APPROVED"
REVIEW_STATUS_REJECTED = "REJECTED"

# Role codes
ROLE_SUPER_ADMIN = "SUPER_ADMIN"        # global admin
ROLE_WORKTYPE_ADMIN = "WORKTYPE_ADMIN"  # admin for a work type (e.g., BUDGET)
ROLE_APPROVER = "APPROVER"              # approver (typically scoped to approval group)

# Spend type selection modes for expense accounts
SPEND_TYPE_MODE_SINGLE_LOCKED = "SINGLE_LOCKED"  # exactly one allowed spend type; UI locked
SPEND_TYPE_MODE_ALLOW_LIST = "ALLOW_LIST"        # choose from allowed spend types list

# Department visibility modes for expense accounts
VISIBILITY_MODE_ALL = "ALL_DEPARTMENTS"
VISIBILITY_MODE_RESTRICTED = "RESTRICTED"

# Optional UI grouping
UI_GROUP_KNOWN_COSTS = "KNOWN_COSTS"

# Optional prompt modes for "Known Costs" prompting behavior
PROMPT_MODE_NONE = "NONE"
PROMPT_MODE_SUGGEST = "SUGGEST"
PROMPT_MODE_REQUIRE_EXPLICIT_NA = "REQUIRE_EXPLICIT_NA"

# Request kinds within a portfolio
REQUEST_KIND_PRIMARY = "PRIMARY"
REQUEST_KIND_SUPPLEMENTARY = "SUPPLEMENTARY"

# Notification statuses
NOTIF_STATUS_QUEUED = "QUEUED"
NOTIF_STATUS_SENT = "SENT"
NOTIF_STATUS_FAILED = "FAILED"
NOTIF_STATUS_SUPPRESSED = "SUPPRESSED"

# Config audit actions
CONFIG_AUDIT_CREATE = "CREATE"
CONFIG_AUDIT_UPDATE = "UPDATE"
CONFIG_AUDIT_ARCHIVE = "ARCHIVE"
CONFIG_AUDIT_RESTORE = "RESTORE"

# Line audit event types
AUDIT_EVENT_STATUS_CHANGE = "STATUS_CHANGE"
AUDIT_EVENT_REVIEW_DECISION = "REVIEW_DECISION"
AUDIT_EVENT_REQUESTER_RESPONSE = "REQUESTER_RESPONSE"
AUDIT_EVENT_ADMIN_FINAL = "ADMIN_FINAL"
AUDIT_EVENT_AMOUNT_OVERRIDE = "AMOUNT_OVERRIDE"

# Work item audit event types
AUDIT_EVENT_FINALIZE = "FINALIZE"
AUDIT_EVENT_UNFINALIZE = "UNFINALIZE"

# Review actions
REVIEW_ACTION_APPROVE = "APPROVE"
REVIEW_ACTION_REJECT = "REJECT"
REVIEW_ACTION_NEEDS_INFO = "NEEDS_INFO"
REVIEW_ACTION_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
REVIEW_ACTION_RESET = "RESET"
REVIEW_ACTION_RESPOND = "RESPOND"


# ============================================================
# Core org models: Events, Departments, Users, Membership
# ============================================================

class EventCycle(db.Model):
    __tablename__ = "event_cycles"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)   # e.g. SMF2027
    name = db.Column(db.String(128), nullable=False)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    # Key dates for the budget cycle
    event_start_date = db.Column(db.Date, nullable=True)          # When the event starts
    submission_deadline = db.Column(db.Date, nullable=True)       # Budget submission deadline
    approval_target_date = db.Column(db.Date, nullable=True)      # Target for completing approvals
    finalization_date = db.Column(db.Date, nullable=True)         # When budgets are locked/finalized

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class Division(db.Model):
    __tablename__ = "divisions"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    departments = db.relationship("Department", backref="division", lazy=True)
    memberships = db.relationship("DivisionMembership", backref="division", lazy=True)


class DivisionMembership(db.Model):
    """
    Division-level membership grants permissions across ALL departments in a division.
    Division heads can view/edit/submit requests for any department in their division.
    """
    __tablename__ = "division_memberships"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.String(64),
        db.ForeignKey("users.id", name="fk_division_memberships_user_id"),
        nullable=False,
        index=True,
    )

    division_id = db.Column(
        db.Integer,
        db.ForeignKey("divisions.id", name="fk_division_memberships_division_id"),
        nullable=False,
        index=True,
    )

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_division_memberships_event_cycle_id"),
        nullable=False,
        index=True,
    )

    can_view = db.Column(db.Boolean, nullable=False, default=True)
    can_edit = db.Column(db.Boolean, nullable=False, default=False)
    is_division_head = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("division_memberships", lazy=True))
    event_cycle = db.relationship("EventCycle")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "division_id", "event_cycle_id",
            name="uq_division_membership_user_div_cycle",
        ),
    )


class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)  # TECHOPS, HOTELS, etc.
    name = db.Column(db.String(128), nullable=False)

    division_id = db.Column(
        db.Integer,
        db.ForeignKey("divisions.id", name="fk_departments_division_id"),
        nullable=True,  # Nullable for backwards compat
        index=True,
    )

    description = db.Column(db.Text, nullable=True)
    mailing_list = db.Column(db.String(256), nullable=True)
    slack_channel = db.Column(db.String(128), nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class User(db.Model):
    __tablename__ = "users"

    # Keep string PK to align with auth subject / internal IDs
    id = db.Column(db.String(64), primary_key=True)

    email = db.Column(db.String(256), nullable=False, unique=True, index=True)
    auth_subject = db.Column(db.String(255), nullable=True, unique=True, index=True)

    display_name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    roles = db.relationship("UserRole", backref="user", cascade="all, delete-orphan", lazy=True)


class DepartmentMembership(db.Model):
    __tablename__ = "department_memberships"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.String(64),
        db.ForeignKey("users.id", name="fk_dept_memberships_user_id"),
        nullable=False,
        index=True,
    )

    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_dept_memberships_department_id"),
        nullable=False,
        index=True,
    )

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_dept_memberships_event_cycle_id"),
        nullable=False,
        index=True,
    )

    can_view = db.Column(db.Boolean, nullable=False, default=True)
    can_edit = db.Column(db.Boolean, nullable=False, default=False)
    is_department_head = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("department_memberships", lazy=True))
    department = db.relationship("Department")
    event_cycle = db.relationship("EventCycle")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "department_id", "event_cycle_id",
            name="uq_dept_membership_user_dept_cycle",
        ),
    )


# ============================================================
# Approval groups + Work type registry + Roles
# ============================================================

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


# ============================================================
# Generic workflow engine: Work items, Lines, Comments, Audit, Reviews
# ============================================================

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

    event_type = db.Column(db.String(64), nullable=False, index=True)  # FINALIZE, UNFINALIZE, STATUS_CHANGE
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    reason = db.Column(db.Text, nullable=True)

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

    visibility = db.Column(db.String(16), nullable=False, default=COMMENT_VISIBILITY_PUBLIC, index=True)
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

    visibility = db.Column(db.String(16), nullable=False, default=COMMENT_VISIBILITY_PUBLIC, index=True)
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


# ============================================================
# Activity telemetry + Notification logs (privacy-friendly)
# ============================================================

class ActivityEvent(db.Model):
    """High-volume access/action telemetry. Option 1 scope: log work item views/exports."""
    __tablename__ = "activity_events"

    id = db.Column(BigInteger, primary_key=True)

    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    actor_user_id = db.Column(db.String(64), nullable=True, index=True)

    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", name="fk_activity_events_work_type_id"),
        nullable=True,
        index=True,
    )

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_activity_events_work_item_id"),
        nullable=True,
        index=True,
    )

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_activity_events_work_line_id"),
        nullable=True,
        index=True,
    )

    event_type = db.Column(db.String(64), nullable=False, index=True)

    # anonymized identifiers (HMAC-derived). Do not store raw IP or raw UA.
    ip_anon_id = db.Column(db.String(64), nullable=True, index=True)
    ip_net_anon_id = db.Column(db.String(64), nullable=True, index=True)
    ua_anon_id = db.Column(db.String(64), nullable=True, index=True)

    correlation_id = db.Column(db.String(64), nullable=True, index=True)
    route = db.Column(db.String(128), nullable=True)
    http_method = db.Column(db.String(16), nullable=True)

    metadata_json = db.Column(db.Text, nullable=True)

    work_type = db.relationship("WorkType")
    work_item = db.relationship("WorkItem")
    work_line = db.relationship("WorkLine")

    __table_args__ = (
        # Composite index for time-series analytics queries
        db.Index("ix_activity_events_occurred_type", "occurred_at", "event_type"),
    )


class NotificationLog(db.Model):
    """Proof of notifications sent (email now, other channels later)."""
    __tablename__ = "notification_logs"

    id = db.Column(BigInteger, primary_key=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    sent_at = db.Column(db.DateTime, nullable=True, index=True)

    channel = db.Column(db.String(16), nullable=False, default="EMAIL", index=True)
    template_key = db.Column(db.String(64), nullable=False, index=True)

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_notification_logs_work_item_id"),
        nullable=True,
        index=True,
    )

    recipient_user_id = db.Column(db.String(64), nullable=True, index=True)
    recipient_email = db.Column(db.String(256), nullable=False, index=True)

    subject = db.Column(db.String(256), nullable=True)

    status = db.Column(db.String(16), nullable=False, default=NOTIF_STATUS_QUEUED, index=True)

    provider_message_id = db.Column(db.String(128), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    correlation_id = db.Column(db.String(64), nullable=True, index=True)
    metadata_json = db.Column(db.Text, nullable=True)

    work_item = db.relationship("WorkItem")

    __table_args__ = (
        # Composite index for queue processing (find pending notifications)
        db.Index("ix_notification_logs_status_created", "status", "created_at"),
    )


# ============================================================
# Budget configuration + per-event overrides + budget line details
# ============================================================

class SpendType(db.Model):
    __tablename__ = "spend_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)   # DIVVY, BANK
    name = db.Column(db.String(64), nullable=False)                            # Divvy, Bank
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class FrequencyOption(db.Model):
    __tablename__ = "frequency_options"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)   # ONE_TIME, RECURRING
    name = db.Column(db.String(64), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class ConfidenceLevel(db.Model):
    __tablename__ = "confidence_levels"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class PriorityLevel(db.Model):
    __tablename__ = "priority_levels"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class ExpenseAccount(db.Model):
    __tablename__ = "expense_accounts"

    id = db.Column(db.Integer, primary_key=True)

    code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    quickbooks_account_name = db.Column(db.String(128), nullable=True)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # Cross-module (future): approved budget in this account counts toward contract-eligible budget
    is_contract_eligible = db.Column(db.Boolean, nullable=False, default=False, index=True)

    # Spend type behavior (base defaults)
    spend_type_mode = db.Column(db.String(32), nullable=False, default=SPEND_TYPE_MODE_ALLOW_LIST, index=True)

    default_spend_type_id = db.Column(
        db.Integer,
        db.ForeignKey("spend_types.id", name="fk_expense_accounts_default_spend_type_id"),
        nullable=True,
        index=True,
    )

    # Department visibility behavior (base defaults)
    visibility_mode = db.Column(db.String(32), nullable=False, default=VISIBILITY_MODE_ALL, index=True)

    # Budget approval routing (L1 approvals)
    approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_expense_accounts_approval_group_id"),
        nullable=True,
        index=True,
    )

    # Fixed-cost behavior (base defaults; per-event overrides are preferred when pricing changes)
    is_fixed_cost = db.Column(db.Boolean, nullable=False, default=False, index=True)

    default_unit_price_cents = db.Column(db.Integer, nullable=True)
    unit_price_locked = db.Column(db.Boolean, nullable=False, default=False)

    default_frequency_id = db.Column(
        db.Integer,
        db.ForeignKey("frequency_options.id", name="fk_expense_accounts_default_frequency_id"),
        nullable=True,
        index=True,
    )
    frequency_locked = db.Column(db.Boolean, nullable=False, default=False)

    warehouse_default = db.Column(db.Boolean, nullable=False, default=False)

    # UI grouping + prompting
    ui_display_group = db.Column(db.String(32), nullable=True, index=True)
    prompt_mode = db.Column(db.String(32), nullable=False, default=PROMPT_MODE_NONE, index=True)

    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    default_spend_type = db.relationship("SpendType", foreign_keys=[default_spend_type_id])
    default_frequency = db.relationship("FrequencyOption", foreign_keys=[default_frequency_id])
    approval_group = db.relationship("ApprovalGroup", foreign_keys=[approval_group_id])

    allowed_spend_types = db.relationship(
        "SpendType",
        secondary="expense_account_spend_types",
        backref=db.backref("expense_accounts", lazy=True),
        lazy=True,
    )

    visible_to_departments = db.relationship(
        "Department",
        secondary="expense_account_departments",
        backref=db.backref("restricted_expense_accounts", lazy=True),
        lazy=True,
    )

    event_overrides = db.relationship(
        "ExpenseAccountEventOverride",
        backref="expense_account",
        cascade="all, delete-orphan",
        lazy=True,
    )


class ExpenseAccountSpendType(db.Model):
    __tablename__ = "expense_account_spend_types"

    expense_account_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_accounts.id", name="fk_east_expense_account_id"),
        primary_key=True,
    )
    spend_type_id = db.Column(
        db.Integer,
        db.ForeignKey("spend_types.id", name="fk_east_spend_type_id"),
        primary_key=True,
    )


class ExpenseAccountDepartment(db.Model):
    __tablename__ = "expense_account_departments"

    expense_account_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_accounts.id", name="fk_ead_expense_account_id"),
        primary_key=True,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_ead_department_id"),
        primary_key=True,
    )


class ConfigAuditEvent(db.Model):
    """Audit log for configuration changes (expense accounts, approval groups, etc.)."""
    __tablename__ = "config_audit_events"

    id = db.Column(db.Integer, primary_key=True)

    entity_type = db.Column(db.String(64), nullable=False, index=True)  # expense_account, approval_group, etc.
    entity_id = db.Column(db.Integer, nullable=False, index=True)

    action = db.Column(db.String(32), nullable=False, index=True)  # CREATE, UPDATE, ARCHIVE, RESTORE
    changes_json = db.Column(db.Text, nullable=True)  # JSON diff of changed fields

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)

    __table_args__ = (
        db.Index("ix_config_audit_entity", "entity_type", "entity_id"),
    )


class ExpenseAccountEventOverride(db.Model):
    __tablename__ = "expense_account_event_overrides"

    id = db.Column(db.Integer, primary_key=True)

    expense_account_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_accounts.id", name="fk_eaeo_expense_account_id"),
        nullable=False,
        index=True,
    )

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_eaeo_event_cycle_id"),
        nullable=False,
        index=True,
    )

    # Override fields (nullable = inherit from base ExpenseAccount)
    is_fixed_cost = db.Column(db.Boolean, nullable=True)

    default_unit_price_cents = db.Column(db.Integer, nullable=True)
    unit_price_locked = db.Column(db.Boolean, nullable=True)

    default_frequency_id = db.Column(
        db.Integer,
        db.ForeignKey("frequency_options.id", name="fk_eaeo_default_frequency_id"),
        nullable=True,
        index=True,
    )
    frequency_locked = db.Column(db.Boolean, nullable=True)

    warehouse_default = db.Column(db.Boolean, nullable=True)

    default_spend_type_id = db.Column(
        db.Integer,
        db.ForeignKey("spend_types.id", name="fk_eaeo_default_spend_type_id"),
        nullable=True,
        index=True,
    )

    ui_display_group = db.Column(db.String(32), nullable=True, index=True)
    prompt_mode = db.Column(db.String(32), nullable=True, index=True)

    __table_args__ = (
        db.UniqueConstraint("expense_account_id", "event_cycle_id", name="uq_eaeo_account_event"),
    )

    event_cycle = db.relationship("EventCycle")
    default_frequency = db.relationship("FrequencyOption", foreign_keys=[default_frequency_id])
    default_spend_type = db.relationship("SpendType", foreign_keys=[default_spend_type_id])


class BudgetLineDetail(db.Model):
    __tablename__ = "budget_line_details"

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_budget_line_details_work_line_id"),
        primary_key=True,
    )

    expense_account_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_accounts.id", name="fk_budget_line_details_expense_account_id"),
        nullable=False,
        index=True,
    )

    # Snapshot of routing at submission/review time (prevents history drift if mappings change)
    routed_approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_budget_line_details_routed_approval_group_id"),
        nullable=True,
        index=True,
    )

    spend_type_id = db.Column(
        db.Integer,
        db.ForeignKey("spend_types.id", name="fk_budget_line_details_spend_type_id"),
        nullable=False,
        index=True,
    )

    unit_price_cents = db.Column(db.Integer, nullable=False)
    quantity = db.Column(Numeric(12, 3), nullable=False, default=1)

    confidence_level_id = db.Column(
        db.Integer,
        db.ForeignKey("confidence_levels.id", name="fk_budget_line_details_confidence_level_id"),
        nullable=True,
        index=True,
    )

    frequency_id = db.Column(
        db.Integer,
        db.ForeignKey("frequency_options.id", name="fk_budget_line_details_frequency_id"),
        nullable=True,
        index=True,
    )

    warehouse_flag = db.Column(db.Boolean, nullable=False, default=False, index=True)

    priority_id = db.Column(
        db.Integer,
        db.ForeignKey("priority_levels.id", name="fk_budget_line_details_priority_id"),
        nullable=True,
        index=True,
    )

    description = db.Column(db.Text, nullable=True)

    work_line = db.relationship("WorkLine", backref=db.backref("budget_detail", uselist=False))
    expense_account = db.relationship("ExpenseAccount")
    routed_approval_group = db.relationship("ApprovalGroup", foreign_keys=[routed_approval_group_id])
    spend_type = db.relationship("SpendType")
    confidence_level = db.relationship("ConfidenceLevel")
    frequency = db.relationship("FrequencyOption")
    priority = db.relationship("PriorityLevel")

    __table_args__ = (
        # Composite index for approval group workload queries
        db.Index("ix_budget_line_details_approval_routing", "routed_approval_group_id", "expense_account_id"),
    )
