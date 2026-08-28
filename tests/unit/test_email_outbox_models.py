"""Model-level tests for the email outbox tables."""
from datetime import datetime

from app.models import EmailOutbox, EmailSuppression
from app.models.constants import OUTBOX_CLAIMABLE_STATUSES, OUTBOX_STATUS_QUEUED
from app import db


def test_outbox_row_autoincrements_id_on_sqlite(app):
    """A plain BIGINT PK does not autoincrement on SQLite; the variant must."""
    with app.app_context():
        row = EmailOutbox(
            template_key="submitted",
            recipient_email="a@example.org",
            dispatch_at=datetime.utcnow(),
            status=OUTBOX_STATUS_QUEUED,
        )
        db.session.add(row)
        db.session.commit()
        assert row.id is not None


def test_dedup_key_is_unique(app):
    with app.app_context():
        from sqlalchemy.exc import IntegrityError
        for _ in range(2):
            db.session.add(EmailOutbox(
                template_key="finalized",
                recipient_email="b@example.org",
                dispatch_at=datetime.utcnow(),
                status=OUTBOX_STATUS_QUEUED,
                dedup_key="finalized:1:b@example.org",
            ))
        try:
            db.session.commit()
            assert False, "expected a unique-constraint violation"
        except IntegrityError:
            db.session.rollback()


def test_the_status_default_is_a_claimable_status():
    """A new row must land in a status the drainer will pick up.

    OUTBOX_CLAIMABLE_STATUSES is derived from the constant names, so a default
    written as a literal can drift from them. Nothing raises when it does: the
    row inserts, looks queued in the admin log, and is never claimed.
    """
    assert EmailOutbox.__table__.c.status.default.arg in OUTBOX_CLAIMABLE_STATUSES
