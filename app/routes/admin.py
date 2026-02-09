"""
Admin routes - budget items, approval summaries, spend summaries.
"""
from flask import Blueprint, render_template, redirect, url_for, request, abort
from sqlalchemy import func, case

from .. import db
from . import h, _require_admin_or_finance, render_admin_page

admin_bp = Blueprint('admin', __name__)


@admin_bp.get("/admin/demo/approval-summary")
def admin_demo_approval_summary():
    from ..models import ApprovalGroup, LineReview, RequestLine, RequestRevision, Request

    _require_admin_or_finance()

    base = (
        db.session.query(
            ApprovalGroup.id.label("group_id"),
            ApprovalGroup.name.label("group_name"),

            func.sum(case((LineReview.status == "PENDING", 1), else_=0)).label("pending_count"),
            func.sum(case((LineReview.status == "NEEDS_INFO", 1), else_=0)).label("needs_info_count"),
            func.sum(case((LineReview.status == "APPROVED", 1), else_=0)).label("approved_count"),
            func.sum(case((LineReview.status == "REJECTED", 1), else_=0)).label("rejected_count"),

            func.sum(case((LineReview.status == "PENDING", RequestLine.requested_amount), else_=0)).label(
                "pending_amount"),
            func.sum(case((LineReview.status == "NEEDS_INFO", RequestLine.requested_amount), else_=0)).label(
                "needs_info_amount"),
            func.sum(
                case(
                    (LineReview.status == "APPROVED",
                     func.coalesce(LineReview.approved_amount, RequestLine.requested_amount)),
                    else_=0,
                )
            ).label("approved_amount"),
            func.sum(case((LineReview.status == "REJECTED", RequestLine.requested_amount), else_=0)).label(
                "rejected_amount"),

            func.count(func.distinct(Request.id)).label("request_count"),
        )
        .select_from(ApprovalGroup)
        .join(LineReview, LineReview.approval_group_id == ApprovalGroup.id)
        .join(RequestLine, LineReview.request_line_id == RequestLine.id)
        .join(RequestRevision, RequestLine.revision_id == RequestRevision.id)
        .join(Request, RequestRevision.request_id == Request.id)
        .filter(ApprovalGroup.is_active == True)  # noqa: E712
        .filter(Request.current_revision_id == RequestLine.revision_id)
        .group_by(ApprovalGroup.id, ApprovalGroup.name, ApprovalGroup.sort_order)
        .order_by(ApprovalGroup.sort_order.asc(), ApprovalGroup.name.asc())
    )

    rows = []
    for r in base.all():
        def nz(x): return int(x or 0)

        rows.append({
            "group_id": r.group_id,
            "group_name": r.group_name,
            "request_count": nz(r.request_count),

            "pending_count": nz(r.pending_count),
            "needs_info_count": nz(r.needs_info_count),
            "approved_count": nz(r.approved_count),
            "rejected_count": nz(r.rejected_count),

            "pending_amount": nz(r.pending_amount),
            "needs_info_amount": nz(r.needs_info_amount),
            "approved_amount": nz(r.approved_amount),
            "rejected_amount": nz(r.rejected_amount),
        })

    totals = {
        "request_count": sum(x["request_count"] for x in rows),
        "pending_count": sum(x["pending_count"] for x in rows),
        "needs_info_count": sum(x["needs_info_count"] for x in rows),
        "approved_count": sum(x["approved_count"] for x in rows),
        "rejected_count": sum(x["rejected_count"] for x in rows),
        "pending_amount": sum(x["pending_amount"] for x in rows),
        "needs_info_amount": sum(x["needs_info_amount"] for x in rows),
        "approved_amount": sum(x["approved_amount"] for x in rows),
        "rejected_amount": sum(x["rejected_amount"] for x in rows),
    }

    return render_admin_page(
        "admin_demo_approval_summary.html",
        rows=rows,
        totals=totals,
    )


@admin_bp.get("/admin/demo/spend-summary")
def admin_demo_spend_summary():
    from ..models import Request, RequestRevision, RequestLine, BudgetItemType

    _require_admin_or_finance()

    include_statuses = ("SUBMITTED", "NEEDS_REVISION", "APPROVED")

    spend_type_expr = func.coalesce(BudgetItemType.spend_type, "Unassigned").label("spend_type")

    q = (
        db.session.query(
            spend_type_expr,
            func.count(RequestLine.id).label("line_count"),
            func.sum(RequestLine.requested_amount).label("total_amount"),
        )
        .select_from(RequestLine)
        .join(RequestRevision, RequestLine.revision_id == RequestRevision.id)
        .join(Request, RequestRevision.request_id == Request.id)
        .outerjoin(BudgetItemType, RequestLine.budget_item_type_id == BudgetItemType.id)
        .filter(Request.current_status.in_(include_statuses))
        .filter(Request.current_revision_id == RequestLine.revision_id)
        .group_by(spend_type_expr)
        .order_by(func.sum(RequestLine.requested_amount).desc())
    )

    rows = []
    for r in q.all():
        total = int(r.total_amount or 0)
        rows.append(
            {
                "spend_type": r.spend_type,
                "line_count": int(r.line_count or 0),
                "total_amount": total,
            }
        )

    grand_total = sum(x["total_amount"] for x in rows)
    max_total = max([x["total_amount"] for x in rows], default=0)

    return render_admin_page(
        "admin_demo_spend_summary.html",
        rows=rows,
        grand_total=grand_total,
        max_total=max_total,
        include_statuses=list(include_statuses),
    )


