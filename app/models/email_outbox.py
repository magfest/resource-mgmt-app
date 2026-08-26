"""Email outbox, suppression list, and stored message bodies.

The outbox is not a cache and not a log. It is mutable work state: rows are
claimed, retried, and pruned at 90 days. The append-only record is
NotificationLog, which is kept for four years.
"""
from datetime import datetime

from sqlalchemy import BigInteger, Integer

from app import db
from .constants import OUTBOX_STATUS_QUEUED


class EmailOutbox(db.Model):
    __tablename__ = "email_outbox"

    # SQLite's rowid autoincrement only applies to a column declared exactly
    # INTEGER PRIMARY KEY. See NotificationLog.id for the same variant.
    id = db.Column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True)

    template_key = db.Column(db.String(64), nullable=False, index=True)
    recipient_email = db.Column(db.String(256), nullable=False)
    recipient_user_id = db.Column(db.String(64), nullable=True)

    dispatch_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    # Not a literal. OUTBOX_CLAIMABLE_STATUSES is derived from these constant
    # names, so a default that drifts from them produces a row the drainer
    # never claims, with nothing raising and no test failing.
    status = db.Column(db.String(16), nullable=False,
                       default=OUTBOX_STATUS_QUEUED, index=True)

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_email_outbox_work_item_id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_email_outbox_event_cycle_id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_email_outbox_department_id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", name="fk_email_outbox_work_type_id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    context_json = db.Column(db.Text, nullable=True)
    dedup_key = db.Column(db.String(256), nullable=True, unique=True)

    claimed_at = db.Column(db.DateTime, nullable=True)
    claimed_by = db.Column(db.String(64), nullable=True, index=True)

    attempt_count = db.Column(db.Integer, nullable=False, default=0)

    # Set on the first render failure, cleared on a successful render. Three
    # jobs: the seven-day age clock, the signal that tells the reaper not to
    # burn a transport attempt, and the gate on writing a duplicate audit row.
    blocked_since = db.Column(db.DateTime, nullable=True)

    last_error = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    provider_message_id = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.Index("ix_email_outbox_status_dispatch", "status", "dispatch_at"),
    )


class EmailSuppression(db.Model):
    __tablename__ = "email_suppression"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(256), nullable=False, unique=True, index=True)
    reason = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)


class EmailMessageBody(db.Model):
    """What was actually rendered and handed to SES.

    Separate from notification_logs because it expires at 24 months against
    that table's four years, and because no audit query should pay to read a
    12 KB body it does not want.
    """
    __tablename__ = "email_message_bodies"

    id = db.Column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True)
    notification_log_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("notification_logs.id", name="fk_email_message_bodies_log_id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    subject = db.Column(db.String(256), nullable=True)
    body_text = db.Column(db.Text, nullable=True)
    body_html = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
