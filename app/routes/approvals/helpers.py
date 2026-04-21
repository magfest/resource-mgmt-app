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
    User,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_REJECTED,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_REJECTED,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_NEEDS_INFO,
    REVIEW_ACTION_NEEDS_ADJUSTMENT,
    REVIEW_ACTION_RESET,
    REVIEW_ACTION_RESPOND,
    AUDIT_EVENT_REVIEW_DECISION,
    AUDIT_EVENT_REQUESTER_RESPONSE,
    AUDIT_EVENT_LINE_CREATED,
    AUDIT_EVENT_FIELD_CHANGE,
    AUDIT_EVENT_LINE_DELETED,
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
    (REVIEW_STATUS_PENDING, REVIEW_ACTION_REJECT): (REVIEW_STATUS_REJECTED, True, "APPROVER"),
    (REVIEW_STATUS_PENDING, REVIEW_ACTION_NEEDS_INFO): (REVIEW_STATUS_NEEDS_INFO, True, "APPROVER"),
    (REVIEW_STATUS_PENDING, REVIEW_ACTION_NEEDS_ADJUSTMENT): (REVIEW_STATUS_NEEDS_ADJUSTMENT, True, "APPROVER"),

    # From NEEDS_INFO
    (REVIEW_STATUS_NEEDS_INFO, REVIEW_ACTION_RESPOND): (REVIEW_STATUS_PENDING, True, "REQUESTER"),

    # From NEEDS_ADJUSTMENT
    (REVIEW_STATUS_NEEDS_ADJUSTMENT, REVIEW_ACTION_RESPOND): (REVIEW_STATUS_PENDING, True, "REQUESTER"),

    # Admin reset from terminal states
    (REVIEW_STATUS_APPROVED, REVIEW_ACTION_RESET): (REVIEW_STATUS_PENDING, False, "ADMIN"),
    (REVIEW_STATUS_REJECTED, REVIEW_ACTION_RESET): (REVIEW_STATUS_PENDING, False, "ADMIN"),
}


@dataclass(frozen=True)
class ReviewQueueItem:
    """A line item in the review queue (used for kicked-back lines)."""
    work_item: WorkItem
    work_line: WorkLine
    review: WorkLineReview
    budget_detail: BudgetLineDetail
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
    """
    Check if user can review this line.

    User can review if:
    - They are an admin, OR
    - They have APPROVER role for the approval group this line is routed to
    """
    if user_ctx.is_super_admin:
        return True

    if not line.budget_detail:
        return False

    routed_group_id = line.budget_detail.routed_approval_group_id
    if not routed_group_id:
        return False

    return routed_group_id in user_ctx.approval_group_ids


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


