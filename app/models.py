from __future__ import annotations

from datetime import datetime
from app import db

# -----------------------------
# Constants / helpers
# -----------------------------

COMMENT_VISIBILITY_PUBLIC = "PUBLIC"
COMMENT_VISIBILITY_ADMIN = "ADMIN"  # admin/approvers/finance only (not requester)

# Line workflow states (Chunk A only; we can refine later)
LINE_STATUS_PENDING = "PENDING"
LINE_STATUS_NEEDS_INFO = "NEEDS_INFO"
LINE_STATUS_APPROVED = "APPROVED"
LINE_STATUS_REJECTED = "REJECTED"  # optional later


# -----------------------------
# Core request + revision
# -----------------------------

class Request(db.Model):
    __tablename__ = "requests"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=True)

    event_cycle = db.Column(db.String(64), nullable=False)  # old should be removed after MVP
    requesting_department = db.Column(db.String(64), nullable=False)

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_requests_event_cycle_id"),
        nullable=True,
        index=True,
    )

    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_requests_department_id"),
        nullable=True,
        index=True,
    )

    event_cycle_obj = db.relationship("EventCycle")
    department = db.relationship("Department")

    created_by_user_id = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # DRAFT / SUBMITTED / NEEDS_REVISION / APPROVED
    current_status = db.Column(db.String(32), nullable=False, default="DRAFT")

    # Whole-request kickback reason (unlock edits)
    kickback_reason = db.Column(db.Text, nullable=True)

    current_revision_id = db.Column(
        db.Integer,
        db.ForeignKey("request_revisions.id"),
        nullable=True,
    )

    approved_revision_id = db.Column(
        db.Integer,
        db.ForeignKey("request_revisions.id"),
        nullable=True,
    )
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_by_user_id = db.Column(db.String(64), nullable=True)

    final_approval_note = db.Column(db.Text, nullable=True)

    closed_at = db.Column(db.DateTime, nullable=True)

    revisions = db.relationship(
        "RequestRevision",
        backref="request",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="RequestRevision.request_id",
    )

    current_revision = db.relationship(
        "RequestRevision",
        foreign_keys=[current_revision_id],
        primaryjoin="Request.current_revision_id == RequestRevision.id",
        uselist=False,
        post_update=True,
    )

    approved_revision = db.relationship(
        "RequestRevision",
        foreign_keys=[approved_revision_id],
        primaryjoin="Request.approved_revision_id == RequestRevision.id",
        uselist=False,
        post_update=True,
    )


class RequestRevision(db.Model):
    __tablename__ = "request_revisions"

    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False, index=True)
    revision_number = db.Column(db.Integer, nullable=False)

    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    submitted_by_user_id = db.Column(db.String(64), nullable=False)

    revision_note = db.Column(db.Text, nullable=True)

    status_at_submission = db.Column(db.String(32), nullable=False, default="SUBMITTED")

    __table_args__ = (
        db.UniqueConstraint("request_id", "revision_number", name="uq_request_revision_number"),
    )


class RequestLine(db.Model):
    __tablename__ = "request_lines"

    id = db.Column(db.Integer, primary_key=True)

    revision_id = db.Column(
        db.Integer,
        db.ForeignKey("request_revisions.id", name="fk_request_lines_revision_id"),
        nullable=False,
        index=True,
    )

    # legacy/freeform category (still useful for grouping)
    category = db.Column(db.String(64), nullable=False)

    # freeform details
    item_name = db.Column(db.String(128), nullable=True)
    description = db.Column(db.Text, nullable=False)

    requested_amount = db.Column(db.Integer, nullable=False)  # dollars for now
    justification = db.Column(db.Text, nullable=False)

    priority = db.Column(db.String(32), nullable=False, default="")
    reason = db.Column(db.Text, nullable=False, default="")

    # Optional typed item selection
    budget_item_type_id = db.Column(
        db.Integer,
        db.ForeignKey("budget_item_types.id", name="fk_request_lines_budget_item_type_id"),
        nullable=True,
        index=True,
    )

    # NOTE: In the new workflow, requester feedback is not a single field.
    # We'll keep this for backwards-compatibility, but the real thread lives in LineComment.
    requester_comment = db.Column(db.Text, nullable=True)

    revision = db.relationship("RequestRevision", backref="lines")
    budget_item_type = db.relationship("BudgetItemType")

    public_line_number = db.Column(db.Integer, nullable=True, index=True)  # NEW

    __table_args__ = (
        db.UniqueConstraint("revision_id", "public_line_number", name="uq_revision_public_line_number"),
    )


