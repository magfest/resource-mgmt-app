"""
Integration tests for notification resilience.

Verifies that workflow operations complete even when queueing the
notification fails.

The resilience boundary moved. Notifications used to be sent inline after
the workflow commit, inside a try/except in the route; a failure there could
not undo the submit. They are now queued inside the submit transaction, so
the guard is the per-recipient try/except in _enqueue_emails. A broken
enqueue costs the email, not the submit.

Finalize notifies inline again, inside the release branch of
finalize_work_item. Its resilience is the try/except around that call, covered
in test_board_release_trigger.py.
"""
from unittest.mock import patch

from app import db
from app.models import (
    EmailOutbox,
    WorkItem,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
)


class TestNotificationResilience:
    """Verify workflow operations succeed even when notifications fail."""

    def test_submit_succeeds_when_enqueue_raises(self, app, client, seed_draft_work_item):
        """
        Work item submission should complete (status=AWAITING_DISPATCH)
        even if the per-recipient enqueue raises.
        """
        with client.session_transaction() as sess:
            sess["active_user_id"] = "test:admin"

        # Raise from enqueue_email, not from notify_*. _enqueue_emails catches
        # per recipient; a raise from notify_* itself is a programming error
        # that must fail the request, which test_notify_enqueues covers.
        with patch(
            "app.services.notifications.enqueue_email",
            side_effect=RuntimeError("outbox insert failed"),
        ):
            response = client.post(
                "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/submit",
                follow_redirects=False,
            )

        # Should redirect (success), not 500
        assert response.status_code == 302

        # Work item should be AWAITING_DISPATCH despite the queue failure,
        # and the lost email must not have left a partial row behind.
        db.session.expire_all()
        work_item = WorkItem.query.filter_by(
            public_id="TST2026-TESTDEPT-BUD-1"
        ).one()
        assert work_item.status == WORK_ITEM_STATUS_AWAITING_DISPATCH
        assert work_item.submitted_at is not None
        assert db.session.query(EmailOutbox).count() == 0

    def test_submit_succeeds_when_notification_works(self, app, client, seed_draft_work_item):
        """
        Baseline: submission works normally when notifications succeed.
        """
        with client.session_transaction() as sess:
            sess["active_user_id"] = "test:admin"

        response = client.post(
            "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/submit",
            follow_redirects=False,
        )

        assert response.status_code == 302

        db.session.expire_all()
        work_item = WorkItem.query.filter_by(
            public_id="TST2026-TESTDEPT-BUD-1"
        ).one()
        assert work_item.status == WORK_ITEM_STATUS_AWAITING_DISPATCH
        assert db.session.query(EmailOutbox).count() > 0
