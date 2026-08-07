"""
Integration tests for notification resilience.

Verifies that workflow operations complete successfully even when
email notifications fail (e.g., SES outage, template error).

Finalize no longer notifies inline; `flask send-board-release-emails` owns
that send. Its resilience is that command's test coverage, not this file's.
"""
from unittest.mock import patch

from app.models import (
    WorkItem,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
)


class TestNotificationResilience:
    """Verify workflow operations succeed even when notifications fail."""

    def test_submit_succeeds_when_notification_raises(self, app, client, seed_draft_work_item):
        """
        Work item submission should complete (status=AWAITING_DISPATCH)
        even if notify_work_item_submitted raises an exception.
        """
        with client.session_transaction() as sess:
            sess["active_user_id"] = "test:admin"

        # Mock the notification to raise an exception (simulates SES outage)
        with patch(
            "app.services.notifications.notify_work_item_submitted",
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
            "app.services.notifications.notify_work_item_submitted",
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

    # test_finalize_succeeds_when_notification_raises removed: finalize no
    # longer calls notify_work_item_finalized, so the mocked exception can't
    # fire. Coverage moves to the send-board-release-emails command.
