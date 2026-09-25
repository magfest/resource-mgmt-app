"""
TechOps work item detail view.

Renders a single TechOps request — the requester's view of what's been
submitted, plus reviewer/admin chrome (audit log, comments) when the
viewer has those roles.
"""
from flask import abort, render_template
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    TechOpsLineDetail,
    WorkItem,
    WorkItemAuditEvent,
    WorkLine,
    AUDIT_EVENT_VIEW,
    COMMENT_VISIBILITY_ADMIN,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    _is_approver_for_work_item,
    compute_work_item_totals,
    format_currency,
    friendly_status,
    get_portfolio_context,
    get_unified_audit_events,
    require_work_item_view,
)
from .preview import PHONE_LINE_NOT_CHOSEN
from .spaces import review_space_facts


@work_bp.get("/<event>/<dept>/techops/item/<public_id>")
def techops_work_item_detail(event: str, dept: str, public_id: str):
    """View a TechOps work item.

    Registered at the literal /techops/item/... segment, so Flask's URL
    matcher prefers it over BUDGET's generic /<work_type_slug>/item/...
    pattern. Cards built via url_for('work.work_item_detail', ...) for
    a TechOps slug build the same URL string and route here.
    """
    ctx = get_portfolio_context(event, dept, "techops")

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=ctx.portfolio.id,
            is_archived=False,
        )
        .options(
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.techops_detail)
                .joinedload(TechOpsLineDetail.service_type),
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.techops_detail)
                .joinedload(TechOpsLineDetail.routed_approval_group),
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.techops_detail)
                .joinedload(TechOpsLineDetail.space),
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.techops_detail)
                .joinedload(TechOpsLineDetail.parent_line),
            selectinload(WorkItem.comments),
            joinedload(WorkItem.techops_detail),
        )
        .first()
    )

    if not work_item:
        abort(404, f"TechOps work item not found: {public_id}")

    perms = require_work_item_view(work_item, ctx)
    user_ctx = get_user_ctx()

    # Log a VIEW event when a non-draft item is opened by someone other
    # than the requester (mirrors the BUDGET detail-view audit pattern).
    is_requester = work_item.created_by_user_id == user_ctx.user_id
    if work_item.status != WORK_ITEM_STATUS_DRAFT and not is_requester:
        db.session.add(WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_VIEW,
            created_by_user_id=user_ctx.user_id,
        ))
        db.session.commit()

    # Filter admin-only comments away from non-admin viewers
    comments = list(work_item.comments)
    if not perms.is_worktype_admin:
        comments = [c for c in comments if c.visibility != COMMENT_VISIBILITY_ADMIN]

    is_approver_for_item = _is_approver_for_work_item(work_item, user_ctx)
    can_add_comment = perms.is_worktype_admin or is_approver_for_item

    can_view_audit = user_ctx.is_super_admin or perms.is_worktype_admin
    audit_events = get_unified_audit_events(work_item) if can_view_audit else []

    # Per-line facts a reviewer needs but no line stores directly: the
    # space's display name (matching what the requester saw) and whether
    # the item's own department still holds that space for this event.
    space_facts = review_space_facts(work_item, ctx.department.id, ctx.event_cycle)

    return render_template(
        "techops/work_item_detail.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        request_detail=work_item.techops_detail,
        lines=list(work_item.lines),
        totals=compute_work_item_totals(work_item),
        format_currency=format_currency,
        friendly_status=friendly_status,
        filtered_comments=comments,
        can_add_comment=can_add_comment,
        audit_events=audit_events,
        can_view_audit=can_view_audit,
        user_ctx=user_ctx,
        space_facts=space_facts,
        phone_line_not_chosen=PHONE_LINE_NOT_CHOSEN,
    )
