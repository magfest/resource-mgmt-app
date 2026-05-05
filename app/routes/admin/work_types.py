"""
Admin routes for work type activation.

Work types themselves are seed-only — code, slug, public_id_prefix and
line_detail_type are wired across the codebase and cannot be changed at
runtime. The only admin operation surfaced here is toggling
``is_active``, which controls whether the worktype appears in the
request-creation pickers, role assignment forms, and division/
department work-type access editors. Already-created portfolios remain
reachable by direct URL even when their work type is inactive.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, abort, flash

from sqlalchemy import func

from app import db
from app.models import (
    ApprovalGroup,
    WorkPortfolio,
    WorkType,
    CONFIG_AUDIT_ARCHIVE,
    CONFIG_AUDIT_RESTORE,
)
from .helpers import (
    require_super_admin,
    render_admin_config_page,
    log_config_change,
    sort_with_override,
)

work_types_bp = Blueprint('work_types', __name__, url_prefix='/work-types')


def _get_work_type_or_404(work_type_id: int) -> WorkType:
    work_type = db.session.get(WorkType, work_type_id)
    if not work_type:
        abort(404, "Work type not found")
    return work_type


@work_types_bp.get("/")
@require_super_admin
def list_work_types():
    """List all work types with active/inactive status and usage counts."""
    work_types = (
        db.session.query(WorkType)
        .order_by(*sort_with_override(WorkType))
        .all()
    )

    portfolio_counts = dict(
        db.session.query(
            WorkPortfolio.work_type_id,
            func.count(WorkPortfolio.id),
        ).group_by(WorkPortfolio.work_type_id).all()
    )

    approval_group_counts = dict(
        db.session.query(
            ApprovalGroup.work_type_id,
            func.count(ApprovalGroup.id),
        ).filter(ApprovalGroup.is_active == True).group_by(ApprovalGroup.work_type_id).all()
    )

    return render_admin_config_page(
        "admin/work_types/list.html",
        work_types=work_types,
        portfolio_counts=portfolio_counts,
        approval_group_counts=approval_group_counts,
    )


@work_types_bp.post("/<int:work_type_id>/archive")
@require_super_admin
def deactivate_work_type(work_type_id: int):
    """Mark a work type inactive (hides from new-entry pickers)."""
    work_type = _get_work_type_or_404(work_type_id)

    if not work_type.is_active:
        flash(f"{work_type.name} is already inactive", "warning")
        return redirect(url_for(".list_work_types"))

    work_type.is_active = False
    log_config_change(
        "work_type",
        work_type.id,
        CONFIG_AUDIT_ARCHIVE,
        {"is_active": {"old": True, "new": False}},
    )

    db.session.commit()
    flash(f"Deactivated work type: {work_type.name}", "success")
    return redirect(url_for(".list_work_types"))


@work_types_bp.post("/<int:work_type_id>/restore")
@require_super_admin
def activate_work_type(work_type_id: int):
    """Mark a work type active (shows in pickers again)."""
    work_type = _get_work_type_or_404(work_type_id)

    if work_type.is_active:
        flash(f"{work_type.name} is already active", "warning")
        return redirect(url_for(".list_work_types"))

    work_type.is_active = True
    log_config_change(
        "work_type",
        work_type.id,
        CONFIG_AUDIT_RESTORE,
        {"is_active": {"old": False, "new": True}},
    )

    db.session.commit()
    flash(f"Activated work type: {work_type.name}", "success")
    return redirect(url_for(".list_work_types"))
