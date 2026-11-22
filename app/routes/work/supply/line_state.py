"""Shared Supply line and order state, read by the review, admin and report cabs."""


def is_line_removed(detail) -> bool:
    """A removed line keeps its row; `removed_at` is the only marker."""
    return detail is not None and detail.removed_at is not None


def is_order_closed(work_item) -> bool:
    detail = work_item.supply_order_detail
    return detail is not None and detail.closed_at is not None


def supply_display_status(work_item) -> str:
    """CLOSED overrides every workflow status; otherwise the shared summary decides."""
    if is_order_closed(work_item):
        return "CLOSED"
    # Imported here: computations imports models that import this package.
    from app.routes.work.helpers.computations import compute_line_status_summary
    return compute_line_status_summary(work_item).effective_status


def reviewer_quantity_label(detail, ag_review) -> str | None:
    """"Approved 6 of 10" once the group approved with a quantity, else None."""
    if detail is None or ag_review is None or detail.quantity_reviewed is None:
        return None
    if ag_review.status != "APPROVED":
        return None
    return f"Approved {detail.quantity_reviewed} of {detail.quantity_requested}"