# -----------------------------
# Draft editing models
# -----------------------------

class RequestDraft(db.Model):
    __tablename__ = "request_drafts"

    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(
        db.Integer,
        db.ForeignKey("requests.id", name="fk_request_drafts_request_id"),
        nullable=False,
        unique=True,
        index=True,
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    request = db.relationship("Request", backref=db.backref("draft", uselist=False))
    lines = db.relationship(
        "DraftLine",
        backref="draft",
        cascade="all, delete-orphan",
        order_by="DraftLine.sort_order.asc()",
        lazy=True,
    )


class DraftLine(db.Model):
    __tablename__ = "draft_lines"

    id = db.Column(db.Integer, primary_key=True)

    draft_id = db.Column(
        db.Integer,
        db.ForeignKey("request_drafts.id", name="fk_draft_lines_draft_id"),
        nullable=False,
        index=True,
    )

    category = db.Column(db.String(64), nullable=False, default="")
    item_name = db.Column(db.String(128), nullable=True)

    budget_item_type_id = db.Column(
        db.Integer,
        db.ForeignKey("budget_item_types.id", name="fk_draft_lines_budget_item_type_id"),
        nullable=True,
        index=True,
    )

    description = db.Column(db.Text, nullable=False, default="")
    requested_amount = db.Column(db.Integer, nullable=False, default=0)
    justification = db.Column(db.Text, nullable=False, default="")

    sort_order = db.Column(db.Integer, nullable=False, default=0, index=True)

    budget_item_type = db.relationship("BudgetItemType")
    priority = db.Column(db.String(32), nullable=False, default="")
    reason = db.Column(db.Text, nullable=False, default="")


# -----------------------------
# Approval groups + item types
# -----------------------------

class ApprovalGroup(db.Model):
    __tablename__ = "approval_groups"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)  # TECH, HOTEL, OTHER
    name = db.Column(db.String(128), nullable=False)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class BudgetItemType(db.Model):
    __tablename__ = "budget_item_types"

    id = db.Column(db.Integer, primary_key=True)

    item_id = db.Column(db.String(64), unique=True, nullable=False)
    item_name = db.Column(db.String(128), nullable=False)
    item_description = db.Column(db.Text, nullable=True)

    spend_type = db.Column(db.String(64), nullable=False)

    spend_group = db.Column(db.String(64), nullable=True)

    approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_budget_item_types_approval_group_id"),
        nullable=False,
        index=True,
    )

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    approval_group = db.relationship("ApprovalGroup", backref="budget_item_types")


# -----------------------------
# Review state (NOT the comment thread)
# -----------------------------

class LineReview(db.Model):
    """
    In the corrected workflow:
    - LineReview represents the per-line workflow owner + state (by approval group).
    - Conversation lives in LineComment.
    - Audit/event history lives in LineAuditEvent.
    - Final approval note will be added in Chunk D (short prominent note).
    """

    __tablename__ = "line_reviews"

    id = db.Column(db.Integer, primary_key=True)

    request_line_id = db.Column(
        db.Integer,
        db.ForeignKey("request_lines.id", name="fk_line_reviews_request_line_id"),
        nullable=False,
        index=True,
    )

    approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_line_reviews_approval_group_id"),
        nullable=False,
        index=True,
    )

    # PENDING / NEEDS_INFO / APPROVED (KICKED_BACK is deprecated; we'll migrate later)
    status = db.Column(db.String(32), nullable=False, default=LINE_STATUS_PENDING)

    # Keep these columns for backwards compatibility with your current UI,
    # but treat them as "legacy" and stop using them once LineComment is wired.
    internal_admin_note = db.Column(db.Text, nullable=True)
    external_admin_note = db.Column(db.Text, nullable=True)

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    updated_by_user_id = db.Column(db.String(64), nullable=True)

    approved_amount = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.UniqueConstraint("request_line_id", "approval_group_id", name="uq_line_review_once"),
    )

    request_line = db.relationship("RequestLine", backref="line_reviews")
    approval_group = db.relationship("ApprovalGroup")

    final_decision_note = db.Column(db.Text, nullable=True)  # PUBLIC rationale shown to requester
    final_decision_at = db.Column(db.DateTime, nullable=True)
    final_decision_by_user_id = db.Column(db.String(64), nullable=True)


