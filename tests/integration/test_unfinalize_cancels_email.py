"""Unfinalize must leave a record of the email it withdrew."""
from datetime import datetime

from app import db
from app.models import (
    EmailOutbox,
    NotificationLog,
    WORK_ITEM_STATUS_FINALIZED,
)
from app.models.constants import (
    NOTIF_STATUS_CANCELLED,
    OUTBOX_STATUS_CANCELLED,
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_SENDING,
)


def _finalized_item_with_queued_release(data, status=OUTBOX_STATUS_QUEUED):
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.finalized_at = datetime.utcnow()
    now = datetime.utcnow()
    db.session.add(EmailOutbox(
        template_key="finalized", recipient_email="a@example.org",
        work_item_id=item.id, event_cycle_id=data["cycle"].id,
        status=status, dedup_key="k-release",
        dispatch_at=now, created_at=now, attempt_count=0))
    db.session.commit()
    return item


def test_unfinalize_logs_the_cancelled_release_email(
    app, seed_draft_work_item, super_admin_ctx
):
    """The outbox row prunes at 90 days; the log is the four-year record."""
    from app.routes.admin_final.helpers import unfinalize_work_item

    item = _finalized_item_with_queued_release(seed_draft_work_item)

    ok, err = unfinalize_work_item(
        item, "Board asked for changes", False, super_admin_ctx)
    db.session.commit()
    assert ok, err

    row = db.session.query(EmailOutbox).one()
    assert row.status == OUTBOX_STATUS_CANCELLED
    # Freed so a re-finalize can queue again rather than hitting ON CONFLICT.
    assert row.dedup_key is None

    log = db.session.query(NotificationLog).filter_by(
        template_key="finalized", status=NOTIF_STATUS_CANCELLED).one()
    assert log.recipient_email == "a@example.org"


def test_unfinalize_leaves_a_claimed_row_to_the_drainer(
    app, seed_draft_work_item, super_admin_ctx
):
    """A SENDING row is another process's to finish.

    Deliberate gap. The drainer re-checks the template and window at send
    time, so racing it here would add a second writer, not a guard.
    """
    from app.routes.admin_final.helpers import unfinalize_work_item

    item = _finalized_item_with_queued_release(
        seed_draft_work_item, status=OUTBOX_STATUS_SENDING)

    ok, err = unfinalize_work_item(
        item, "Board asked for changes", False, super_admin_ctx)
    db.session.commit()
    assert ok, err

    row = db.session.query(EmailOutbox).one()
    assert row.status == OUTBOX_STATUS_SENDING
    assert row.dedup_key == "k-release"
