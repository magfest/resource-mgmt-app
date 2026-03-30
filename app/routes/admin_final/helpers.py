"""
Admin Final Review helpers - helper functions for admin final review workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from flask import abort
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkItemAuditEvent,
    WorkLineAuditEvent,
    WorkPortfolio,
    BudgetLineDetail,
    EventCycle,
    Department,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_PAUSED,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_REJECTED,
    AUDIT_EVENT_ADMIN_FINAL,
    AUDIT_EVENT_AMOUNT_OVERRIDE,
    AUDIT_EVENT_FINALIZE,
    AUDIT_EVENT_UNFINALIZE,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_NEEDS_INFO,
    REVIEW_ACTION_RESET,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
)
from app.routes import UserContext
from app.routes.work.helpers.checkout import is_checked_out


@dataclass(frozen=True)
class AdminQueueItem:
    """A line item in the admin review queue (used for kicked-back lines)."""
    work_item: WorkItem
    work_line: WorkLine
    approval_group_review: Optional[WorkLineReview]
    admin_review: Optional[WorkLineReview]
    budget_detail: BudgetLineDetail
    line_total_cents: int
    recommended_amount_cents: Optional[int]


@dataclass(frozen=True)
class AdminRequestQueueItem:
    """A request-level item in the admin review queue."""
    work_item: WorkItem
    event_cycle: "EventCycle"
    department: "Department"
    ready_line_count: int
    total_line_count: int
    total_requested_cents: int
    total_recommended_cents: int


@dataclass(frozen=True)
class AdminQueues:
    """Queues for the admin final review dashboard."""
    ready_for_review: List[AdminRequestQueueItem]  # Request-level view
    kicked_back: List[AdminQueueItem]  # Line-level (needs specific action)
    recently_finalized: List[WorkItem]
    ready_request_count: int
    ready_line_count: int


# ============================================================
# Permission Checks
# ============================================================

def require_admin(user_ctx: UserContext) -> None:
    """Abort 403 if user is not an admin."""
    if not user_ctx.is_super_admin:
        abort(403, "Admin access required.")


def require_budget_admin(user_ctx: UserContext) -> None:
    """
    Require Budget worktype admin OR super admin.
    Aborts with 403 if user lacks permission.
    """
    if user_ctx.is_super_admin:
        return  # Super admin has access

    # Check for WORKTYPE_ADMIN role with Budget work type
    from app.models import UserRole, WorkType, ROLE_WORKTYPE_ADMIN

    budget_wt = WorkType.query.filter_by(code='BUDGET', is_active=True).first()
    if not budget_wt:
        abort(403, "Budget work type not configured")

    has_role = UserRole.query.filter_by(
        user_id=user_ctx.user_id,
        role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=budget_wt.id
    ).first()

    if not has_role:
        abort(403, "You need Budget Admin access for this page")


# ============================================================
# Review Record Management
# ============================================================

def get_approval_group_review(line: WorkLine) -> Optional[WorkLineReview]:
    """Get the APPROVAL_GROUP stage review for a line."""
    if not line.budget_detail:
        return None

    return WorkLineReview.query.filter_by(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
    ).first()


def get_admin_final_review(line: WorkLine) -> Optional[WorkLineReview]:
    """Get the ADMIN_FINAL stage review for a line."""
    return WorkLineReview.query.filter_by(
        work_line_id=line.id,
        stage=REVIEW_STAGE_ADMIN_FINAL,
    ).first()


def batch_load_reviews_by_line(line_ids: List[int]) -> dict:
    """
    Batch-load all reviews for multiple lines in two queries.

    Returns dict with structure:
    {
        line_id: {
            'admin': WorkLineReview or None,
            'ag': WorkLineReview or None
        }
    }
    """
    if not line_ids:
        return {}

    # Initialize result dict
    result = {lid: {'admin': None, 'ag': None} for lid in line_ids}

    # Batch query for all reviews (both stages) in one query
    reviews = WorkLineReview.query.filter(
        WorkLineReview.work_line_id.in_(line_ids)
    ).all()

    # Organize by line_id and stage
    for review in reviews:
        if review.stage == REVIEW_STAGE_ADMIN_FINAL:
            result[review.work_line_id]['admin'] = review
        elif review.stage == REVIEW_STAGE_APPROVAL_GROUP:
            result[review.work_line_id]['ag'] = review

    return result


def get_or_create_admin_review(line: WorkLine, user_ctx: UserContext) -> Tuple[WorkLineReview, bool]:
    """
    Get or create an ADMIN_FINAL WorkLineReview.

    Uses SELECT ... FOR UPDATE to prevent duplicate ADMIN_FINAL reviews
    from concurrent requests (the standard unique constraint doesn't protect
    rows where approval_group_id IS NULL due to SQL NULL semantics).

    Returns (review, created) tuple.
    """
    # Lock existing review row if present, preventing concurrent creation
    review = WorkLineReview.query.filter_by(
        work_line_id=line.id,
        stage=REVIEW_STAGE_ADMIN_FINAL,
    ).with_for_update().first()

    if review:
        return review, False

    review = WorkLineReview(
        work_line_id=line.id,
        stage=REVIEW_STAGE_ADMIN_FINAL,
        approval_group_id=None,  # Admin final is not tied to an approval group
        status=REVIEW_STATUS_PENDING,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(review)
    db.session.flush()

    return review, True


# ============================================================
# Finalization Checks
# ============================================================

def can_finalize_work_item(work_item: WorkItem) -> Tuple[bool, str]:
    """
    Check if a work item can be finalized.

    Returns (can_finalize, reason) tuple.

    Note: Admins can finalize from either AWAITING_DISPATCH or SUBMITTED status
    (AWAITING_DISPATCH allows admin bypass of the dispatch queue).
    """
    if work_item.status == WORK_ITEM_STATUS_FINALIZED:
        return False, "Work item is already finalized."

    # Allow finalization from AWAITING_DISPATCH (admin bypass) or SUBMITTED
    if work_item.status not in (WORK_ITEM_STATUS_AWAITING_DISPATCH, WORK_ITEM_STATUS_SUBMITTED):
        return False, "Work item must be submitted before finalization."

    # Block finalization while a reviewer has an active checkout
    if is_checked_out(work_item):
        return False, "Cannot finalize: a reviewer currently has this item checked out."

    # Batch-load all reviews for all lines (1 query instead of 2N)
    line_ids = [line.id for line in work_item.lines]
    reviews_by_line = batch_load_reviews_by_line(line_ids)

    # Single pass through lines to check all conditions
    has_any_decision = False
    for line in work_item.lines:
        # Check for kicked-back lines
        if line.status in (WORK_LINE_STATUS_NEEDS_INFO, WORK_LINE_STATUS_NEEDS_ADJUSTMENT):
            return False, f"Line {line.line_number} is awaiting requester response."

        # Check if this line has a decision (from either admin or AG review)
        if not has_any_decision:
            line_reviews = reviews_by_line.get(line.id, {})
            admin_review = line_reviews.get('admin')
            ag_review = line_reviews.get('ag')

            if admin_review and admin_review.status in (REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED):
                has_any_decision = True
            elif ag_review and ag_review.status in (REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED):
                has_any_decision = True

    if not has_any_decision:
        return False, "At least one line must be reviewed before finalization."

    return True, "OK"


def get_finalization_summary(work_item: WorkItem) -> dict:
    """
    Get summary of work item state for finalization.

    Returns dict with counts of lines in each state.
    """
    summary = {
        "total_lines": 0,
        "approved_lines": 0,
        "rejected_lines": 0,
        "pending_lines": 0,
        "kicked_back_lines": 0,
        "total_requested_cents": 0,
        "total_approved_cents": 0,
    }

    for line in work_item.lines:
        summary["total_lines"] += 1

        if line.budget_detail:
            line_total = line.budget_detail.unit_price_cents * int(line.budget_detail.quantity)
            summary["total_requested_cents"] += line_total

        if line.status == WORK_LINE_STATUS_APPROVED:
            summary["approved_lines"] += 1
            summary["total_approved_cents"] += line.approved_amount_cents or 0
        elif line.status == WORK_LINE_STATUS_REJECTED:
            summary["rejected_lines"] += 1
        elif line.status in (WORK_LINE_STATUS_NEEDS_INFO, WORK_LINE_STATUS_NEEDS_ADJUSTMENT):
            summary["kicked_back_lines"] += 1
        else:
            summary["pending_lines"] += 1

    return summary


# ============================================================
# Admin Final Review Application
# ============================================================

def apply_admin_final_decision(
    line: WorkLine,
    work_item: WorkItem,
    action: str,
    approved_amount_cents: Optional[int],
    note: Optional[str],
    user_ctx: UserContext,
) -> Tuple[bool, Optional[str]]:
    """
    Apply an admin final review decision.

    This sets the authoritative approved_amount_cents on WorkLine.

    Returns (success, error_message) tuple.
    """
    require_budget_admin(user_ctx)

    # Get or create admin review record
    review, _created = get_or_create_admin_review(line, user_ctx)

    # Get approval group recommendation (if exists)
    ag_review = get_approval_group_review(line)
    if ag_review:
        recommended = ag_review.approved_amount_cents
    else:
        recommended = None

    # Validate action
    if action == REVIEW_ACTION_APPROVE:
        if approved_amount_cents is None:
            # Use recommended amount or line total
            if recommended is not None:
                approved_amount_cents = recommended
            elif line.budget_detail:
                approved_amount_cents = line.budget_detail.unit_price_cents * int(line.budget_detail.quantity)
            else:
                return False, "No amount specified and no default available."

        # Check if amount differs from recommended (requires note)
        if recommended is not None and approved_amount_cents != recommended:
            if not (note or "").strip():
                return False, "Note required when modifying recommended amount."

            # Create amount override audit event
            _create_line_audit(
                line,
                AUDIT_EVENT_AMOUNT_OVERRIDE,
                str(recommended),
                str(approved_amount_cents),
                note,
                user_ctx,
            )

        review.status = REVIEW_STATUS_APPROVED
        review.approved_amount_cents = approved_amount_cents
        line.status = WORK_LINE_STATUS_APPROVED
        line.approved_amount_cents = approved_amount_cents  # AUTHORITATIVE

    elif action == REVIEW_ACTION_REJECT:
        if not (note or "").strip():
            return False, "Note required for rejection."

        review.status = REVIEW_STATUS_REJECTED
        line.status = WORK_LINE_STATUS_REJECTED
        line.approved_amount_cents = None

    elif action == REVIEW_ACTION_NEEDS_INFO:
        if not (note or "").strip():
            return False, "Note required when requesting information."

        review.status = REVIEW_STATUS_NEEDS_INFO
        line.status = WORK_LINE_STATUS_NEEDS_INFO
        line.needs_requester_action = True

    elif action == REVIEW_ACTION_RESET:
        # Reset to pending for re-review
        review.status = REVIEW_STATUS_PENDING
        line.status = WORK_LINE_STATUS_PENDING
        line.needs_requester_action = False

    else:
        return False, f"Invalid action: {action}"

    # Update review metadata
    review.decided_at = datetime.utcnow()
    review.decided_by_user_id = user_ctx.user_id
    review.note = (note or "").strip() or None

    # Update line metadata
    line.status_changed_at = datetime.utcnow()
    line.status_changed_by_user_id = user_ctx.user_id
    line.current_review_stage = REVIEW_STAGE_ADMIN_FINAL

    # Create audit event
    _create_line_audit(
        line,
        AUDIT_EVENT_ADMIN_FINAL,
        None,
        review.status,
        note,
        user_ctx,
    )

    return True, None


def _create_line_audit(
    line: WorkLine,
    event_type: str,
    old_value: Optional[str],
    new_value: str,
    note: Optional[str],
    user_ctx: UserContext,
) -> None:
    """Create a line audit event."""
    event = WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=event_type,
        field_name="status",
        old_value=old_value,
        new_value=new_value,
        note=note,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(event)


# ============================================================
# Finalization / Unfinalization
# ============================================================

def finalize_work_item(
    work_item: WorkItem,
    user_ctx: UserContext,
    note: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Finalize a work item.

    Sets approved amounts on all pending lines based on their current values.

    Returns (success, error_message) tuple.
    """
    require_budget_admin(user_ctx)

    # Require a note
    if not (note or "").strip():
        return False, "A note is required for finalization."

    # Lock the work item row to prevent concurrent finalization
    db.session.query(WorkItem).with_for_update().get(work_item.id)

    can_do, reason = can_finalize_work_item(work_item)
    if not can_do:
        return False, reason

    # For any lines still PENDING, approve them at their requested amount
    for line in work_item.lines:
        if line.status == WORK_LINE_STATUS_PENDING:
            # Get or create admin review
            review, _created = get_or_create_admin_review(line, user_ctx)

            # Approve at requested amount
            if line.budget_detail:
                amount = line.budget_detail.unit_price_cents * int(line.budget_detail.quantity)
            else:
                amount = 0

            review.status = REVIEW_STATUS_APPROVED
            review.approved_amount_cents = amount
            review.decided_at = datetime.utcnow()
            review.decided_by_user_id = user_ctx.user_id
            review.note = "Auto-approved during finalization"

            line.status = WORK_LINE_STATUS_APPROVED
            line.approved_amount_cents = amount
            line.status_changed_at = datetime.utcnow()
            line.status_changed_by_user_id = user_ctx.user_id
            line.current_review_stage = REVIEW_STAGE_ADMIN_FINAL

    # Capture old status for audit before changing
    old_status = work_item.status

    # Set work item to finalized
    work_item.status = WORK_ITEM_STATUS_FINALIZED
    work_item.finalized_at = datetime.utcnow()
    work_item.finalized_by_user_id = user_ctx.user_id

    # Create audit event
    audit = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_FINALIZE,
        old_value=old_status,
        new_value=WORK_ITEM_STATUS_FINALIZED,
        reason=(note or "").strip(),
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(audit)

    # If this is a PRIMARY request, un-pause any PAUSED supplementary requests
    if work_item.request_kind == REQUEST_KIND_PRIMARY:
        paused_supplementary = WorkItem.query.filter_by(
            portfolio_id=work_item.portfolio_id,
            request_kind=REQUEST_KIND_SUPPLEMENTARY,
            status=WORK_ITEM_STATUS_PAUSED,
            is_archived=False,
        ).all()

        # Batch-load all reviews for all lines across all paused supplementaries (1 query)
        all_supp_line_ids = []
        for supp in paused_supplementary:
            all_supp_line_ids.extend(line.id for line in supp.lines)
        reviews_by_line = batch_load_reviews_by_line(all_supp_line_ids)

        for supp in paused_supplementary:
            # Validate supplementary is in a consistent state before un-pausing
            # Check that all lines have budget details and valid approval group routing
            can_unpause = True
            for line in supp.lines:
                if not line.budget_detail:
                    can_unpause = False
                    break
                if not line.budget_detail.routed_approval_group_id:
                    can_unpause = False
                    break
                # Check that line has a valid review record (from batch-loaded data)
                line_reviews = reviews_by_line.get(line.id, {})
                if not line_reviews.get('ag'):
                    can_unpause = False
                    break

            if can_unpause:
                supp.status = WORK_ITEM_STATUS_SUBMITTED
                # Create audit event for un-pause
                supp_audit = WorkItemAuditEvent(
                    work_item_id=supp.id,
                    event_type="UNPAUSE",
                    old_value=WORK_ITEM_STATUS_PAUSED,
                    new_value=WORK_ITEM_STATUS_SUBMITTED,
                    reason="Primary request re-finalized",
                    created_by_user_id=user_ctx.user_id,
                )
                db.session.add(supp_audit)
            else:
                # Log that we couldn't un-pause due to invalid state
                supp_audit = WorkItemAuditEvent(
                    work_item_id=supp.id,
                    event_type="UNPAUSE_FAILED",
                    old_value=WORK_ITEM_STATUS_PAUSED,
                    new_value=WORK_ITEM_STATUS_PAUSED,
                    reason="Primary re-finalized but supplementary has invalid line state - manual review required",
                    created_by_user_id=user_ctx.user_id,
                )
                db.session.add(supp_audit)

    return True, None