# -----------------------------
# NEW: Comments (threads)
# -----------------------------

class RequestComment(db.Model):
    __tablename__ = "request_comments"

    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(
        db.Integer,
        db.ForeignKey("requests.id", name="fk_request_comments_request_id"),
        nullable=False,
        index=True,
    )

    visibility = db.Column(db.String(16), nullable=False, default=COMMENT_VISIBILITY_PUBLIC)
    body = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=False)

    request = db.relationship("Request", backref=db.backref("comments", lazy=True, cascade="all, delete-orphan"))


class LineComment(db.Model):
    __tablename__ = "line_comments"

    id = db.Column(db.Integer, primary_key=True)

    request_line_id = db.Column(
        db.Integer,
        db.ForeignKey("request_lines.id", name="fk_line_comments_request_line_id"),
        nullable=False,
        index=True,
    )

    visibility = db.Column(db.String(16), nullable=False, default=COMMENT_VISIBILITY_PUBLIC)
    body = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=False)

    request_line = db.relationship("RequestLine",
                                   backref=db.backref("comments", lazy=True, cascade="all, delete-orphan"))


# -----------------------------
# NEW: Audit / event logs
# -----------------------------

class RequestAuditEvent(db.Model):
    __tablename__ = "request_audit_events"

    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(
        db.Integer,
        db.ForeignKey("requests.id", name="fk_request_audit_request_id"),
        nullable=False,
        index=True,
    )

    event_type = db.Column(db.String(64), nullable=False)  # e.g. SUBMITTED, KICKED_BACK, FINAL_APPROVED
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=False)

    request = db.relationship("Request", backref=db.backref("audit_events", lazy=True, cascade="all, delete-orphan"))


class LineAuditEvent(db.Model):
    __tablename__ = "line_audit_events"

    id = db.Column(db.Integer, primary_key=True)

    request_line_id = db.Column(
        db.Integer,
        db.ForeignKey("request_lines.id", name="fk_line_audit_request_line_id"),
        nullable=False,
        index=True,
    )

    # e.g. REQUEST_INFO, STATUS_CHANGE, AMOUNT_CHANGE, FINAL_NOTE_SET
    event_type = db.Column(db.String(64), nullable=False)

    # optional detail blobs (text for sqlite simplicity; can be JSON later)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=False)

    request_line = db.relationship("RequestLine",
                                   backref=db.backref("audit_events", lazy=True, cascade="all, delete-orphan"))


# -----------------------------
# Users / roles / Membership
# -----------------------------

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(64), primary_key=True)  # keep for now (internal key)
    email = db.Column(db.String(256), nullable=False, unique=True, index=True)  # NEW
    auth_subject = db.Column(db.String(255), nullable=True, unique=True, index=True)  # NEW

    display_name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    roles = db.relationship(
        "UserRole",
        backref="user",
        cascade="all, delete-orphan",
        lazy=True,
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

    # ADMIN, APPROVER, FINANCE, REQUESTER
    role_code = db.Column(db.String(32), nullable=False, index=True)

    approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_user_roles_approval_group_id"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", "role_code", name="uq_user_role_once"),
    )


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
        nullable=True,
        index=True,
    )

    can_view = db.Column(db.Boolean, nullable=False, default=True)
    can_edit = db.Column(db.Boolean, nullable=False, default=False)
    is_department_head = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship("User", backref=db.backref("department_memberships", lazy=True))
    department = db.relationship("Department")
    event_cycle = db.relationship("EventCycle")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "department_id", "event_cycle_id",
            name="uq_dept_membership_user_dept_cycle",
        ),
    )


# -----------------------------
# Departments / Events
# -----------------------------

class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)  # TECHOPS, HOTELS, etc.
    name = db.Column(db.String(128), nullable=False)

    description = db.Column(db.Text, nullable=True)
    mailing_list = db.Column(db.String(256), nullable=True)
    slack_channel = db.Column(db.String(128), nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class EventCycle(db.Model):
    __tablename__ = "event_cycles"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)  # SMF2026
    name = db.Column(db.String(128), nullable=False)  # Super MAGFest 2026

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
