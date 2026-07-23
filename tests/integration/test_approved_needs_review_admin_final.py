"""Feature 2: an APPROVED_NEEDS_REVIEW line counts as a decision for finalize."""
from app import db
from app.models import (
    WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP,
    WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
)
from app.routes.admin_final.helpers import can_finalize_work_item


def test_flagged_line_makes_item_finalizable(app, seed_draft_work_item):
    data = seed_draft_work_item
    item = data["work_item"]; line = data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        created_by_user_id=data["admin"].id))
    db.session.commit()
    can, reason = can_finalize_work_item(item)
    assert can is True, reason