def unfinalize_work_item(
    work_item: WorkItem,
    reason: str,
    reset_lines: bool,
    user_ctx: UserContext,
) -> Tuple[bool, Optional[str]]:
    """
    Unfinalize a work item for re-review.

    Args:
        work_item: The work item to unfinalize
        reason: Required reason for unfinalizing
        reset_lines: If True, reset all line reviews to PENDING
        user_ctx: Current user context

    Returns (success, error_message) tuple.
    """
    require_budget_admin(user_ctx)

    if not (reason or "").strip():
        return False, "Reason required for unfinalize."

    # Lock the work item row to prevent concurrent state changes
    db.session.query(WorkItem).with_for_update().get(work_item.id)

    if work_item.status != WORK_ITEM_STATUS_FINALIZED:
        return False, "Work item is not finalized."

    # Create audit event BEFORE changing state
    audit = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_UNFINALIZE,
        old_value=WORK_ITEM_STATUS_FINALIZED,
        new_value=WORK_ITEM_STATUS_SUBMITTED,
        reason=reason.strip(),
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(audit)

    # Reset work item status
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    work_item.finalized_at = None
    work_item.finalized_by_user_id = None

    # Optionally reset line reviews
    if reset_lines:
        # Batch load admin reviews to avoid N+1 queries
        line_ids = [line.id for line in work_item.lines]
        reviews_by_line = batch_load_reviews_by_line(line_ids)

        for line in work_item.lines:
            admin_review = reviews_by_line.get(line.id, {}).get('admin')
            if admin_review:
                admin_review.status = REVIEW_STATUS_PENDING
                admin_review.decided_at = None
                admin_review.decided_by_user_id = None

            line.status = WORK_LINE_STATUS_PENDING
            line.approved_amount_cents = None
            line.needs_requester_action = False

    # If this is a PRIMARY request, pause any submitted supplementary requests
    if work_item.request_kind == REQUEST_KIND_PRIMARY:
        active_supplementary = WorkItem.query.filter_by(
            portfolio_id=work_item.portfolio_id,
            request_kind=REQUEST_KIND_SUPPLEMENTARY,
            status=WORK_ITEM_STATUS_SUBMITTED,
            is_archived=False,
        ).all()

        for supp in active_supplementary:
            supp.status = WORK_ITEM_STATUS_PAUSED
            # Create audit event for pause
            supp_audit = WorkItemAuditEvent(
                work_item_id=supp.id,
                event_type="PAUSE",
                old_value=WORK_ITEM_STATUS_SUBMITTED,
                new_value=WORK_ITEM_STATUS_PAUSED,
                reason=f"Primary request unfinalized: {reason.strip()}",
                created_by_user_id=user_ctx.user_id,
            )
            db.session.add(supp_audit)

    return True, None


