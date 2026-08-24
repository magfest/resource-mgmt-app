"""Claim, reap, and per-row processing for the outbox drainer.

The run loop (`drain_outbox`) is separate work; this module stops at the
primitives it calls. Claim is claim-then-select-by-run-id rather than
`UPDATE ... RETURNING`: Postgres does not accept `LIMIT` on `UPDATE`, and
SQLite only gained `RETURNING` in 3.35. `process_row` commits per row, so a
killed dyno loses at most the row in flight and the reaper recovers that one.
"""
import json
from datetime import datetime, timedelta

from flask import current_app

from app import db
from app.models import (
    Department,
    EmailMessageBody,
    EmailOutbox,
    EmailSuppression,
    EventCycle,
    WorkItem,
    WorkType,
)
from app.models.constants import (
    NOTIF_STATUS_CANCELLED,
    NOTIF_STATUS_FAILED,
    NOTIF_STATUS_RENDER_BLOCKED,
    NOTIF_STATUS_SENT,
    NOTIF_STATUS_SUPPRESSED,
    OUTBOX_CLAIMABLE_STATUSES,
    OUTBOX_STATUS_CANCELLED,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_RENDER_BLOCKED,
    OUTBOX_STATUS_SENDING,
    OUTBOX_STATUS_SENT,
    OUTBOX_STATUS_SUPPRESSED,
)
from app.services.email import (
    build_message_parts,
    is_email_enabled,
    send_via_ses,
    write_notification_log,
)
from app.services.email_errors import (
    ACCOUNT_HALT,
    PERMANENT,
    TRANSIENT_ACCOUNT,
    AccountHaltError,
    ThrottleStopError,
    classify_ses_error,
)
from app.services.email_templates import get_template, render_email_template

# Task 10 sets these in app.config. The defaults live here as well so one
# missing key cannot raise in the middle of a drain.
_DEFAULT_MAX_ATTEMPTS = 7
_DEFAULT_RENDER_RETRY_MINUTES = 60
_DEFAULT_RENDER_MAX_AGE_DAYS = 7

# NotificationLog carries its own status vocabulary. Map explicitly rather than
# writing the outbox status through, so the audit table keeps one set of names.
_LOG_STATUS = {
    OUTBOX_STATUS_SENT: NOTIF_STATUS_SENT,
    OUTBOX_STATUS_SUPPRESSED: NOTIF_STATUS_SUPPRESSED,
    OUTBOX_STATUS_FAILED: NOTIF_STATUS_FAILED,
    OUTBOX_STATUS_CANCELLED: NOTIF_STATUS_CANCELLED,
    OUTBOX_STATUS_RENDER_BLOCKED: NOTIF_STATUS_RENDER_BLOCKED,
}

# Reminder rows are the one kind that renders without a work item. Match on the
# suffix because resolve_template_key may prefix a work-type code.
_REMINDER_KIND = "submission_reminder"


def transport_backoff(attempt_count: int) -> timedelta:
    """Return the delay before the next transport retry, doubling from 20 minutes."""
    return timedelta(minutes=10 * (2 ** attempt_count))


def reap_stale_claims(now=None) -> int:
    """Return rows stranded in SENDING by a crashed run.

    A row with blocked_since set never reached the transport, so it goes back
    to RENDER_BLOCKED with its attempt_count untouched. Incrementing it would
    burn a delivery attempt on a template problem.
    """
    now = now or datetime.utcnow()
    cutoff = now - timedelta(minutes=30)
    rows = db.session.query(EmailOutbox).filter(
        EmailOutbox.status == OUTBOX_STATUS_SENDING,
        EmailOutbox.claimed_at <= cutoff,
    ).all()
    for row in rows:
        if row.blocked_since:
            row.status = OUTBOX_STATUS_RENDER_BLOCKED
        else:
            row.status = OUTBOX_STATUS_QUEUED
            row.attempt_count = (row.attempt_count or 0) + 1
        row.claimed_at = None
        row.claimed_by = None
    db.session.commit()
    return len(rows)