@admin_bp.get("/admin/budget-items")
def admin_budget_items():
    from ..models import BudgetItemType, ApprovalGroup

    _require_admin_or_finance()

    q = (request.args.get("q") or "").strip()
    show_inactive = (request.args.get("show_inactive") == "1")

    query = db.session.query(BudgetItemType).join(ApprovalGroup)
    if not show_inactive:
        query = query.filter(BudgetItemType.is_active == True)  # noqa: E712

    if q:
        like = f"%{q}%"
        query = query.filter(
            (BudgetItemType.item_id.ilike(like))
            | (BudgetItemType.item_name.ilike(like))
            | (BudgetItemType.spend_type.ilike(like))
        )

    items = query.order_by(BudgetItemType.item_id.asc()).all()

    return render_admin_page(
        "admin_budget_items.html",
        items=items,
        q=q,
        show_inactive=show_inactive,
    )


@admin_bp.get("/admin/budget-items/new")
def admin_budget_items_new():
    from ..models import ApprovalGroup
    _require_admin_or_finance()

    groups = (
        db.session.query(ApprovalGroup)
        .filter(ApprovalGroup.is_active == True)  # noqa: E712
        .order_by(ApprovalGroup.sort_order.asc(), ApprovalGroup.name.asc())
        .all()
    )
    return render_admin_page("admin_budget_item_form.html", item=None, groups=groups)


@admin_bp.post("/admin/budget-items/new")
def admin_budget_items_new_post():
    from ..models import BudgetItemType, ApprovalGroup
    _require_admin_or_finance()

    item_id = (request.form.get("item_id") or "").strip()
    item_name = (request.form.get("item_name") or "").strip()
    item_description = (request.form.get("item_description") or "").strip() or None
    spend_type = (request.form.get("spend_type") or "").strip()
    spend_group = (request.form.get("spend_group") or "").strip() or None
    approval_group_id = request.form.get("approval_group_id")
    is_active = (request.form.get("is_active") == "1")

    if not item_id or not item_name or not spend_type or not approval_group_id:
        return "Missing required fields.", 400

    if db.session.query(BudgetItemType).filter(BudgetItemType.item_id == item_id).first():
        return f"item_id already exists: {item_id}", 400

    group = db.session.get(ApprovalGroup, int(approval_group_id))
    if not group or not group.is_active:
        return "Invalid approval group.", 400

    item = BudgetItemType(
        item_id=item_id,
        item_name=item_name,
        item_description=item_description,
        spend_type=spend_type,
        spend_group=spend_group,
        approval_group_id=group.id,
        is_active=is_active,
    )
    db.session.add(item)
    db.session.commit()
    return redirect(url_for("admin.admin_budget_items"))


@admin_bp.get("/admin/budget-items/<int:item_type_id>/edit")
def admin_budget_items_edit(item_type_id: int):
    from ..models import BudgetItemType, ApprovalGroup
    _require_admin_or_finance()

    item = db.session.get(BudgetItemType, item_type_id)
    if not item:
        abort(404)

    groups = (
        db.session.query(ApprovalGroup)
        .filter(ApprovalGroup.is_active == True)  # noqa: E712
        .order_by(ApprovalGroup.sort_order.asc(), ApprovalGroup.name.asc())
        .all()
    )
    return render_admin_page("admin_budget_item_form.html", item=item, groups=groups)


@admin_bp.post("/admin/budget-items/<int:item_type_id>/edit")
def admin_budget_items_edit_post(item_type_id: int):
    from ..models import BudgetItemType, ApprovalGroup
    _require_admin_or_finance()

    item = db.session.get(BudgetItemType, item_type_id)
    if not item:
        abort(404)

    item_id = (request.form.get("item_id") or "").strip()
    item_name = (request.form.get("item_name") or "").strip()
    item_description = (request.form.get("item_description") or "").strip() or None
    spend_type = (request.form.get("spend_type") or "").strip()
    spend_group = (request.form.get("spend_group") or "").strip() or None
    approval_group_id = request.form.get("approval_group_id")
    is_active = (request.form.get("is_active") == "1")

    if not item_id or not item_name or not spend_type or not approval_group_id:
        return "Missing required fields.", 400

    existing = (
        db.session.query(BudgetItemType)
        .filter(BudgetItemType.item_id == item_id, BudgetItemType.id != item.id)
        .first()
    )
    if existing:
        return f"item_id already exists: {item_id}", 400

    group = db.session.get(ApprovalGroup, int(approval_group_id))
    if not group or not group.is_active:
        return "Invalid approval group.", 400

    item.item_id = item_id
    item.item_name = item_name
    item.item_description = item_description
    item.spend_type = spend_type
    item.spend_group = spend_group
    item.approval_group_id = group.id
    item.is_active = is_active

    db.session.commit()
    return redirect(url_for("admin.admin_budget_items"))
