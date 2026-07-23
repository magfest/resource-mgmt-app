"""Feature 2: APPROVED_NEEDS_REVIEW status registration."""
from app.models.constants import (
    WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
    REVIEW_ACTION_APPROVE_NEEDS_REVIEW,
)
from app.routes.work.helpers.formatting import friendly_status


def test_status_values():
    assert WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW == "APPROVED_NEEDS_REVIEW"
    assert REVIEW_STATUS_APPROVED_NEEDS_REVIEW == "APPROVED_NEEDS_REVIEW"
    assert REVIEW_ACTION_APPROVE_NEEDS_REVIEW == "APPROVE_NEEDS_REVIEW"


def test_friendly_label():
    assert friendly_status("APPROVED_NEEDS_REVIEW") == "Recommended With Comments"


def test_sync_line_status_maps_new_status(app):
    from app.models import WorkLine, WorkLineReview
    from app.routes.approvals.helpers import sync_line_status
    line = WorkLine(work_item_id=1, line_number=1, status="PENDING")
    review = WorkLineReview(
        work_line_id=1, stage="APPROVAL_GROUP",
        status="APPROVED_NEEDS_REVIEW",
    )
    sync_line_status(line, review)
    assert line.status == "APPROVED_NEEDS_REVIEW"
    assert line.needs_requester_action is False