def _due_rows_subquery(now, batch_size: int):
    """Build the claimable-row-id subquery used by claim_due_rows.

    Split out so a test can compile it against the Postgres dialect and pin
    FOR UPDATE SKIP LOCKED without duplicating this query by hand.
    """
    return (
        db.session.query(EmailOutbox.id)
        .filter(EmailOutbox.status.in_(OUTBOX_CLAIMABLE_STATUSES),
                EmailOutbox.dispatch_at <= now)
        .order_by(EmailOutbox.dispatch_at, EmailOutbox.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )


def claim_due_rows(run_id: str, batch_size: int, now=None) -> list:
    """Claim due rows for this run, atomically.

    Claim-then-select-by-run-id rather than UPDATE ... RETURNING: Postgres does
    not accept LIMIT on UPDATE, and SQLite only gained RETURNING in 3.35. The
    run id makes the follow-up SELECT exact.

    Includes RENDER_BLOCKED rows (OUTBOX_CLAIMABLE_STATUSES), not just QUEUED:
    a blocked row is due work waiting on a person to fix a template, not a
    dead row.

    Heroku Scheduler's documented jitter can overlap two runs. The subquery
    carries FOR UPDATE SKIP LOCKED so a second run's row selection never
    includes a row the first run is still holding, rather than relying on
    Postgres to re-check the status filter after a lock wait resolves.
    SQLAlchemy's SQLite dialect drops the clause silently instead of raising,
    so this needs no dialect branch; SQLite is single-writer and has no
    concurrent claim to protect against. Do not remove this as dead weight
    because it disappears from the SQLite-compiled SQL in dev.
    """
    now = now or datetime.utcnow()
    table = EmailOutbox.__table__
    subq = _due_rows_subquery(now, batch_size)
    db.session.execute(
        table.update()
        .where(table.c.id.in_(subq))
        .values(status=OUTBOX_STATUS_SENDING, claimed_at=now, claimed_by=run_id)
    )
    db.session.commit()
    return (
        db.session.query(EmailOutbox)
        .filter(EmailOutbox.claimed_by == run_id,
                EmailOutbox.status == OUTBOX_STATUS_SENDING)
        .order_by(EmailOutbox.dispatch_at, EmailOutbox.id)
        .all()
    )


def _config(key: str, default):
    return current_app.config.get(key, default)


def _missing_required_entities(row, work_item, department, event_cycle):
    """Name the first entity the row needs and no longer has, or None.

    Keyed on the template key, not on the FK shape. Every work-item FK is
    ON DELETE SET NULL, so a deleted request and a row that never had one look
    identical once the id is gone; an FK-shape test would read a deleted
    request as a reminder and render a work-item template against nothing.
    """
    if row.template_key.endswith(_REMINDER_KIND):
        if department is None:
            return "department"
        if event_cycle is None:
            return "event_cycle"
        return None
    if work_item is None:
        return "work item"
    return None


def _clear_claim(row):
    row.claimed_at = None
    row.claimed_by = None


def _terminate(row, status, reason, rendered=None, parts=None,
               provider_message_id=None) -> str:
    """Write the row's final state, its audit row, and its body. Commits.

    Every terminal outcome writes a NotificationLog row, cancellations
    included. The outbox row is pruned at 90 days; the log is the four-year
    record, and "the request it referred to was deleted" is a legitimate answer
    to "why did I not get my email".
    """
    row.status = status
    row.last_error = reason
    if status in (OUTBOX_STATUS_CANCELLED, OUTBOX_STATUS_FAILED):
        # Free the dedup key. A terminal row otherwise owns it until the
        # 90-day prune, so the documented operator recovery of re-enqueueing a
        # failed row hits ON CONFLICT DO NOTHING and silently does nothing.
        # SENT and SUPPRESSED keep theirs; those emails reached their outcome.
        row.dedup_key = None

    log = write_notification_log(
        recipient_email=row.recipient_email,
        template_key=row.template_key,
        status=_LOG_STATUS[status],
        work_item_id=row.work_item_id,
        recipient_user_id=row.recipient_user_id,
        subject=rendered.subject if rendered else None,
        provider_message_id=provider_message_id,
        error=reason,
        event_cycle_id=row.event_cycle_id,
    )
    if parts is not None:
        db.session.flush()  # log.id is the body's FK
        db.session.add(EmailMessageBody(
            notification_log_id=log.id,
            subject=rendered.subject if rendered else None,
            body_text=parts.text,
            body_html=parts.html,
        ))
    db.session.commit()
    return status


def _block_on_render(row, reason) -> str:
    """Park the row for a template fix, or fail it once it is too old. Commits.

    Writes an audit row on the first block only. A row blocked the full seven
    days retries about 168 times, and one log row per retry buries the trail.
    Never touches attempt_count: a template problem is not a delivery attempt.
    """
    now = datetime.utcnow()
    max_age = timedelta(days=_config("EMAIL_RENDER_MAX_AGE_DAYS",
                                     _DEFAULT_RENDER_MAX_AGE_DAYS))
    if row.blocked_since and now - row.blocked_since >= max_age:
        return _terminate(row, OUTBOX_STATUS_FAILED, reason)

    first_block = row.blocked_since is None
    row.status = OUTBOX_STATUS_RENDER_BLOCKED
    row.last_error = reason
    row.dispatch_at = now + timedelta(
        minutes=_config("EMAIL_RENDER_RETRY_MINUTES", _DEFAULT_RENDER_RETRY_MINUTES)
    )
    _clear_claim(row)
    if first_block:
        row.blocked_since = now
        write_notification_log(
            recipient_email=row.recipient_email,
            template_key=row.template_key,
            status=_LOG_STATUS[OUTBOX_STATUS_RENDER_BLOCKED],
            work_item_id=row.work_item_id,
            recipient_user_id=row.recipient_user_id,
            error=reason,
            event_cycle_id=row.event_cycle_id,
        )
    db.session.commit()
    return OUTBOX_STATUS_RENDER_BLOCKED


def _requeue_or_fail(row, reason, rendered, parts) -> str:
    """Burn one delivery attempt, then either back off or give up. Commits.

    Increment before the test. EMAIL_MAX_ATTEMPTS counts total attempts
    including the first, so 7 means one send plus six retries at 20, 40, 80,
    160, 320, and 640 minutes: about 21 hours. Testing before incrementing
    yields eight attempts and about 42 hours, twice the decided window.
    """
    row.attempt_count = (row.attempt_count or 0) + 1
    if row.attempt_count >= _config("EMAIL_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS):
        return _terminate(row, OUTBOX_STATUS_FAILED, reason,
                          rendered=rendered, parts=parts)
    row.status = OUTBOX_STATUS_QUEUED
    row.last_error = reason
    row.dispatch_at = datetime.utcnow() + transport_backoff(row.attempt_count)
    _clear_claim(row)
    db.session.commit()
    return OUTBOX_STATUS_QUEUED


def _handle_transport_failure(row, result, rendered, parts) -> str:
    """Route one SES failure by its error class. Commits before any raise."""
    reason = result.error or f"SES error {result.error_code or 'unknown'}"
    kind = classify_ses_error(result.error_code)

    if kind == ACCOUNT_HALT:
        # Sending is paused account-wide. Requeue without burning an attempt;
        # no row in this batch can succeed, so the run ends here.
        row.status = OUTBOX_STATUS_QUEUED
        row.last_error = reason
        _clear_claim(row)
        db.session.commit()
        raise AccountHaltError(reason)

    if kind == PERMANENT:
        # SES will reject this message every time. Further attempts only delay
        # the operator seeing it.
        return _terminate(row, OUTBOX_STATUS_FAILED, reason,
                          rendered=rendered, parts=parts)

    status = _requeue_or_fail(row, reason, rendered, parts)
    if kind == TRANSIENT_ACCOUNT:
        raise ThrottleStopError(reason)
    return status


def process_row(row, run_id: str) -> str:
    """Run one claimed outbox row to its next state and commit.

    Order is load, resolve, render, suppress, send. Rendering comes before the
    suppression check so a suppressed recipient still has a stored record of
    what would have gone to them, and so a dev box with EMAIL_ENABLED false
    fills the archive instead of writing empty SUPPRESSED rows.

    Returns the status written to the row. Raises AccountHaltError or
    ThrottleStopError to end the run; every other failure is contained.
    """
    # Load every FK the row carries, not just work_item. Reminder rows have
    # work_item_id NULL and render from department plus event_cycle.
    work_item = db.session.get(WorkItem, row.work_item_id) if row.work_item_id else None
    department = db.session.get(Department, row.department_id) if row.department_id else None
    event_cycle = db.session.get(EventCycle, row.event_cycle_id) if row.event_cycle_id else None
    work_type = db.session.get(WorkType, row.work_type_id) if row.work_type_id else None

    missing = _missing_required_entities(row, work_item, department, event_cycle)
    if missing:
        return _terminate(row, OUTBOX_STATUS_CANCELLED,
                          f"Referenced {missing} no longer exists")

    # A missing or inactive template is deliberate silence, not an error.
    template = get_template(row.template_key)
    if template is None or not template.is_active:
        return _terminate(row, OUTBOX_STATUS_CANCELLED,
                          f"Template {row.template_key} missing or inactive")

    # render_email_template catches only TemplateSyntaxError and UndefinedError.
    # A filter type error, a division by zero, or a stale ORM instance raises
    # straight past it. Uncaught, that kills the run, and because the claim
    # orders by dispatch_at the same row is claimed first on every later tick:
    # all email stops indefinitely. json.loads is inside the guard for the same
    # reason.
    try:
        context = {
            "work_item": work_item,
            "department": department,
            "event_cycle": event_cycle,
            "work_type": work_type,
            "base_url": _config("BASE_URL", "https://budget.magfest.org"),
            "recipient_email": row.recipient_email,
        }
        if row.context_json:
            context.update(json.loads(row.context_json))
        rendered = render_email_template(row.template_key, context)
    except Exception as e:
        return _block_on_render(row, f"Template {row.template_key} raised: {e}")
    if rendered is None:
        return _block_on_render(row, f"Template {row.template_key} failed to render")

    if row.blocked_since:
        # Repaired. Clear the marker so the seven-day clock does not carry over.
        row.blocked_since = None

    parts = build_message_parts(rendered.body_text)

    suppressed = db.session.query(EmailSuppression.id).filter(
        db.func.lower(EmailSuppression.email) == row.recipient_email.lower()
    ).first()
    if suppressed or not is_email_enabled():
        reason = "Recipient suppressed" if suppressed else "EMAIL_ENABLED is false"
        return _terminate(row, OUTBOX_STATUS_SUPPRESSED, reason,
                          rendered=rendered, parts=parts)

    result = send_via_ses(row.recipient_email, rendered.subject, parts)
    if result.status == NOTIF_STATUS_SENT:
        row.sent_at = datetime.utcnow()
        row.provider_message_id = result.provider_message_id
        return _terminate(row, OUTBOX_STATUS_SENT, None, rendered=rendered,
                          parts=parts, provider_message_id=result.provider_message_id)
    return _handle_transport_failure(row, result, rendered, parts)
