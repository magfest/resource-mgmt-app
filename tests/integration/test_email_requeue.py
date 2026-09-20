"""Returning a terminal outbox row to the queue.

Recovery before this was unfinalize plus re-finalize, one item at a time.
"""
from datetime import datetime

from app import db
from app.models import EmailOutbox
from app.models.constants import (
    OUTBOX_STATUS_CANCELLED,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_SENT,
    OUTBOX_STATUS_SUPPRESSED,
)

REQUEUE = "/admin/email/outbox/{}/requeue"


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def _row(status, dedup_key=None, **kwargs):
    now = datetime.utcnow()
    row = EmailOutbox(
        template_key="finalized", recipient_email="a@example.org",
        status=status, dedup_key=dedup_key,
        dispatch_at=now, created_at=now, attempt_count=0, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row.id


def test_requeue_returns_a_cancelled_row_to_the_queue(
    app, client, seed_workflow_data
):
    with app.app_context():
        row_id = _row(OUTBOX_STATUS_CANCELLED, last_error="Template inactive")
        db.session.get(EmailOutbox, row_id).attempt_count = 3
        db.session.commit()

    _login(client, "test:admin")
    resp = client.post(REQUEUE.format(row_id), follow_redirects=True)

    assert resp.status_code == 200
    with app.app_context():
        row = db.session.get(EmailOutbox, row_id)
        assert row.status == OUTBOX_STATUS_QUEUED
        # The counter resets: the drainer's backoff reads it, and a row that
        # kept 3 attempts would be one failure from permanent.
        assert row.attempt_count == 0
        assert row.last_error is None
        assert row.blocked_since is None


def test_requeue_returns_a_failed_row_to_the_queue(app, client, seed_workflow_data):
    with app.app_context():
        row_id = _row(OUTBOX_STATUS_FAILED, last_error="SES said no")

    _login(client, "test:admin")
    client.post(REQUEUE.format(row_id), follow_redirects=True)

    with app.app_context():
        assert db.session.get(EmailOutbox, row_id).status == OUTBOX_STATUS_QUEUED


def test_requeue_refuses_a_sent_row(app, client, seed_workflow_data):
    """SENT and SUPPRESSED keep their dedup key.

    Requeueing one would hit ON CONFLICT DO NOTHING on the next enqueue and
    appear to work while doing nothing.
    """
    with app.app_context():
        row_id = _row(OUTBOX_STATUS_SENT, dedup_key="k-sent")

    _login(client, "test:admin")
    resp = client.post(REQUEUE.format(row_id), follow_redirects=True)

    assert resp.status_code == 200
    with app.app_context():
        row = db.session.get(EmailOutbox, row_id)
        assert row.status == OUTBOX_STATUS_SENT
        assert row.dedup_key == "k-sent"


def test_requeue_refuses_a_suppressed_row(app, client, seed_workflow_data):
    with app.app_context():
        row_id = _row(OUTBOX_STATUS_SUPPRESSED, dedup_key="k-supp")

    _login(client, "test:admin")
    client.post(REQUEUE.format(row_id), follow_redirects=True)

    with app.app_context():
        row = db.session.get(EmailOutbox, row_id)
        assert row.status == OUTBOX_STATUS_SUPPRESSED
        assert row.dedup_key == "k-supp"


def test_requeue_requires_admin(app, client, seed_workflow_data):
    with app.app_context():
        row_id = _row(OUTBOX_STATUS_CANCELLED)

    _login(client, "test:reviewer")
    resp = client.post(REQUEUE.format(row_id))

    assert resp.status_code in (302, 403)
    with app.app_context():
        assert db.session.get(EmailOutbox, row_id).status == OUTBOX_STATUS_CANCELLED


def test_requeue_of_a_missing_row_is_404(app, client, seed_workflow_data):
    _login(client, "test:admin")
    assert client.post(REQUEUE.format(999999)).status_code == 404


def test_the_debug_panel_lists_cancelled_rows_with_a_requeue_form(
    app, client, seed_workflow_data
):
    """The panel queried FAILED only, so a cancelled row had no way back.

    Silence is the common case now that a window or an override can stop an
    email, which is why the section covers both statuses.
    """
    with app.app_context():
        row_id = _row(OUTBOX_STATUS_CANCELLED, last_error="Send window closed")

    _login(client, "test:admin")
    body = client.get("/admin/email/").get_data(as_text=True)

    assert "Send window closed" in body
    assert REQUEUE.format(row_id) in body
