"""
Regression guard for the line-level requester-response notification.

When a requester responds to a NEEDS_INFO line, the reviewer who asked
for the info must be the one notified. This is subtle because
apply_review_decision unconditionally overwrites review.decided_by_user_id
with the acting user (app/routes/approvals/helpers.py:573):

    review.decided_by_user_id = user_ctx.user_id

For a RESPOND action user_ctx is the *responder*, so line_respond must
capture review.decided_by_user_id BEFORE calling apply_review_decision.
A prior version read it afterward, which sent the "a response arrived"
notification to the responder instead of the reviewer — the reviewer
never learned a response came in. See app/routes/approvals/reviews.py
(line_respond / line_adjust) and notify_response_received in
app/services/notifications.py.

This test drives the real HTTP route and asserts the original reviewer
is the id handed to notify_response_received. If the capture order
regresses, it fails.
"""
from datetime import datetime
from unittest.mock import patch, MagicMock

from app import db
from app.models import (
    WorkItem,
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_NEEDS_INFO,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_NEEDS_INFO,
)


def test_response_notifies_original_reviewer_not_responder(app, client, seed_draft_work_item):
    """
    Reviewer test:reviewer flags line 1 NEEDS_INFO; super-admin test:admin
    (a *different* user with rights) responds. The response_received
    notification should target the original reviewer, not the responder.
    """
    data = seed_draft_work_item
    reviewer_id = data["reviewer"].id       # "test:reviewer" — asked for info
    responder_id = data["admin"].id         # "test:admin"    — SUPER_ADMIN, responds

    # --- Arrange: put line 1 into NEEDS_INFO, decided by the reviewer ---
    with app.app_context():
        work_item = WorkItem.query.filter_by(
            public_id="TST2026-TESTDEPT-BUD-1"
        ).one()
        work_item.status = WORK_ITEM_STATUS_SUBMITTED

        line = work_item.lines[0]
        line.status = WORK_LINE_STATUS_NEEDS_INFO
        line.needs_requester_action = True

        review = WorkLineReview(
            work_line_id=line.id,
            stage=REVIEW_STAGE_APPROVAL_GROUP,
            approval_group_id=data["approval_group"].id,   # matches routed group
            status=REVIEW_STATUS_NEEDS_INFO,
            note="Please clarify the vendor.",
            decided_at=datetime.utcnow(),
            decided_by_user_id=reviewer_id,
            created_by_user_id=reviewer_id,
        )
        db.session.add(review)
        db.session.commit()

    # Log in as the responder (distinct from the reviewer).
    with client.session_transaction() as sess:
        sess["active_user_id"] = responder_id

    # --- Act: respond, capturing the id passed to the notifier ---
    fake_notify = MagicMock(return_value=True)
    with patch(
        "app.services.notifications.notify_response_received",
        fake_notify,
    ):
        response = client.post(
            "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/line/1/respond",
            data={"response": "The vendor is ACME Supplies."},
            follow_redirects=False,
        )

    # The response itself must succeed regardless of the bug.
    assert response.status_code == 302

    # The notifier must have been invoked exactly once.
    fake_notify.assert_called_once()

    # Second positional arg is reviewer_user_id (work_item, reviewer_user_id).
    notified_user_id = fake_notify.call_args.args[1]

    assert notified_user_id == reviewer_id, (
        f"Expected the original reviewer ({reviewer_id!r}) to be notified, "
        f"but notify_response_received was called with {notified_user_id!r} "
        f"(the responder). The reviewer never learns a response arrived — "
        f"they must watch the Slack channel instead."
    )