"""
Dashboard routes - approvals dashboard for reviewers.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template

from .. import db
from . import h

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.get("/dashboard/approvals")
def approvals_dashboard():
    from ..models_old import (
        ApprovalGroup,
        LineReview,
        RequestLine,
        RequestRevision,
        Request,
        BudgetItemType,
    )

    if h.is_admin():
        groups = (
            db.session.query(ApprovalGroup)
            .filter(ApprovalGroup.is_active == True)  # noqa: E712
            .order_by(ApprovalGroup.sort_order.asc(), ApprovalGroup.name.asc())
            .all()
        )
    else:
        group_ids = h.active_user_approval_group_ids()
        if not group_ids:
            return "Forbidden", 403

        groups = (
            db.session.query(ApprovalGroup)
            .filter(ApprovalGroup.id.in_(group_ids))
            .filter(ApprovalGroup.is_active == True)  # noqa: E712
            .order_by(ApprovalGroup.sort_order.asc(), ApprovalGroup.name.asc())
            .all()
        )

    cutoff = datetime.utcnow() - timedelta(hours=72)

    def base_q_for_group(group_id: int):
        return (
            db.session.query(LineReview, RequestLine, Request, BudgetItemType)
            .join(RequestLine, LineReview.request_line_id == RequestLine.id)
            .join(RequestRevision, RequestLine.revision_id == RequestRevision.id)
            .join(Request, RequestRevision.request_id == Request.id)
            .outerjoin(BudgetItemType, RequestLine.budget_item_type_id == BudgetItemType.id)
            .filter(LineReview.approval_group_id == group_id)
            .filter(Request.current_revision_id == RequestLine.revision_id)
        )

    queues_by_group_id = {}

    for g in groups:
        q = base_q_for_group(g.id)

        needs_review = (
            q.filter(LineReview.status == "PENDING")
            .order_by(Request.id.asc(), RequestLine.id.asc())
            .all()
        )

        needs_info = (
            q.filter(LineReview.status == "NEEDS_INFO")
            .order_by(LineReview.updated_at.desc(), Request.id.asc(), RequestLine.id.asc())
            .all()
        )

        recently_updated = (
            q.filter(LineReview.updated_at >= cutoff)
            .order_by(LineReview.updated_at.desc(), Request.id.asc(), RequestLine.id.asc())
            .all()
        )

        queues_by_group_id[g.id] = {
            "needs_review": needs_review,
            "kicked_back": needs_info,
            "recently_updated": recently_updated,
        }

    return render_template(
        "approvals_dashboard.html",
        groups=groups,
        queues_by_group_id=queues_by_group_id,
        cutoff=cutoff,
    )
