"""Retention prune: bodies expire at 24 months, log rows at four years.

The body delete must be explicit. SQLite leaves PRAGMA foreign_keys off and
this app registers no connect listener, so the ON DELETE CASCADE on
email_message_bodies fires on Postgres and silently does not in dev or test.
test_pruning_a_log_row_leaves_no_orphan_body is the check on that.
"""
from datetime import datetime, timedelta

from app import db
from app.models import EmailMessageBody, NotificationLog
from app.models.constants import NOTIF_STATUS_SENT
from app.services.email_drainer import prune_email_audit


def _log_with_body(age_days: int) -> NotificationLog:
    """Create one log row and its body, both aged age_days."""
    created = datetime.utcnow() - timedelta(days=age_days)
    log = NotificationLog(
        template_key="submission_reminder",
        recipient_email="a@example.org",
        status=NOTIF_STATUS_SENT,
        created_at=created,
    )
    db.session.add(log)
    db.session.commit()
    db.session.add(EmailMessageBody(
        notification_log_id=log.id,
        subject="Subj",
        body_text="hi",
        body_html="<p>hi</p>",
        created_at=created,
    ))
    db.session.commit()
    return log


def test_body_pruned_but_log_row_kept(app):
    """800 days is past 24 months and well inside four years."""
    with app.app_context():
        _log_with_body(800)

        prune_email_audit()

        assert db.session.query(EmailMessageBody).count() == 0
        assert db.session.query(NotificationLog).count() == 1


def test_pruning_a_log_row_leaves_no_orphan_body(app):
    """A log past four years takes its body with it, whatever the body window.

    Body retention is raised past the log window on purpose. At the default 24
    months the body is already expired on its own, so the assertion would hold
    even if the log delete orphaned it, and the test would prove nothing.
    """
    with app.app_context():
        app.config["EMAIL_BODY_RETENTION_MONTHS"] = 120
        _log_with_body(4 * 365 + 30)

        prune_email_audit()

        assert db.session.query(EmailMessageBody).count() == 0
        assert db.session.query(NotificationLog).count() == 0


def test_recent_rows_survive(app):
    """Without this, a prune that deleted everything would pass the other two."""
    with app.app_context():
        _log_with_body(0)

        prune_email_audit()

        assert db.session.query(EmailMessageBody).count() == 1
        assert db.session.query(NotificationLog).count() == 1
