"""
Computation helpers for totals and status summaries.

Functions for computing portfolio/work item totals and line status summaries.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import (
    WorkPortfolio,
    WorkItem,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_UNDER_REVIEW,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_REJECTED,
    REVIEW_STAGE_ADMIN_FINAL,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
)
from app.line_details import get_line_amount_cents


# ============================================================
# Totals Computation
# ============================================================

def compute_portfolio_totals(portfolio: WorkPortfolio) -> dict:
    """
    Compute totals for a portfolio.

    Works with any line detail type (budget, contract, supply).

    Returns dict with:
        - requested: Total requested amount in cents
        - approved: Total approved amount in cents
        - pending: requested - approved
    """
    requested = 0
    approved = 0

    for item in portfolio.work_items:
        if item.is_archived:
            continue

        for line in item.lines:
            line_total = get_line_amount_cents(line)
            requested += line_total

            if line.status == WORK_LINE_STATUS_APPROVED:
                approved += line.approved_amount_cents or 0

    return {
        "requested": requested,
        "approved": approved,
        "pending": requested - approved,
    }


def compute_work_item_totals(item: WorkItem) -> dict:
    """
    Compute totals for a single work item.

    Works with any line detail type (budget, contract, supply).

    Returns dict with:
        - requested: Total requested amount in cents
        - approved: Total approved amount in cents
        - line_count: Number of lines
    """
    requested = 0
    approved = 0
    line_count = 0

    for line in item.lines:
        line_count += 1
        line_total = get_line_amount_cents(line)
        requested += line_total

        if line.status == WORK_LINE_STATUS_APPROVED:
            approved += line.approved_amount_cents or 0

    return {
        "requested": requested,
        "approved": approved,
        "line_count": line_count,
    }


# ============================================================
# Line Status Summary
# ============================================================

@dataclass
class LineStatusSummary:
    """Summary of line statuses for a work item."""
    line_count: int
    pending_count: int
    needs_info_count: int
    needs_adjustment_count: int
    approved_count: int
    rejected_count: int
    ag_approved_count: int  # Lines approved at Approval Group stage only
    final_approved_count: int  # Lines approved at Admin Final stage
    effective_status: str  # The effective status label considering line issues
    has_issues: bool  # True if any lines need attention
    # Portfolio-level fields (for home page display)
    supplementary_count: int = 0  # Number of supplementary work items
    supplementary_draft_count: int = 0  # Supplementary items in draft
    supplementary_needs_attention: int = 0  # Supplementary items needing action


def compute_line_status_summary(item: WorkItem) -> LineStatusSummary:
    """
    Compute a summary of line statuses for a work item.

    Returns a LineStatusSummary with counts for each status type
    and an effective_status that reflects if any lines are blocked.
    """
    pending = 0
    needs_info = 0
    needs_adjustment = 0
    approved = 0
    rejected = 0
    ag_approved = 0
    final_approved = 0

    for line in item.lines:
        if line.status == WORK_LINE_STATUS_PENDING:
            pending += 1
        elif line.status == WORK_LINE_STATUS_NEEDS_INFO:
            needs_info += 1
        elif line.status == WORK_LINE_STATUS_NEEDS_ADJUSTMENT:
            needs_adjustment += 1
        elif line.status == WORK_LINE_STATUS_APPROVED:
            approved += 1
            # Track approval stage
            if line.current_review_stage == REVIEW_STAGE_ADMIN_FINAL:
                final_approved += 1
            else:
                ag_approved += 1
        elif line.status == WORK_LINE_STATUS_REJECTED:
            rejected += 1

    line_count = len(item.lines)
    has_issues = needs_info > 0 or needs_adjustment > 0

    # Determine effective status
    # Priority: NEEDS_INFO/NEEDS_ADJUSTMENT > base item status
    if item.status == WORK_ITEM_STATUS_DRAFT:
        effective_status = "DRAFT"
    elif item.status == WORK_ITEM_STATUS_AWAITING_DISPATCH:
        effective_status = "AWAITING_DISPATCH"
    elif item.status == WORK_ITEM_STATUS_FINALIZED:
        effective_status = "FINALIZED"
    elif needs_info > 0 and needs_adjustment > 0:
        effective_status = "NEEDS_RESPONSE"
    elif needs_info > 0:
        effective_status = "NEEDS_INFO"
    elif needs_adjustment > 0:
        effective_status = "NEEDS_ADJUSTMENT"
    elif item.status == WORK_ITEM_STATUS_SUBMITTED:
        if pending > 0:
            effective_status = "UNDER_REVIEW"
        else:
            effective_status = "SUBMITTED"
    else:
        effective_status = item.status

    return LineStatusSummary(
        line_count=line_count,
        pending_count=pending,
        needs_info_count=needs_info,
        needs_adjustment_count=needs_adjustment,
        approved_count=approved,
        rejected_count=rejected,
        ag_approved_count=ag_approved,
        final_approved_count=final_approved,
        effective_status=effective_status,
        has_issues=has_issues,
    )


def compute_portfolio_status_summary(portfolio: WorkPortfolio) -> LineStatusSummary | None:
    """
    Compute a status summary for an entire portfolio, including supplementary items.

    Returns None if no primary work item exists.
    The returned summary is based on the primary item but includes
    supplementary_count and supplementary_needs_attention fields.
    """
    # Find the primary work item
    primary = WorkItem.query.filter_by(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    if not primary:
        return None

    # Compute primary status
    summary = compute_line_status_summary(primary)

    # Count supplementary items and their states
    supplementary_items = WorkItem.query.filter_by(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_SUPPLEMENTARY,
        is_archived=False,
    ).all()

    supp_count = len(supplementary_items)
    supp_draft = 0
    supp_needs_attention = 0

    for supp in supplementary_items:
        if supp.status == WORK_ITEM_STATUS_DRAFT:
            supp_draft += 1
        elif supp.status == WORK_ITEM_STATUS_AWAITING_DISPATCH:
            # Awaiting dispatch counts as needing attention (admin action required)
            supp_needs_attention += 1
        elif supp.status in (WORK_ITEM_STATUS_NEEDS_INFO, WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_UNDER_REVIEW):
            # Check if any lines need attention
            supp_summary = compute_line_status_summary(supp)
            if supp_summary.has_issues:
                supp_needs_attention += 1

    # Return updated summary with supplementary info
    return LineStatusSummary(
        line_count=summary.line_count,
        pending_count=summary.pending_count,
        needs_info_count=summary.needs_info_count,
        needs_adjustment_count=summary.needs_adjustment_count,
        approved_count=summary.approved_count,
        rejected_count=summary.rejected_count,
        ag_approved_count=summary.ag_approved_count,
        final_approved_count=summary.final_approved_count,
        effective_status=summary.effective_status,
        has_issues=summary.has_issues,
        supplementary_count=supp_count,
        supplementary_draft_count=supp_draft,
        supplementary_needs_attention=supp_needs_attention,
    )


def compute_portfolio_status_from_loaded(portfolio: WorkPortfolio) -> LineStatusSummary | None:
    """
    Compute portfolio status summary using already-loaded work items and lines.

    Same logic as compute_portfolio_status_summary() but operates on
    pre-loaded relationships (via selectinload) instead of issuing queries.
    Use this when portfolios have been batch-loaded with eager loading.
    """
    primary = None
    supplementary_items = []
    for item in portfolio.work_items:
        if item.is_archived:
            continue
        if item.request_kind == REQUEST_KIND_PRIMARY:
            primary = item
        elif item.request_kind == REQUEST_KIND_SUPPLEMENTARY:
            supplementary_items.append(item)

    if not primary:
        return None

    summary = compute_line_status_summary(primary)

    supp_count = len(supplementary_items)
    supp_draft = 0
    supp_needs_attention = 0

    for supp in supplementary_items:
        if supp.status == WORK_ITEM_STATUS_DRAFT:
            supp_draft += 1
        elif supp.status == WORK_ITEM_STATUS_AWAITING_DISPATCH:
            supp_needs_attention += 1
        elif supp.status in (WORK_ITEM_STATUS_NEEDS_INFO, WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_UNDER_REVIEW):
            supp_summary = compute_line_status_summary(supp)
            if supp_summary.has_issues:
                supp_needs_attention += 1

    return LineStatusSummary(
        line_count=summary.line_count,
        pending_count=summary.pending_count,
        needs_info_count=summary.needs_info_count,
        needs_adjustment_count=summary.needs_adjustment_count,
        approved_count=summary.approved_count,
        rejected_count=summary.rejected_count,
        ag_approved_count=summary.ag_approved_count,
        final_approved_count=summary.final_approved_count,
        effective_status=summary.effective_status,
        has_issues=summary.has_issues,
        supplementary_count=supp_count,
        supplementary_draft_count=supp_draft,
        supplementary_needs_attention=supp_needs_attention,
    )
