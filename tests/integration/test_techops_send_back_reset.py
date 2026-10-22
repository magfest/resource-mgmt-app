"""reset_submitted_to_draft undoes a TechOps submit, review rows included."""
from datetime import datetime

from app import db
from app.models import (
    WorkLineReview,
    AUDIT_EVENT_RECALL_TO_DRAFT,
    REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.routes import UserContext
from app.routes.work.helpers.lifecycle import (
    RETURN_ACTION_PULL_BACK,
    RETURN_ACTION_SEND_BACK,
    reset_submitted_to_draft,
    submit_work_item,
)
from tests.integration.techops_review_helpers import add_routed_line
from tests.integration.test_techops_review_affordances import techops_review  # noqa: F401


def _ctx(user_id):
    return UserContext(
        user_id=user_id, user=None, roles=(),
        is_super_admin=False, approval_group_ids=set(),
    )


def test_reset_returns_item_to_draft(techops_review):
    item, line = techops_review["item"], techops_review["line"]
    item.submitted_at = datetime.utcnow()
    item.submitted_by_user_id = "test:author"
    item.checked_out_by_user_id = "test:other"
    item.checked_out_at = item.checked_out_expires_at = datetime.utcnow()
    line.needs_requester_action = True
    db.session.commit()

    reset_submitted_to_draft(item, _ctx("test:author"), action=RETURN_ACTION_PULL_BACK)
    db.session.commit()

    assert item.status == WORK_ITEM_STATUS_DRAFT
    assert item.submitted_at is None and item.submitted_by_user_id is None
    assert item.checked_out_by_user_id is None
    assert item.checked_out_at is None and item.checked_out_expires_at is None
    assert WorkLineReview.query.count() == 0
    assert line.status == WORK_LINE_STATUS_PENDING
    assert line.needs_requester_action is False
    assert line.current_review_stage is None


def test_reset_snapshot_records_deleted_reviews(techops_review):
    item, line = techops_review["item"], techops_review["line"]
    review = WorkLineReview.query.filter_by(work_line_id=line.id).one()
    review.status = REVIEW_STATUS_REJECTED
    review.decided_by_user_id = "test:reviewer"
    review.note = "No budget for this"
    db.session.commit()

    event = reset_submitted_to_draft(
        item, _ctx("test:admin"), action=RETURN_ACTION_SEND_BACK, reason="Wrong rooms")
    db.session.commit()

    assert event.event_type == AUDIT_EVENT_RECALL_TO_DRAFT
    assert event.created_by_user_id == "test:admin"
    assert event.reason == "Wrong rooms"
    assert event.old_value == WORK_ITEM_STATUS_SUBMITTED
    assert event.new_value == WORK_ITEM_STATUS_DRAFT
    assert event.snapshot["action"] == "SEND_BACK"
    assert event.snapshot["from_status"] == WORK_ITEM_STATUS_SUBMITTED
    assert event.snapshot["reviews"] == [{
        "line_number": 1, "approval_group_code": "TECHOPS_GEN",
        "status": "REJECTED", "decided_by_user_id": "test:reviewer",
        "note": "No budget for this",
    }]


def test_resubmit_after_reset_creates_one_review_per_line(techops_review):
    item = techops_review["item"]
    add_routed_line(item, 2, "TECHOPS_GEN")
    reset_submitted_to_draft(item, _ctx("test:author"), action=RETURN_ACTION_PULL_BACK)
    db.session.commit()

    submit_work_item(item, _ctx("test:author"))
    db.session.commit()

    counts = sorted(
        WorkLineReview.query.filter_by(work_line_id=line.id).count()
        for line in item.lines
    )
    assert counts == [1, 1]
    assert item.status == WORK_ITEM_STATUS_SUBMITTED