def reset_line_for_rereview(
    line: WorkLine,
    user_ctx: UserContext,
) -> Tuple[bool, Optional[str]]:
    """
    Reset a specific line for re-review (after unfinalize).

    Returns (success, error_message) tuple.
    """
    require_budget_admin(user_ctx)

    admin_review = get_admin_final_review(line)
    if admin_review:
        admin_review.status = REVIEW_STATUS_PENDING
        admin_review.decided_at = None
        admin_review.decided_by_user_id = None

    line.status = WORK_LINE_STATUS_PENDING
    line.approved_amount_cents = None
    line.needs_requester_action = False
    line.status_changed_at = datetime.utcnow()
    line.status_changed_by_user_id = user_ctx.user_id

    _create_line_audit(
        line,
        AUDIT_EVENT_ADMIN_FINAL,
        "reset",
        REVIEW_STATUS_PENDING,
        "Reset for re-review",
        user_ctx,
    )

    return True, None


# ============================================================
# Dashboard Queues
# ============================================================

def build_admin_queues(
    event_cycle_id: Optional[int] = None,
    department_id: Optional[int] = None,
) -> AdminQueues:
    """
    Build the admin final review queues.

    Returns queues for:
    - ready_for_review: Requests with lines ready for admin review (request-level)
    - kicked_back: Lines with ADMIN_FINAL NEEDS_INFO (line-level)
    - pending_finalization: Work items ready to finalize
    - recently_finalized: Work items finalized in last 7 days
    """
    # Base query for submitted work items with eager loading
    base_item_query = WorkItem.query.filter(
        WorkItem.status.in_([WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED]),
        WorkItem.is_archived == False,
    ).options(
        # Eager load portfolio and its relations
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
        # Eager load lines with budget details
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    )

    if event_cycle_id or department_id:
        base_item_query = base_item_query.join(
            WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id
        )
        if event_cycle_id:
            base_item_query = base_item_query.filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        if department_id:
            base_item_query = base_item_query.filter(WorkPortfolio.department_id == department_id)

    # Get all submitted work items for processing
    submitted_items = base_item_query.filter(
        WorkItem.status == WORK_ITEM_STATUS_SUBMITTED
    ).all()

    # Batch load all reviews for all lines across all submitted items (single query)
    all_line_ids = []
    for item in submitted_items:
        all_line_ids.extend(line.id for line in item.lines)
    reviews_by_line = batch_load_reviews_by_line(all_line_ids)

    # Track request-level data for ready_for_review
    ready_requests: dict[int, dict] = {}
    kicked_back = []
    total_ready_lines = 0

    for item in submitted_items:
        item_has_kicked_back = False
        item_ready_lines = 0
        item_total_requested = 0
        item_total_recommended = 0

        for line in item.lines:
            # Use batch-loaded reviews instead of per-line queries
            line_reviews = reviews_by_line.get(line.id, {'admin': None, 'ag': None})
            ag_review = line_reviews['ag']
            admin_review = line_reviews['admin']
            detail = line.budget_detail

            # Calculate line total
            if detail:
                line_total = detail.unit_price_cents * int(detail.quantity)
            else:
                line_total = 0

            # Get recommended amount from approval group review
            if ag_review:
                recommended = ag_review.approved_amount_cents
            else:
                recommended = None

            # Check line state
            if admin_review:
                if admin_review.status in (REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_NEEDS_ADJUSTMENT):
                    queue_item = AdminQueueItem(
                        work_item=item,
                        work_line=line,
                        approval_group_review=ag_review,
                        admin_review=admin_review,
                        budget_detail=detail,
                        line_total_cents=line_total,
                        recommended_amount_cents=recommended,
                    )
                    kicked_back.append(queue_item)
                    item_has_kicked_back = True
                elif admin_review.status == REVIEW_STATUS_PENDING:
                    # Ready for admin final review
                    item_ready_lines += 1
                    item_total_requested += line_total
                    item_total_recommended += recommended or line_total
                # APPROVED or REJECTED means decided
            elif ag_review and ag_review.status == REVIEW_STATUS_APPROVED:
                # Ready for admin final review
                item_ready_lines += 1
                item_total_requested += line_total
                item_total_recommended += recommended or line_total
            elif line.status in (WORK_LINE_STATUS_NEEDS_INFO, WORK_LINE_STATUS_NEEDS_ADJUSTMENT):
                # Line kicked back at approval group level
                item_has_kicked_back = True

        # Add to ready_requests if there are lines ready
        if item_ready_lines > 0:
            portfolio = item.portfolio
            ready_requests[item.id] = {
                "work_item": item,
                "event_cycle": portfolio.event_cycle,
                "department": portfolio.department,
                "ready_line_count": item_ready_lines,
                "total_line_count": len(item.lines),
                "total_requested_cents": item_total_requested,
                "total_recommended_cents": item_total_recommended,
            }
            total_ready_lines += item_ready_lines

    # Convert ready_requests dict to list of AdminRequestQueueItem
    ready_for_review = [
        AdminRequestQueueItem(
            work_item=data["work_item"],
            event_cycle=data["event_cycle"],
            department=data["department"],
            ready_line_count=data["ready_line_count"],
            total_line_count=data["total_line_count"],
            total_requested_cents=data["total_requested_cents"],
            total_recommended_cents=data["total_recommended_cents"],
        )
        for data in ready_requests.values()
    ]

    # Sort by oldest submitted first
    ready_for_review.sort(key=lambda x: x.work_item.submitted_at or datetime.min)

    # Recently finalized (last 7 days) - add eager loading
    cutoff = datetime.utcnow() - timedelta(days=7)
    recently_finalized_query = WorkItem.query.filter(
        WorkItem.status == WORK_ITEM_STATUS_FINALIZED,
        WorkItem.finalized_at >= cutoff,
        WorkItem.is_archived == False,
    ).options(
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
    ).order_by(WorkItem.finalized_at.desc())

    if event_cycle_id or department_id:
        recently_finalized_query = recently_finalized_query.join(
            WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id
        )
        if event_cycle_id:
            recently_finalized_query = recently_finalized_query.filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        if department_id:
            recently_finalized_query = recently_finalized_query.filter(WorkPortfolio.department_id == department_id)

    recently_finalized = recently_finalized_query.all()

    return AdminQueues(
        ready_for_review=ready_for_review,
        kicked_back=kicked_back,
        recently_finalized=recently_finalized,
        ready_request_count=len(ready_for_review),
        ready_line_count=total_ready_lines,
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
