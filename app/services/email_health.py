"""Read-only queries for outbox health and message lookup.

Two consumers: the operator health page and the support lookup that answers
"did this person get the email". Nothing here writes; the drainer owns every
state change on email_outbox.
"""
from datetime import datetime

from sqlalchemy import func

from app import db
from app.models import EmailMessageBody, EmailOutbox, NotificationLog, WorkItem
from app.models.constants import (
    OUTBOX_STATUS_RENDER_BLOCKED,
    OUTBOX_TERMINAL_STATUSES,
)


def get_queue_health(now=None):
    """Summarize outbox depth, backlog age, and render failures.

    Returns live_by_status, outcome_by_status, oldest_due_minutes,
    scheduled_count, render_blocked_count, and render_blocked_templates.
    Statuses with no rows are absent from the two dicts rather than reported
    as zero.

    The two dicts are split because one is a queue and the other is history.
    Live rows are work the drainer still owes; terminal rows are finished and
    linger only until the 90-day prune, so a single count reads as backlog
    that does not exist.
    """
    now = now or datetime.utcnow()

    depth_by_status = dict(
        db.session.query(EmailOutbox.status, func.count(EmailOutbox.id))
        .group_by(EmailOutbox.status)
        .all()
    )
    live_by_status = {
        status: count for status, count in depth_by_status.items()
        if status not in OUTBOX_TERMINAL_STATUSES
    }
    outcome_by_status = {
        status: count for status, count in depth_by_status.items()
        if status in OUTBOX_TERMINAL_STATUSES
    }

    # Backlog is work that is late, not work that is planned. Sub-project 2
    # parks rows weeks in the future on purpose, so a row dated 30 days out
    # would read as 30 days of backlog and pin the alarm red forever; an alarm
    # that is always red is one nobody reads. Terminal rows are finished work
    # and are not backlog either.
    oldest_due = (
        db.session.query(func.min(EmailOutbox.dispatch_at))
        .filter(
            EmailOutbox.dispatch_at <= now,
            EmailOutbox.status.notin_(OUTBOX_TERMINAL_STATUSES),
        )
        .scalar()
    )
    oldest_due_minutes = 0
    if oldest_due is not None:
        oldest_due_minutes = max(0, int((now - oldest_due).total_seconds() // 60))

    scheduled_count = (
        db.session.query(func.count(EmailOutbox.id))
        .filter(
            EmailOutbox.dispatch_at > now,
            EmailOutbox.status.notin_(OUTBOX_TERMINAL_STATUSES),
        )
        .scalar()
    ) or 0

    blocked_templates = [
        row[0]
        for row in db.session.query(EmailOutbox.template_key)
        .filter(EmailOutbox.status == OUTBOX_STATUS_RENDER_BLOCKED)
        .distinct()
        .all()
    ]

    return {
        "live_by_status": live_by_status,
        "outcome_by_status": outcome_by_status,
        "oldest_due_minutes": oldest_due_minutes,
        "scheduled_count": scheduled_count,
        "render_blocked_count": depth_by_status.get(OUTBOX_STATUS_RENDER_BLOCKED, 0),
        "render_blocked_templates": sorted(blocked_templates),
    }


# Reads NotificationLog, not email_outbox. The outbox is pruned at 90 days and
# holds only current work state; a lookup built on it would go blind on day 91
# without saying so.
def lookup_messages(recipient_email=None, public_id=None, limit=100):
    """Return the most recent notification log rows for a recipient or work item.

    With neither filter set, returns the most recent `limit` rows across all
    recipients; the limit is the only bound, so this never scans the table
    unbounded.
    """
    query = db.session.query(NotificationLog)

    if recipient_email:
        query = query.filter(
            func.lower(NotificationLog.recipient_email) == recipient_email.strip().lower()
        )

    if public_id:
        query = query.join(
            WorkItem, NotificationLog.work_item_id == WorkItem.id
        ).filter(WorkItem.public_id == public_id)

    rows = (
        query.order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
        .limit(limit)
        .all()
    )

    # One query for every body, not one per row. An operator loads this page
    # during an incident; an N+1 over 100 rows is felt.
    log_ids = [row.id for row in rows]
    with_body = set()
    if log_ids:
        with_body = {
            body_id
            for (body_id,) in db.session.query(EmailMessageBody.notification_log_id)
            .filter(EmailMessageBody.notification_log_id.in_(log_ids))
            .all()
        }

    return [
        {
            "id": row.id,
            "created_at": row.created_at,
            "template_key": row.template_key,
            "recipient_email": row.recipient_email,
            "status": row.status,
            "error_message": row.error_message,
            "provider_message_id": row.provider_message_id,
            "has_body": row.id in with_body,
        }
        for row in rows
    ]


def pending_messages(limit=100):
    """Return outbox rows that have not reached an outcome yet.

    The Notification Log is written only when a row terminates, so a queued
    email does not appear there at all. These rows are shown alongside it so
    the page answers "what is waiting" as well as "what happened"; they are
    not log rows and are not stored as any.

    Subject is None on purpose. A queued row has not been rendered, so no
    subject exists yet. Showing a blank is honest; guessing one would not be.
    """
    rows = (
        db.session.query(EmailOutbox)
        .filter(EmailOutbox.status.notin_(OUTBOX_TERMINAL_STATUSES))
        .order_by(EmailOutbox.dispatch_at, EmailOutbox.id)
        .limit(limit)
        .all()
    )
    return [
        {
            "created_at": row.created_at,
            "dispatch_at": row.dispatch_at,
            "template_key": row.template_key,
            "recipient_email": row.recipient_email,
            "status": row.status,
            "work_item_id": row.work_item_id,
            "attempt_count": row.attempt_count or 0,
            "last_error": row.last_error,
        }
        for row in rows
    ]
