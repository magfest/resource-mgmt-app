"""
Integration tests for notification resilience.

Verifies that workflow operations complete successfully even when
email notifications fail (e.g., SES outage, template error).
"""
from datetime import datetime
from unittest.mock import patch

from app import db
from app.models import (
    WorkItem,
    WorkLineReview,
    ApprovalGroup,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_FINALIZED,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
)


class TestNotificationResilience:
    """Verify workflow operations succeed even when notifications fail."""

    def test_submit_succeeds_when_notification_raises(self, app, client, seed_draft_work_item):
        """
        Work item submission should complete (status=AWAITING_DISPATCH)
        even if notify_budget_submitted raises an exception.
        """
        with client.session_transaction() as sess:
            sess["active_user_id"] = "test:admin"

        # Mock the notification to raise an exception (simulates SES outage)
        with patch(
            "app.services.notifications.notify_budget_submitted",
            side_effect=RuntimeError("SES connection timeout"),
        ):
            response = client.post(
                "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/submit",
                follow_redirects=False,
            )

        # Should redirect (success), not 500
        assert response.status_code == 302

        # Work item should be AWAITING_DISPATCH despite notification failure
        with app.app_context():
            work_item = WorkItem.query.filter_by(
                public_id="TST2026-TESTDEPT-BUD-1"
            ).one()
            assert work_item.status == WORK_ITEM_STATUS_AWAITING_DISPATCH
            assert work_item.submitted_at is not None

    def test_submit_succeeds_when_notification_works(self, app, client, seed_draft_work_item):
        """
        Baseline: submission works normally when notifications succeed.
        """
        with client.session_transaction() as sess:
            sess["active_user_id"] = "test:admin"

        with patch(
            "app.services.notifications.notify_budget_submitted",
            return_value=1,
        ):
            response = client.post(
                "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/submit",
                follow_redirects=False,
            )

        assert response.status_code == 302

        with app.app_context():
            work_item = WorkItem.query.filter_by(
                public_id="TST2026-TESTDEPT-BUD-1"
            ).one()
            assert work_item.status == WORK_ITEM_STATUS_AWAITING_DISPATCH

    def test_finalize_succeeds_when_notification_raises(self, app, client, seed_draft_work_item):
        """
        Finalization should complete (status=FINALIZED) even if
        notify_budget_finalized raises an exception.
        """
        # Move the work item to SUBMITTED with an approved review so it can be finalized
        with app.app_context():
            work_item = WorkItem.query.filter_by(
                public_id="TST2026-TESTDEPT-BUD-1"
            ).one()
            work_item.status = WORK_ITEM_STATUS_SUBMITTED
            work_item.submitted_at = datetime.utcnow()
            work_item.submitted_by_user_id = "test:admin"

            line = work_item.lines[0]
            ag = ApprovalGroup.query.first()
            review = WorkLineReview(
                work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
                approval_group_id=ag.id, status=REVIEW_STATUS_APPROVED,
                approved_amount_cents=5000,
                decided_at=datetime.utcnow(),
                decided_by_user_id="test:admin",
                created_by_user_id="test:admin",
            )
            db.session.add(review)
            db.session.commit()
            work_item_id = work_item.id

        with client.session_transaction() as sess:
            sess["active_user_id"] = "test:admin"

        # Mock the notification to raise an exception
        with patch(
            "app.services.notifications.notify_budget_finalized",
            side_effect=RuntimeError("SES connection timeout"),
        ):
            response = client.post(
                f"/admin/final-review/finalize/{work_item_id}",
                data={"note": "Approved for event"},
                follow_redirects=False,
            )

        # Should redirect (success), not 500
        assert response.status_code == 302

        # Work item should be FINALIZED despite notification failure
        with app.app_context():
            work_item = WorkItem.query.filter_by(
                public_id="TST2026-TESTDEPT-BUD-1"
            ).one()
            assert work_item.status == WORK_ITEM_STATUS_FINALIZED
