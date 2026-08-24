"""Model-level tests for the email outbox tables."""
from datetime import datetime

from app.models import EmailMessageBody, EmailOutbox, EmailSuppression, NotificationLog
from app.models.constants import OUTBOX_STATUS_QUEUED
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


def test_message_body_links_to_its_log_row(app):
    """Do NOT assert ON DELETE CASCADE here.

    SQLite defaults PRAGMA foreign_keys to OFF and this app registers no
    connect listener to turn it on, so no ondelete clause fires in dev or
    test. The CASCADE is declared for Postgres; Task 13's prune deletes
    bodies explicitly so retention does not depend on it.
    """
    with app.app_context():
        log = NotificationLog(
            template_key="submitted", recipient_email="c@example.org", status="SENT",
        )
        db.session.add(log)
        db.session.commit()
        db.session.add(EmailMessageBody(
            notification_log_id=log.id, subject="s", body_text="t", body_html="<p>t</p>",
        ))
        db.session.commit()
        body = db.session.query(EmailMessageBody).one()
        assert body.notification_log_id == log.id