def get_reviewable_groups(user_ctx: UserContext) -> List[ApprovalGroup]:
    """
    Get approval groups the user can review.

    Admins can review all groups. Approvers can review their assigned groups.
    """
    if user_ctx.is_super_admin:
        return ApprovalGroup.query.filter_by(is_active=True).order_by(
            ApprovalGroup.sort_order.asc(),
            ApprovalGroup.name.asc()
        ).all()

    if not user_ctx.approval_group_ids:
        return []

    return ApprovalGroup.query.filter(
        ApprovalGroup.id.in_(user_ctx.approval_group_ids),
        ApprovalGroup.is_active == True
    ).order_by(
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


# ============================================================
# Review Record Management
# ============================================================

def get_or_create_review(line: WorkLine, user_ctx: UserContext) -> Tuple[WorkLineReview, bool]:
    """
    Get or create a WorkLineReview for the APPROVAL_GROUP stage.

    Returns (review, created) tuple.
    """
    if not line.budget_detail:
        abort(400, "Line has no budget detail.")

    routed_group_id = line.budget_detail.routed_approval_group_id

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
    if not line.budget_detail:
        return None

    return WorkLineReview.query.filter_by(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=line.budget_detail.routed_approval_group_id,
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
    key = (current_status, action)

    if key not in VALID_TRANSITIONS:
        return "", f"Invalid transition: {current_status} -> {action}"

    new_status, note_required, allowed_role = VALID_TRANSITIONS[key]

    # Check note requirement
    if note_required and not (note or "").strip():
        return "", "A note is required for this action."

    # Check role permission
    if allowed_role == "APPROVER":
        if not is_reviewer_for_line(line, user_ctx):
            return "", "You do not have permission to perform this action."
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
        if work_item.checked_out_by_user_id != user_ctx.user_id:
            return False, "You must checkout this work item before making review decisions."

    # 2. Validate transition
    old_status = review.status
    new_status, error = validate_review_transition(
        old_status, action, note, user_ctx, review, line, ctx=ctx
    )
    if error:
        return False, error

    # 3. Apply changes
    review.status = new_status
    review.decided_at = datetime.utcnow()
    review.decided_by_user_id = user_ctx.user_id
    review.note = (note or "").strip() or None

    if amount_cents is not None and action == REVIEW_ACTION_APPROVE:
        review.approved_amount_cents = amount_cents

    # 4. Sync line status
    sync_line_status(line, review)
    line.status_changed_by_user_id = user_ctx.user_id

    # 5. Create audit event
    create_line_audit_event(
        line,
        AUDIT_EVENT_REVIEW_DECISION if action != REVIEW_ACTION_RESPOND else AUDIT_EVENT_REQUESTER_RESPONSE,
        old_status,
        new_status,
        note,
        user_ctx,
    )

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
    # Base query for reviews in this group at APPROVAL_GROUP stage
    # Use contains_eager for joined tables, joinedload for additional relations
    base_query = (
        db.session.query(WorkLineReview)
        .join(WorkLine, WorkLineReview.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(BudgetLineDetail, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .options(
            # Use contains_eager for tables already joined, then extend with joinedload
            contains_eager(WorkLineReview.work_line)
                .contains_eager(WorkLine.work_item)
                .contains_eager(WorkItem.portfolio)
                .joinedload(WorkPortfolio.event_cycle),
            contains_eager(WorkLineReview.work_line)
                .contains_eager(WorkLine.work_item)
                .contains_eager(WorkItem.portfolio)
                .joinedload(WorkPortfolio.department),
            contains_eager(WorkLineReview.work_line)
                .contains_eager(WorkLine.budget_detail),
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

    # Batch load all work items with their lines and budget details
    work_items_map = {}
    if work_item_ids:
        work_items_with_lines = WorkItem.query.filter(
            WorkItem.id.in_(work_item_ids)
        ).options(
            selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
        ).all()
        work_items_map = {wi.id: wi for wi in work_items_with_lines}

    # Build request-level pending queue
    pending_requests = []
    for wi_id, reviews in pending_by_item.items():
        work_item = work_items_map.get(wi_id, reviews[0].work_line.work_item)
        portfolio = work_item.portfolio

        # Count total lines in this group for this request
        total_lines_in_group = sum(
            1 for line in work_item.lines
            if line.budget_detail and line.budget_detail.routed_approval_group_id == group_id
        )

        # Calculate total amount for lines in this group
        total_cents = sum(
            line.budget_detail.unit_price_cents * int(line.budget_detail.quantity)
            for line in work_item.lines
            if line.budget_detail and line.budget_detail.routed_approval_group_id == group_id
        )

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
        detail = line.budget_detail
        line_total = detail.unit_price_cents * int(detail.quantity) if detail else 0
        return ReviewQueueItem(
            work_item=line.work_item,
            work_line=line,
            review=review,
            budget_detail=detail,
            line_total_cents=line_total,
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
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
        ).all()
        for wi in additional_items:
            work_items_map[wi.id] = wi

    recently_decided_requests = []
    for wi_id, reviews in decided_by_item.items():
        work_item = work_items_map.get(wi_id, reviews[0].work_line.work_item)
        portfolio = work_item.portfolio

        total_lines_in_group = sum(
            1 for line in work_item.lines
            if line.budget_detail and line.budget_detail.routed_approval_group_id == group_id
        )

        total_cents = sum(
            line.budget_detail.unit_price_cents * int(line.budget_detail.quantity)
            for line in work_item.lines
            if line.budget_detail and line.budget_detail.routed_approval_group_id == group_id
        )

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
