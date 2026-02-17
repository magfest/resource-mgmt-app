"""
Shared route helpers and blueprint registration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Any, Optional, Set
from flask import Flask
from flask import abort

from .. import db


@dataclass
class RouteHelpers:
    ensure_demo_users: Callable[[], None]
    ensure_demo_budget_data: Callable[[], None]
    ensure_demo_org_data: Callable[[], None]
    get_active_user_id: Callable[[], str]
    get_active_user: Callable[[], Any]
    active_user_roles: Callable[[], list[str]]
    is_admin: Callable[[], bool]
    is_finance: Callable[[], bool]
    active_user_approval_group_ids: Callable[[], set[int]]
    can_review_group: Callable[[int], bool]
    recalc_request_status_from_lines: Callable[[Any], None]


# Global helpers reference - set by register_all_routes()
h: RouteHelpers | None = None


@dataclass(frozen=True)
class UserContext:
    user_id: str
    user: object | None
    roles: tuple[str, ...]
    is_admin: bool
    is_finance: bool
    approval_group_ids: Set[int]


@dataclass(frozen=True)
class RequestPerms:
    can_view: bool
    can_edit: bool
    can_submit: bool
    can_finalize: bool

    # convenience flags
    is_owner: bool
    is_admin: bool
    is_finance: bool
    is_finalized: bool

@dataclass(frozen=True)
class DeptPerms:
    can_view: bool
    can_edit: bool
    is_department_head: bool


# Status constants
FINAL_REQUEST_STATUSES = {"APPROVED"}  # later: {"FINALIZED"} or both during transition
LINE_TERMINAL_STATUSES = {"APPROVED", "REJECTED"}
LINE_ACTIVE_STATUSES = {"PENDING", "NEEDS_INFO"} | LINE_TERMINAL_STATUSES
PRIORITY_OPTIONS = [
    ("CRITICAL", "Critical"),
    ("HIGH", "High"),
    ("MEDIUM", "Medium"),
    ("LOW", "Low"),
]
REQ_EDITABLE_STATUSES_OWNER = {"DRAFT", "NEEDS_REVISION"}
REQ_SUBMITTABLE_STATUSES_OWNER = {"DRAFT", "NEEDS_REVISION"}
REQ_EDITABLE_STATUSES_ADMIN = {"DRAFT", "NEEDS_REVISION", "SUBMITTED"}



def _require_helpers():
    if h is None:
        raise RuntimeError("Route helpers not initialized.")


def get_user_ctx() -> UserContext:
    _require_helpers()
    uid = h.get_active_user_id()
    u = h.get_active_user()
    roles = tuple(h.active_user_roles() or [])
    return UserContext(
        user_id=uid,
        user=u,
        roles=roles,
        is_admin=h.is_admin(),
        is_finance=h.is_finance(),
        approval_group_ids=set(h.active_user_approval_group_ids() or []),
    )

def _get_membership_for_request(req, *, user_ctx: UserContext):
    """
    Returns a DepartmentMembership row for this user + (dept, cycle), or None.
    MVP: exact match only.
    """
    from ..models_old import DepartmentMembership

    dept_id = getattr(req, "department_id", None)
    cycle_id = getattr(req, "event_cycle_id", None)

    if not dept_id or not cycle_id:
        return None

    return (
        db.session.query(DepartmentMembership)
        .filter(DepartmentMembership.user_id == user_ctx.user_id)
        .filter(DepartmentMembership.department_id == dept_id)
        .filter(DepartmentMembership.event_cycle_id == cycle_id)
        .one_or_none()
    )


def build_request_perms(req, *, user_ctx: UserContext, review_summary: dict | None = None) -> RequestPerms:
    status = (req.current_status or "").upper()
    is_finalized = status in FINAL_REQUEST_STATUSES

    is_owner = (user_ctx.user_id == req.created_by_user_id)

    membership = _get_membership_for_request(req, user_ctx=user_ctx)
    m_can_view = bool(membership and membership.can_view)
    m_can_edit = bool(membership and membership.can_edit)

    # ---- View ----
    # Admin/Finance always view. Owners always view. Dept members with can_view can view.
    can_view = user_ctx.is_admin or user_ctx.is_finance or is_owner or m_can_view

    # ---- Edit ----
    # Must not be finalized.
    # Admins can edit in any non-finalized state (keeps existing behavior).
    # Owners can edit only in owner-editable statuses.
    # Dept members with can_edit can edit in owner-editable statuses (same restriction as owner for now).
    can_edit = (not is_finalized) and (
        user_ctx.is_admin
        or (is_owner and status in REQ_EDITABLE_STATUSES_OWNER)
        or (m_can_edit and status in REQ_EDITABLE_STATUSES_OWNER)
    )

    # ---- Submit ----
    # Must not be finalized.
    # Admin: allow submit in your existing admin statuses.
    # Owner: allow submit in owner-submittable statuses.
    # Dept editors: allow submit in owner-submittable statuses (same as owner for now).
    can_submit = (not is_finalized) and (
        (user_ctx.is_admin and status in REQ_EDITABLE_STATUSES_ADMIN)
        or (is_owner and status in REQ_SUBMITTABLE_STATUSES_OWNER)
        or (m_can_edit and status in REQ_SUBMITTABLE_STATUSES_OWNER)
    )

    ready = bool(review_summary and review_summary.get("ready_to_finalize"))
    can_finalize = (
        (not is_finalized)
        and (status == "SUBMITTED")
        and ready
        and (user_ctx.is_admin or user_ctx.is_finance)
    )

    return RequestPerms(
        can_view=can_view,
        can_edit=can_edit,
        can_submit=can_submit,
        can_finalize=can_finalize,
        is_owner=is_owner,
        is_admin=user_ctx.is_admin,
        is_finance=user_ctx.is_finance,
        is_finalized=is_finalized,
    )

def user_can_edit_line_review(lr, *, user_ctx: UserContext) -> bool:
    if not lr:
        return False
    if user_ctx.is_admin or user_ctx.is_finance:
        return True
    return (
        lr.approval_group_id is not None
        and lr.approval_group_id in user_ctx.approval_group_ids
    )

def _validate_line_transition(current_status: str, action: str, note: str | None):
    """
    Returns (new_status, note_required_bool, clears_final_note_bool)
    Raises ValueError with a user-friendly message for invalid transitions.
    """
    s = (current_status or "PENDING").upper()
    a = (action or "").upper()
    note = (note or "").strip()

    if s not in LINE_ACTIVE_STATUSES:
        raise ValueError(f"Unknown line status: {s}")

    if s in LINE_TERMINAL_STATUSES:
        if a != "MARK_PENDING":
            raise ValueError(f"Invalid transition: {s} -> {a}")
        return ("PENDING", False, True)

    if a == "APPROVE":
        if not note:
            raise ValueError("Approval note is required.")
        return ("APPROVED", True, False)

    if a == "REJECT":
        if not note:
            raise ValueError("Rejection note is required.")
        return ("REJECTED", True, False)

    if a == "REQUEST_INFO":
        if not note:
            raise ValueError("A message is required when requesting info.")
        return ("NEEDS_INFO", True, False)

    if a == "MARK_PENDING":
        return ("PENDING", False, False)

    if a == "UPDATE_DECISION_NOTE":
        raise ValueError("Invalid action in this state.")

    raise ValueError("Invalid action.")


def _apply_line_review_transition(*, lr, action: str, note: str | None, internal_note: str | None = None):
    """
    Canonical line review transition implementation.
    """
    from datetime import datetime
    from ..models_old import LineAuditEvent, LineComment

    uid = h.get_active_user_id()

    old_status = (lr.status or "PENDING").upper()
    action_u = (action or "").upper()
    note_clean = (note or "").strip()

    new_status, _note_required, clear_final_note = _validate_line_transition(old_status, action_u, note_clean)

    lr.status = new_status
    lr.updated_by_user_id = uid

    if clear_final_note and hasattr(lr, "final_decision_note"):
        lr.final_decision_note = None
        lr.final_decision_at = None
        lr.final_decision_by_user_id = None

    if action_u == "MARK_PENDING":
        if old_status in ("NEEDS_INFO", "APPROVED", "REJECTED"):
            lr.external_admin_note = None
            lr.internal_admin_note = None

    if action_u == "REQUEST_INFO":
        lr.external_admin_note = note_clean
        lr.internal_admin_note = (internal_note or "").strip() or None

        db.session.add(LineComment(
            request_line_id=lr.request_line_id,
            visibility="PUBLIC",
            body=f"[Needs info] {note_clean}",
            created_by_user_id=uid,
        ))

    if new_status in ("APPROVED", "REJECTED"):
        if hasattr(lr, "final_decision_note"):
            lr.final_decision_note = note_clean
            lr.final_decision_at = datetime.utcnow()
            lr.final_decision_by_user_id = uid
        else:
            if not (lr.external_admin_note or "").strip():
                lr.external_admin_note = note_clean

    db.session.add(LineAuditEvent(
        request_line_id=lr.request_line_id,
        event_type="STATUS_CHANGE",
        old_value=old_status,
        new_value=f"{new_status} :: {note_clean}" if note_clean else new_status,
        created_by_user_id=uid,
    ))


def _get_or_create_line_review_for_line(line):
    """
    Returns (LineReview, created_bool).
    """
    from ..models_old import LineReview, BudgetItemType, ApprovalGroup

    if not getattr(line, "id", None):
        raise RuntimeError("RequestLine must be flushed before creating LineReview.")

    group_id = None
    if line.budget_item_type_id:
        bit = db.session.get(BudgetItemType, line.budget_item_type_id)
        if bit and bit.approval_group_id:
            group_id = bit.approval_group_id

    if group_id is None:
        other = (
            db.session.query(ApprovalGroup)
            .filter(ApprovalGroup.code == "OTHER")
            .one_or_none()
        )
        if not other:
            raise RuntimeError("Missing ApprovalGroup code=OTHER. Seed demo data.")
        group_id = other.id

    lr = (
        db.session.query(LineReview)
        .filter(LineReview.request_line_id == line.id)
        .filter(LineReview.approval_group_id == group_id)
        .one_or_none()
    )
    if lr:
        return lr, False

    lr = LineReview(
        request_line_id=line.id,
        approval_group_id=group_id,
        status="PENDING",
        updated_by_user_id=h.get_active_user_id(),
    )
    db.session.add(lr)
    return lr, True


def _ensure_line_reviews_for_revision(revision_id: int) -> int:
    """
    Ensures each RequestLine in the revision has the required LineReview row.
    Returns number of newly created LineReview rows.
    """
    from ..models_old import RequestLine

    lines = (
        db.session.query(RequestLine)
        .filter(RequestLine.revision_id == revision_id)
        .order_by(RequestLine.id.asc())
        .all()
    )

    created = 0
    for line in lines:
        _lr, was_created = _get_or_create_line_review_for_line(line)
        if was_created:
            created += 1

    return created


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _line_match_key(line) -> str:
    """Best-effort stable-ish key between revisions."""
    return "|".join([
        str(line.budget_item_type_id or ""),
        _norm(line.item_name or ""),
        _norm(line.description or ""),
    ])


def _require_admin_or_finance():
    if (not h.is_admin()) and (not h.is_finance()):
        abort(403)


def register_all_routes(app: Flask, helpers: RouteHelpers) -> None:
    """Register all blueprints with the Flask app."""
    global h
    h = helpers

    from .dev import dev_bp
    from .requests import requests_bp
    from .lines import lines_bp
    from .admin import admin_bp
    from .dashboard import dashboard_bp

    app.register_blueprint(dev_bp)
    app.register_blueprint(requests_bp)
    app.register_blueprint(lines_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(dashboard_bp)

def get_request_or_404(request_id: int):
    from ..models_old import Request

    req = db.session.get(Request, request_id)
    if not req:
        abort(404)
    return req


def require_can_view(req, *, review_summary: dict | None = None) -> RequestPerms:

    user_ctx = get_user_ctx()
    perms = build_request_perms(req, user_ctx=user_ctx, review_summary=review_summary)
    if not perms.can_view:
        abort(403)
    return perms

def require_can_edit(req, *, user_ctx: UserContext | None = None, review_summary: dict | None = None):
    user_ctx = user_ctx or get_user_ctx()
    perms = build_request_perms(req, user_ctx=user_ctx, review_summary=review_summary)
    if not perms.can_edit:
        abort(403, "Edit rights error")
    return perms

def require_can_submit(req, *, user_ctx: UserContext | None = None, review_summary: dict | None = None):
    user_ctx = user_ctx or get_user_ctx()
    perms = build_request_perms(req, user_ctx=user_ctx, review_summary=review_summary)
    if not perms.can_submit:
        abort(403)
    return perms

def render_request_page(template: str, *, req, review_summary: dict | None = None, **ctx):
    from flask import render_template
    perms = require_can_view(req, review_summary=review_summary)
    user_ctx = get_user_ctx()
    return render_template(
        template,
        req=req,
        user_ctx=user_ctx,
        perms=perms,
        review_summary=review_summary,
        **ctx
    )

def render_page(template: str, **ctx):
    from flask import render_template
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx, **ctx)


def render_admin_page(template: str, **ctx):
    from flask import render_template
    _require_admin_or_finance()
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx, **ctx)


def get_dept_perms_for_user(*, user_id: str, department_id: int, event_cycle_id: int | None) -> DeptPerms | None:
    """
    Returns DeptPerms for an exact department + event_cycle membership.
    For MVP, we do exact match only (no global rows).
    """
    from ..models_old import DepartmentMembership

    row = (
        db.session.query(DepartmentMembership)
        .filter(DepartmentMembership.user_id == user_id)
        .filter(DepartmentMembership.department_id == department_id)
        .filter(DepartmentMembership.event_cycle_id == event_cycle_id)
        .one_or_none()
    )
    if not row:
        return None

    return DeptPerms(
        can_view=bool(row.can_view),
        can_edit=bool(row.can_edit),
        is_department_head=bool(row.is_department_head),
    )

def get_editable_departments_for_user(*, user_id: str, event_cycle_id: int) -> list:
    """
    Returns Department rows the user can edit for a specific cycle.
    """
    from ..models_old import DepartmentMembership, Department

    rows = (
        db.session.query(Department)
        .join(DepartmentMembership, DepartmentMembership.department_id == Department.id)
        .filter(DepartmentMembership.user_id == user_id)
        .filter(DepartmentMembership.event_cycle_id == event_cycle_id)
        .filter(DepartmentMembership.can_edit.is_(True))
        .filter(Department.is_active.is_(True))
        .order_by(Department.sort_order.asc(), Department.name.asc())
        .all()
    )
    return rows


def can_user_create_main_request(*, user_ctx: UserContext, department_id: int, event_cycle_id: int) -> bool:
    if user_ctx.is_admin:
        return True
    from ..models_old import DepartmentMembership
    row = (
        db.session.query(DepartmentMembership.id)
        .filter(DepartmentMembership.user_id == user_ctx.user_id)
        .filter(DepartmentMembership.department_id == department_id)
        .filter(DepartmentMembership.event_cycle_id == event_cycle_id)
        .filter(DepartmentMembership.can_edit.is_(True))
        .first()
    )
    return bool(row)

def get_editable_departments(*, user_ctx: UserContext, event_cycle_id: int):
    from ..models_old import Department, DepartmentMembership
    q = db.session.query(Department).filter(Department.is_active.is_(True))
    if not user_ctx.is_admin:
        q = (
            q.join(DepartmentMembership, DepartmentMembership.department_id == Department.id)
             .filter(DepartmentMembership.user_id == user_ctx.user_id)
             .filter(DepartmentMembership.event_cycle_id == event_cycle_id)
             .filter(DepartmentMembership.can_edit.is_(True))
        )
    return q.order_by(Department.sort_order.asc(), Department.name.asc()).all()

