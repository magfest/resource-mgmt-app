"""Task 12: block approval-group review actions once a line has a terminal
admin-final decision.

An admin can finalize a line directly from SUBMITTED (admin bypass) before
the approval group has reviewed it, leaving the AG review PENDING. Once the
admin has made a terminal decision (APPROVED or REJECTED) on a line, the
approval group must no longer be able to take review actions on it -
comments remain allowed.
"""
from app import db
from app.models import (
    WorkLineReview,
    WorkLineComment,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_APPROVED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _prep_admin_finalized(data):
    """Put the line into the buggy state: item SUBMITTED, an APPROVAL_GROUP
    review still PENDING, but the admin has already recorded a terminal
    ADMIN_FINAL decision (simulating the admin bypass from SUBMITTED)."""
    item = data["work_item"]
    line = data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    ag_review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_PENDING, created_by_user_id=data["admin"].id)
    admin_review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL,
        status=REVIEW_STATUS_APPROVED, created_by_user_id=data["admin"].id)
    line.status = WORK_LINE_STATUS_APPROVED
    db.session.add_all([ag_review, admin_review])
    db.session.commit()
    return item, line, ag_review, admin_review


def test_reviewer_action_blocked_after_admin_final(client, seed_draft_work_item):
    data = seed_draft_work_item
    item, line, ag_review, admin_review = _prep_admin_finalized(data)
    _login(client, "test:admin")
    # admins are reviewers for every line, so this checkout succeeds
    client.post(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/checkout")
    client.post(
        f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/approve",
        data={},
        follow_redirects=True,
    )
    db.session.refresh(ag_review)
    # The AG review must still be PENDING - the admin-finalized line could
    # not be re-decided by the approval group.
    assert ag_review.status == REVIEW_STATUS_PENDING


def test_reviewer_can_still_comment_after_admin_final(client, seed_draft_work_item):
    data = seed_draft_work_item
    item, line, ag_review, admin_review = _prep_admin_finalized(data)
    _login(client, "test:admin")
    client.post(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/checkout")
    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/comment",
        data={"comment": "still commenting after admin final"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    comment = WorkLineComment.query.filter_by(work_line_id=line.id).first()
    assert comment is not None
