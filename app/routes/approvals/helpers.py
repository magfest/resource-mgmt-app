"""
Approval workflow helpers - review-specific helper functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Set, Tuple, List, Optional

from flask import abort
from sqlalchemy.orm import joinedload, selectinload, contains_eager

from app import db
from app.line_details import (
    LineDetail,
    get_line_detail,
    get_line_amount_cents,
    get_line_routing_approval_group,
)
from app.models import (
    WorkLine,
    WorkLineReview,
    WorkLineAuditEvent,
    WorkItemAuditEvent,
    WorkItem,
    WorkPortfolio,
    ApprovalGroup,
    EventCycle,
    Department,
    BudgetLineDetail,
    ContractLineDetail,
    SupplyOrderLineDetail,
    User,
    UserRole,
    WorkType,
    ROLE_WORKTYPE_ADMIN,
    ROLE_WORKTYPE_VIEWER,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
    REVIEW_STATUS_REJECTED,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW,
    WORK_LINE_STATUS_REJECTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_APPROVE_NEEDS_REVIEW,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_NEEDS_INFO,
    REVIEW_ACTION_NEEDS_ADJUSTMENT,
    REVIEW_ACTION_RESPOND,
    REVIEW_ACTION_REOPEN,
    AUDIT_EVENT_REVIEW_DECISION,
    AUDIT_EVENT_REQUESTER_RESPONSE,
    AUDIT_EVENT_LINE_CREATED,
    AUDIT_EVENT_FIELD_CHANGE,
    AUDIT_EVENT_LINE_DELETED,
    AUDIT_EVENT_UNFINALIZE,
    ROLE_APPROVER,
)
from app.routes import get_user_ctx, UserContext


# ============================================================
# Valid Transitions Table
# ============================================================

# Maps (current_status, action) -> (new_status, note_required, allowed_roles)
# allowed_roles: "APPROVER" = approvers for the group, "REQUESTER" = requester, "ADMIN" = admin only
VALID_TRANSITIONS = {
    # From PENDING
    (REVIEW_STATUS_PENDING, REVIEW_ACTION_APPROVE): (REVIEW_STATUS_APPROVED, False, "APPROVER"),
    (REVIEW_STATUS_PENDING, REVIEW_ACTION_APPROVE_NEEDS_REVIEW):
        (REVIEW_STATUS_APPROVED_NEEDS_REVIEW, True, "APPROVER"),
    (REVIEW_STATUS_PENDING, REVIEW_ACTION_REJECT): (REVIEW_STATUS_REJECTED, True, "APPROVER"),
    (REVIEW_STATUS_PENDING, REVIEW_ACTION_NEEDS_INFO): (REVIEW_STATUS_NEEDS_INFO, True, "APPROVER"),
    (REVIEW_STATUS_PENDING, REVIEW_ACTION_NEEDS_ADJUSTMENT): (REVIEW_STATUS_NEEDS_ADJUSTMENT, True, "APPROVER"),

    # From NEEDS_INFO
    (REVIEW_STATUS_NEEDS_INFO, REVIEW_ACTION_RESPOND): (REVIEW_STATUS_PENDING, True, "REQUESTER"),

    # From NEEDS_ADJUSTMENT
    (REVIEW_STATUS_NEEDS_ADJUSTMENT, REVIEW_ACTION_RESPOND): (REVIEW_STATUS_PENDING, True, "REQUESTER"),

    # From a decision, group-final work types only (validate_review_transition guards)
    (REVIEW_STATUS_APPROVED, REVIEW_ACTION_REOPEN): (REVIEW_STATUS_PENDING, True, "APPROVER"),
    (REVIEW_STATUS_REJECTED, REVIEW_ACTION_REOPEN): (REVIEW_STATUS_PENDING, True, "APPROVER"),
}


@dataclass(frozen=True)
class ReviewQueueItem:
    """A line item in the review queue (used for kicked-back lines).

    `detail` is the type-specific line detail (BudgetLineDetail,
    ContractLineDetail, SupplyOrderLineDetail, etc.). Templates that
    show worktype-specific columns dispatch on
    work_item.portfolio.work_type.code.
    """
    work_item: WorkItem
    work_line: WorkLine
    review: WorkLineReview
    detail: Optional[LineDetail]
    line_total_cents: int


@dataclass(frozen=True)
class RequestQueueItem:
    """A request-level item in the review queue."""
    work_item: WorkItem
    event_cycle: "EventCycle"
    department: "Department"
    pending_line_count: int
    total_line_count: int
    total_requested_cents: int


@dataclass(frozen=True)
class ApprovalQueues:
    """Queues for the approval dashboard."""
    pending_requests: List[RequestQueueItem]  # Request-level view
    kicked_back: List[ReviewQueueItem]  # Line-level (needs specific action)
    recently_decided_requests: List[RequestQueueItem]  # Request-level view
    pending_request_count: int
    pending_line_count: int
    kicked_back_count: int
    recently_decided_count: int


# ============================================================
# Permission Checks
# ============================================================

def is_reviewer_for_line(line: WorkLine, user_ctx: UserContext) -> bool:
    """Return whether the user may act as a reviewer of this line.

    Super admins and members of the routed group always may. On a group-final
    work type an admin of that work type also may, because no admin-final
    stage exists to hold their authority instead.
    """
    if user_ctx.is_super_admin:
        return True

    routed_group = get_line_routing_approval_group(line)
    if routed_group is not None and routed_group.id in user_ctx.approval_group_ids:
        return True

    # Local imports: see the circular-import note in approvals/reviews.py.
    from app.routes.work.helpers.context import is_worktype_admin
    from app.routes.work.helpers.review_state import is_group_final
    work_item = line.work_item
    return is_group_final(work_item) and is_worktype_admin(
        user_ctx, work_item.portfolio.work_type_id,
    )


def can_view_work_item_lines(work_item: WorkItem, ctx, user_ctx: UserContext) -> bool:
    """Check if user may READ the lines of a work item they requested.

    The read counterpart of can_respond_to_work_item, which requires edit.
    `DepartmentMembershipWorkTypeAccess.can_edit` defaults to False
    (app/models/org.py:329), so view-only is the default shape of a grant and
    a read must not go through the edit predicate.

    This grants access on membership in the REQUESTING department only. Do not
    replace it with the WorkItemPerms.can_view that require_work_item_view
    returns: that flag is also True for an approver on any line of the item,
    so reusing it would let an approver read lines routed to another group.

    Args:
        work_item: The work item whose lines are being read.
        ctx: PortfolioContext carrying the viewer's memberships.

    Returns:
        True if the user may read this work item's lines.
    """
    if user_ctx.is_super_admin:
        return True

    if work_item.created_by_user_id == user_ctx.user_id:
        return True

    work_type_id = ctx.work_type.id if ctx.work_type else None
    if not work_type_id:
        return False

    if ctx.membership and ctx.membership.can_view_work_type(work_type_id):
        return True

    if ctx.division_membership and ctx.division_membership.can_view_work_type(work_type_id):
        return True

    return False


def can_respond_to_work_item(work_item: WorkItem, ctx, user_ctx: UserContext) -> bool:
    """
    Check if user can respond to kicked-back lines on a work item.

    User can respond if they are:
    - A super admin
    - The original requester (created_by_user_id)
    - A department member with edit access for this work type
    - A division member with edit access for this work type

    Args:
        work_item: The work item to check
        ctx: PortfolioContext with membership info
        user_ctx: Current user context

    Returns:
        True if user can respond to kicked-back lines
    """
    if user_ctx.is_super_admin:
        return True

    if work_item.created_by_user_id == user_ctx.user_id:
        return True

    work_type_id = ctx.work_type.id if ctx.work_type else None
    if not work_type_id:
        return False

    if ctx.membership and ctx.membership.can_edit_work_type(work_type_id):
        return True

    if ctx.division_membership and ctx.division_membership.can_edit_work_type(work_type_id):
        return True

    return False


def get_reviewable_groups(
    user_ctx: UserContext, work_type_code: Optional[str] = None,
) -> List[ApprovalGroup]:
    """
    Get approval groups whose review queue the user may open.

    Super admins see every group. Approvers see their own groups, and a
    work-type admin or viewer sees every group of that work type. Opening a
    queue is not a decision right; can_decide_line answers that.

    Args:
        work_type_code: Limit the result to one work type's groups.
    """
    query = ApprovalGroup.query.filter(ApprovalGroup.is_active == True)
    if work_type_code:
        query = query.join(WorkType, ApprovalGroup.work_type_id == WorkType.id).filter(
            WorkType.code == work_type_code.upper()
        )

    if not user_ctx.is_super_admin:
        staff_work_type_ids = [
            row.work_type_id for row in UserRole.query.filter(
                UserRole.user_id == user_ctx.user_id,
                UserRole.role_code.in_([ROLE_WORKTYPE_ADMIN, ROLE_WORKTYPE_VIEWER]),
                UserRole.work_type_id.isnot(None),
            )
        ]
        if not user_ctx.approval_group_ids and not staff_work_type_ids:
            return []
        query = query.filter(db.or_(
            ApprovalGroup.id.in_(user_ctx.approval_group_ids or []),
            ApprovalGroup.work_type_id.in_(staff_work_type_ids),
        ))

    return query.order_by(
        ApprovalGroup.sort_order.asc(),
        ApprovalGroup.name.asc()
    ).all()


def require_reviewer_for_line(line: WorkLine, user_ctx: UserContext) -> None:
    """Abort 403 if user cannot review this line."""
    if not is_reviewer_for_line(line, user_ctx):
        abort(403, "You do not have permission to review this line.")


def require_checkout_for_review(work_item: WorkItem, user_ctx: UserContext) -> None:
    """Abort 403 if user does not have checkout on this work item."""
    if work_item.checked_out_by_user_id != user_ctx.user_id:
        abort(403, "You must checkout this work item before making review decisions.")


def decision_lock_satisfied(work_item: WorkItem, user_ctx: UserContext) -> bool:
    """Return whether the checkout rule allows this user to decide now.

    Group-final work types have no review sessions; everywhere else the
    decider must hold the checkout.
    """
    from app.routes.work.helpers.review_state import is_group_final
    if is_group_final(work_item):
        return True
    return work_item.checked_out_by_user_id == user_ctx.user_id


def can_decide_line(line: WorkLine, user_ctx: UserContext) -> bool:
    """Return whether the user may approve, reject or kick back this line now.

    The one rule every decision button and decision route reads.
    """
    from app.routes.work.helpers.review_state import (
        AWAITING_REVIEWER_GROUP, get_line_review_state,
    )
    return (
        get_line_review_state(line).awaiting == AWAITING_REVIEWER_GROUP
        and is_reviewer_for_line(line, user_ctx)
        and decision_lock_satisfied(line.work_item, user_ctx)
    )


def can_reopen_line(line: WorkLine, user_ctx: UserContext) -> bool:
    """Return whether the user may send this decided line back to PENDING.

    Group-final work types only, and only someone who could have decided it.
    """
    from app.routes.work.helpers.review_state import is_group_final
    if not is_group_final(line.work_item):
        return False
    review = get_review_for_line(line)
    return (
        review is not None
        and review.status in (REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED)
        and is_reviewer_for_line(line, user_ctx)
    )


# ============================================================
# Review Record Management
# ============================================================

def get_or_create_review(line: WorkLine, user_ctx: UserContext) -> Tuple[WorkLineReview, bool]:
    """
    Get or create a WorkLineReview for the APPROVAL_GROUP stage.

    Returns (review, created) tuple.
    """
    routed_group = get_line_routing_approval_group(line)
    if routed_group is None:
        abort(400, "Line has no routed approval group.")

    routed_group_id = routed_group.id

    # Look for existing review at APPROVAL_GROUP stage
    review = WorkLineReview.query.filter_by(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=routed_group_id,
    ).first()

    if review:
        return review, False

    # Create new review
    review = WorkLineReview(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=routed_group_id,
        status=REVIEW_STATUS_PENDING,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(review)
    db.session.flush()

    return review, True


def get_review_for_line(line: WorkLine) -> Optional[WorkLineReview]:
    """Get the APPROVAL_GROUP stage review for a line, if it exists."""
    routed_group = get_line_routing_approval_group(line)
    if routed_group is None:
        return None

    return WorkLineReview.query.filter_by(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=routed_group.id,
    ).first()


# ============================================================
# Transition Validation
# ============================================================

def validate_review_transition(
    current_status: str,
    action: str,
    note: Optional[str],
    user_ctx: UserContext,
    review: WorkLineReview,
    line: WorkLine,
    ctx=None,
) -> Tuple[str, Optional[str]]:
    """
    Validate a review status transition.

    Args:
        current_status: Current review status
        action: Action being taken
        note: Optional note text
        user_ctx: Current user context
        review: The review record
        line: The work line
        ctx: PortfolioContext (optional, used for permission checks)

    Returns (new_status, error_message) tuple.
    error_message is None if transition is valid.
    """
    if action == REVIEW_ACTION_REOPEN:
        from app.routes.work.helpers.review_state import is_group_final
        if not is_group_final(line.work_item):
            # BUDGET reopens through its admin-final tools instead.
            return "", "Reopen is not available for this work type."

    key = (current_status, action)

    if key not in VALID_TRANSITIONS:
        if current_status in (
            REVIEW_STATUS_APPROVED, REVIEW_STATUS_APPROVED_NEEDS_REVIEW, REVIEW_STATUS_REJECTED,
        ):
            return "", "This line was already decided."
        return "", f"Invalid transition: {current_status} -> {action}"

    new_status, note_required, allowed_role = VALID_TRANSITIONS[key]

    # Check note requirement
    if note_required and not (note or "").strip():
        return "", "A note is required for this action."

    # Check role permission
    if allowed_role == "APPROVER":
        if not is_reviewer_for_line(line, user_ctx):
            return "", "You do not have permission to perform this action."
        # Once an admin has made a final decision on this line, the approval
        # group can no longer take review actions (comments remain allowed).
        admin_final = WorkLineReview.query.filter_by(
            work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL,
        ).first()
        if admin_final and admin_final.status in (REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED):
            return "", "This line has a final admin decision and can no longer be reviewed by the approval group."
    elif allowed_role == "REQUESTER":
        # Requester must be owner or have edit rights
        if not line.needs_requester_action:
            return "", "This line is not awaiting your response."

        work_item = line.work_item
        if ctx:
            # Use consolidated permission check when ctx is available
            if not can_respond_to_work_item(work_item, ctx, user_ctx):
                return "", "You do not have permission to respond to this line."
        else:
            # Fallback: query DB directly (for backwards compatibility)
            if not _can_respond_to_work_item_db(work_item, user_ctx):
                return "", "You do not have permission to respond to this line."
    elif allowed_role == "ADMIN":
        if not user_ctx.is_super_admin:
            return "", "Only admins can perform this action."

    return new_status, None


def _can_respond_to_work_item_db(work_item: WorkItem, user_ctx: UserContext) -> bool:
    """
    Check if user can respond to a work item by querying DB directly.

    This is a fallback for when PortfolioContext is not available.
    Prefer can_respond_to_work_item() when ctx is available.
    """
    if user_ctx.is_super_admin:
        return True

    if work_item.created_by_user_id == user_ctx.user_id:
        return True

    if not work_item.portfolio:
        return False

    from app.models import DepartmentMembership, DivisionMembership
    work_type_id = work_item.portfolio.work_type_id

    # Check direct department membership
    membership = DepartmentMembership.query.filter_by(
        department_id=work_item.portfolio.department_id,
        event_cycle_id=work_item.portfolio.event_cycle_id,
        user_id=user_ctx.user_id,
    ).first()
    if membership and membership.can_edit_work_type(work_type_id):
        return True

    # Check division membership
    dept = Department.query.get(work_item.portfolio.department_id)
    if dept and dept.division_id:
        div_membership = DivisionMembership.query.filter_by(
            division_id=dept.division_id,
            event_cycle_id=work_item.portfolio.event_cycle_id,
            user_id=user_ctx.user_id,
        ).first()
        if div_membership and div_membership.can_edit_work_type(work_type_id):
            return True

    return False


# ============================================================
# Status Sync
# ============================================================

def sync_line_status(line: WorkLine, review: WorkLineReview) -> None:
    """
    Update WorkLine status and flags based on its WorkLineReview.
    """
    # Map review status to line status
    status_map = {
        REVIEW_STATUS_PENDING: WORK_LINE_STATUS_PENDING,
        REVIEW_STATUS_NEEDS_INFO: WORK_LINE_STATUS_NEEDS_INFO,
        REVIEW_STATUS_NEEDS_ADJUSTMENT: WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
        REVIEW_STATUS_APPROVED: WORK_LINE_STATUS_APPROVED,
        REVIEW_STATUS_APPROVED_NEEDS_REVIEW: WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW,
        REVIEW_STATUS_REJECTED: WORK_LINE_STATUS_REJECTED,
    }

    new_status = status_map.get(review.status, WORK_LINE_STATUS_PENDING)

    line.status = new_status
    line.status_changed_at = datetime.utcnow()

    # Set needs_requester_action flag
    line.needs_requester_action = review.status in (
        REVIEW_STATUS_NEEDS_INFO,
        REVIEW_STATUS_NEEDS_ADJUSTMENT,
    )


# ============================================================
# Audit Events
# ============================================================

def create_line_audit_event(
    line: WorkLine,
    event_type: str,
    old_value: Optional[str],
    new_value: str,
    note: Optional[str],
    user_ctx: UserContext,
    field_name: str = "status",
) -> WorkLineAuditEvent:
    """Create an audit event for a line."""
    event = WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=event_type,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        note=note,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(event)
    return event


def audit_line_created(
    line: WorkLine,
    detail: BudgetLineDetail,
    user_ctx: UserContext,
) -> WorkLineAuditEvent:
    """Create an audit event for a newly created line."""
    parts = [f"Line #{line.line_number}"]
    if detail.expense_account:
        parts.append(detail.expense_account.name)
    parts.append(f"qty={detail.quantity}")
    parts.append(f"price=${detail.unit_price_cents / 100:,.2f}")
    summary = ", ".join(parts)

    return create_line_audit_event(
        line=line,
        event_type=AUDIT_EVENT_LINE_CREATED,
        old_value=None,
        new_value=summary,
        note=detail.description,
        user_ctx=user_ctx,
        field_name="line",
    )


def audit_line_field_changes(
    line: WorkLine,
    changes: List[Tuple[str, str, str]],
    user_ctx: UserContext,
) -> List[WorkLineAuditEvent]:
    """
    Create FIELD_CHANGE audit events for each changed field.

    Args:
        line: The work line that was changed
        changes: List of (field_name, old_value, new_value) tuples
        user_ctx: Current user context

    Returns:
        List of created audit events
    """
    events = []
    for field_name, old_val, new_val in changes:
        event = create_line_audit_event(
            line=line,
            event_type=AUDIT_EVENT_FIELD_CHANGE,
            old_value=str(old_val) if old_val is not None else None,
            new_value=str(new_val),
            note=None,
            user_ctx=user_ctx,
            field_name=field_name,
        )
        events.append(event)
    return events


def audit_line_deleted(
    work_item: WorkItem,
    line: WorkLine,
    detail: Optional[BudgetLineDetail],
    user_ctx: UserContext,
) -> WorkItemAuditEvent:
    """
    Create a LINE_DELETED audit event at the work item level.

    Uses WorkItemAuditEvent so it survives the cascade delete of the line.
    """
    snapshot = {
        "line_number": line.line_number,
    }
    if detail:
        snapshot["description"] = detail.description
        snapshot["quantity"] = str(detail.quantity)
        snapshot["unit_price_cents"] = detail.unit_price_cents
        if detail.expense_account:
            snapshot["expense_account"] = detail.expense_account.name
        if detail.spend_type:
            snapshot["spend_type"] = detail.spend_type.name
        if detail.confidence_level:
            snapshot["confidence_level"] = detail.confidence_level.name

    event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_LINE_DELETED,
        old_value=f"Line #{line.line_number}",
        new_value=None,
        reason=None,
        snapshot=snapshot,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(event)
    return event


# ============================================================
# Apply Review Decision
# ============================================================

def apply_review_decision(
    review: WorkLineReview,
    line: WorkLine,
    work_item: WorkItem,
    action: str,
    note: Optional[str],
    amount_cents: Optional[int],
    user_ctx: UserContext,
    ctx=None,
) -> Tuple[bool, Optional[str]]:
    """
    Apply a review decision atomically.

    Args:
        review: The review record to update
        line: The work line being reviewed
        work_item: The parent work item
        action: The review action (approve, reject, etc.)
        note: Optional note text
        amount_cents: Approved amount (for approvals)
        user_ctx: Current user context
        ctx: PortfolioContext (optional, improves permission check efficiency)

    Returns (success, error_message) tuple.
    """
    # 1. Verify checkout (except for requester responses)
    if action != REVIEW_ACTION_RESPOND:
        if not decision_lock_satisfied(work_item, user_ctx):
            return False, "You must checkout this work item before making review decisions."

    # 2. Re-read the request, then the review, under row locks. Without review
    # sessions two reviewers can decide at once: the review lock refuses a second
    # decision on one line, and the request lock lets the later of two last-line
    # decisions see the earlier one and finalize. Lock the request first, always.
    # Both refreshes discard unflushed changes; callers must not set attributes
    # on work_item or review before this call. refresh replays the caller's
    # joinedload of portfolio, and PostgreSQL refuses a bare FOR UPDATE across
    # that outer join; OF limits the lock to the request row.
    db.session.refresh(work_item, with_for_update={"of": WorkItem})
    db.session.refresh(review, with_for_update=True)

    # 3. Validate transition
    old_status = review.status
    new_status, error = validate_review_transition(
        old_status, action, note, user_ctx, review, line, ctx=ctx
    )
    if error:
        return False, error

    # 4. Apply changes
    review.status = new_status
    review.decided_at = datetime.utcnow()
    review.decided_by_user_id = user_ctx.user_id
    review.note = (note or "").strip() or None

    if amount_cents is not None and action in (
        REVIEW_ACTION_APPROVE, REVIEW_ACTION_APPROVE_NEEDS_REVIEW
    ):
        review.approved_amount_cents = amount_cents

    # 5. Sync line status
    sync_line_status(line, review)
    line.status_changed_by_user_id = user_ctx.user_id

    # 6. Create audit event
    create_line_audit_event(
        line,
        AUDIT_EVENT_REVIEW_DECISION if action != REVIEW_ACTION_RESPOND else AUDIT_EVENT_REQUESTER_RESPONSE,
        old_status,
        new_status,
        note,
        user_ctx,
    )

    # 6b. A reopened line means the request is no longer fully decided.
    if action == REVIEW_ACTION_REOPEN and work_item.status == WORK_ITEM_STATUS_FINALIZED:
        db.session.add(WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_UNFINALIZE,
            old_value=WORK_ITEM_STATUS_FINALIZED,
            new_value=WORK_ITEM_STATUS_SUBMITTED,
            reason=(note or "").strip(),
            snapshot={"trigger": "line_reopened", "line_number": line.line_number},
            created_by_user_id=user_ctx.user_id,
        ))
        work_item.status = WORK_ITEM_STATUS_SUBMITTED
        work_item.finalized_at = None
        work_item.finalized_by_user_id = None

    # 7. Auto-finalize for non-admin-final worktypes once every approval-group
    # review has a terminal decision. No-op for admin-final worktypes (BUDGET).
    from app.routes.work.helpers.lifecycle import try_auto_finalize
    try_auto_finalize(work_item, user_ctx)

    return True, None


# ============================================================
# Dashboard Queues
# ============================================================

def build_approval_queues(
    group_id: int,
    event_cycle_id: Optional[int] = None,
    department_id: Optional[int] = None,
) -> ApprovalQueues:
    """
    Build the approval queues for a dashboard.

    Returns queues for:
    - pending_requests: Requests with PENDING lines (grouped by request)
    - kicked_back: Lines with NEEDS_INFO or NEEDS_ADJUSTMENT (line-level)
    - recently_decided_requests: Requests with recently decided lines (grouped)
    """
    # Base query for reviews in this group at APPROVAL_GROUP stage.
    # Detail tables are loaded polymorphically via selectinload so non-BUDGET
    # work types (TechOps, Contract, Supply) aren't excluded by an inner join.
    base_query = (
        db.session.query(WorkLineReview)
        .join(WorkLine, WorkLineReview.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .options(
            contains_eager(WorkLineReview.work_line)
                .contains_eager(WorkLine.work_item)
                .contains_eager(WorkItem.portfolio)
                .joinedload(WorkPortfolio.event_cycle),
            contains_eager(WorkLineReview.work_line)
                .contains_eager(WorkLine.work_item)
                .contains_eager(WorkItem.portfolio)
                .joinedload(WorkPortfolio.department),
            contains_eager(WorkLineReview.work_line)
                .selectinload(WorkLine.budget_detail),
            contains_eager(WorkLineReview.work_line)
                .selectinload(WorkLine.contract_detail),
            contains_eager(WorkLineReview.work_line)
                .selectinload(WorkLine.supply_detail),
            contains_eager(WorkLineReview.work_line)
                .selectinload(WorkLine.techops_detail),
        )
        .filter(WorkLineReview.stage == REVIEW_STAGE_APPROVAL_GROUP)
        .filter(WorkLineReview.approval_group_id == group_id)
        .filter(WorkItem.is_archived == False)
    )

    # Apply event cycle filter
    if event_cycle_id:
        base_query = base_query.filter(WorkPortfolio.event_cycle_id == event_cycle_id)

    # Apply department filter
    if department_id:
        base_query = base_query.filter(WorkPortfolio.department_id == department_id)

    # Pending reviews
    pending_reviews = base_query.filter(
        WorkLineReview.status == REVIEW_STATUS_PENDING
    ).order_by(WorkLineReview.created_at.asc()).all()

    # Group pending reviews by work item and collect work item IDs
    pending_by_item: dict[int, list] = {}
    work_item_ids = set()
    for review in pending_reviews:
        wi_id = review.work_line.work_item_id
        work_item_ids.add(wi_id)
        if wi_id not in pending_by_item:
            pending_by_item[wi_id] = []
        pending_by_item[wi_id].append(review)

    # Batch load all work items with their lines and details.
    # Loading all three detail relationships keeps the queue polymorphic.
    work_items_map = {}
    if work_item_ids:
        work_items_with_lines = WorkItem.query.filter(
            WorkItem.id.in_(work_item_ids)
        ).options(
            selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
            selectinload(WorkItem.lines).joinedload(WorkLine.contract_detail),
            selectinload(WorkItem.lines).joinedload(WorkLine.supply_detail),
            selectinload(WorkItem.lines).joinedload(WorkLine.techops_detail),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
        ).all()
        work_items_map = {wi.id: wi for wi in work_items_with_lines}

    # Build request-level pending queue
    pending_requests = []
    for wi_id, reviews in pending_by_item.items():
        work_item = work_items_map.get(wi_id, reviews[0].work_line.work_item)
        portfolio = work_item.portfolio

        # Lines whose routing snapshot points at this approval group, regardless of work type
        lines_in_group = [
            line for line in work_item.lines
            if (g := get_line_routing_approval_group(line)) and g.id == group_id
        ]

        total_lines_in_group = len(lines_in_group)
        total_cents = sum(get_line_amount_cents(line) for line in lines_in_group)

        pending_requests.append(RequestQueueItem(
            work_item=work_item,
            event_cycle=portfolio.event_cycle,
            department=portfolio.department,
            pending_line_count=len(reviews),
            total_line_count=total_lines_in_group,
            total_requested_cents=total_cents,
        ))

    # Sort by oldest pending first (based on work item submitted_at)
    pending_requests.sort(key=lambda x: x.work_item.submitted_at or datetime.min)

    # Kicked back queue (keep line-level for specific action items)
    kicked_back_reviews = base_query.filter(
        WorkLineReview.status.in_([REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_NEEDS_ADJUSTMENT])
    ).order_by(WorkLineReview.decided_at.desc()).all()

    def to_queue_item(review: WorkLineReview) -> ReviewQueueItem:
        """Convert a WorkLineReview to a ReviewQueueItem for display."""
        line = review.work_line
        return ReviewQueueItem(
            work_item=line.work_item,
            work_line=line,
            review=review,
            detail=get_line_detail(line),
            line_total_cents=get_line_amount_cents(line),
        )

    # Recently decided (last 72 hours) - group by request
    cutoff = datetime.utcnow() - timedelta(hours=72)
    recently_decided_reviews = base_query.filter(
        WorkLineReview.status.in_([REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED]),
        WorkLineReview.decided_at >= cutoff,
    ).order_by(WorkLineReview.decided_at.desc()).all()

    # Collect work item IDs from recently decided
    decided_work_item_ids = set()
    decided_by_item: dict[int, list] = {}
    for review in recently_decided_reviews:
        wi_id = review.work_line.work_item_id
        decided_work_item_ids.add(wi_id)
        if wi_id not in decided_by_item:
            decided_by_item[wi_id] = []
        decided_by_item[wi_id].append(review)

    # Batch load decided work items if not already loaded
    for wi_id in decided_work_item_ids:
        if wi_id not in work_items_map:
            work_item_ids.add(wi_id)

    if decided_work_item_ids - set(work_items_map.keys()):
        additional_items = WorkItem.query.filter(
            WorkItem.id.in_(decided_work_item_ids - set(work_items_map.keys()))
        ).options(
            selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
            selectinload(WorkItem.lines).joinedload(WorkLine.contract_detail),
            selectinload(WorkItem.lines).joinedload(WorkLine.supply_detail),
            selectinload(WorkItem.lines).joinedload(WorkLine.techops_detail),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
        ).all()
        for wi in additional_items:
            work_items_map[wi.id] = wi

    recently_decided_requests = []
    for wi_id, reviews in decided_by_item.items():
        work_item = work_items_map.get(wi_id, reviews[0].work_line.work_item)
        portfolio = work_item.portfolio

        lines_in_group = [
            line for line in work_item.lines
            if (g := get_line_routing_approval_group(line)) and g.id == group_id
        ]

        total_cents = sum(get_line_amount_cents(line) for line in lines_in_group)

        recently_decided_requests.append(RequestQueueItem(
            work_item=work_item,
            event_cycle=portfolio.event_cycle,
            department=portfolio.department,
            pending_line_count=0,  # All decided
            total_line_count=len(reviews),  # Lines that were decided
            total_requested_cents=total_cents,
        ))

    return ApprovalQueues(
        pending_requests=pending_requests,
        kicked_back=[to_queue_item(r) for r in kicked_back_reviews],
        recently_decided_requests=recently_decided_requests,
        pending_request_count=len(pending_requests),
        pending_line_count=len(pending_reviews),
        kicked_back_count=len(kicked_back_reviews),
        recently_decided_count=len(recently_decided_requests),
    )


def get_active_event_cycles() -> List[EventCycle]:
    """Get active event cycles for filter dropdown."""
    return EventCycle.query.filter_by(is_active=True).order_by(
        EventCycle.sort_order.asc(),
        EventCycle.name.asc()
    ).all()


def get_active_departments(event_cycle_id: Optional[int] = None) -> List[Department]:
    """
    Get active departments for filter dropdown.

    If event_cycle_id is provided, only returns departments enabled for that event.
    """
    if event_cycle_id:
        from app.routes.work.helpers import get_enabled_departments_for_event
        return get_enabled_departments_for_event(event_cycle_id)

    return Department.query.filter_by(is_active=True).order_by(
        Department.sort_order.asc(),
        Department.name.asc()
    ).all()
