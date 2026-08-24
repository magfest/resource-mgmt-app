"""Claim, reap, per-row processing, and the run loop for the outbox drainer.

`drain_outbox` is the only code path that calls SES; everything else in the app
enqueues. Claim is claim-then-select-by-run-id rather than
`UPDATE ... RETURNING`: Postgres does not accept `LIMIT` on `UPDATE`, and
SQLite only gained `RETURNING` in 3.35. `process_row` commits per row, so a
killed dyno loses at most the row in flight and the reaper recovers that one.
"""
import json
import signal
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from flask import current_app

from app import db
from app.models import (
    Department,
    EmailMessageBody,
    EmailOutbox,
    EmailSuppression,
    EventCycle,
    NotificationLog,
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
    OUTBOX_TERMINAL_STATUSES,
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
from app.services.slack import send_slack_message

# app.config carries these; the defaults live here as well so one missing key
# cannot raise in the middle of a drain.
_DEFAULT_MAX_ATTEMPTS = 7
_DEFAULT_RENDER_RETRY_MINUTES = 60
_DEFAULT_RENDER_MAX_AGE_DAYS = 7
_DEFAULT_BATCH_SIZE = 500
_DEFAULT_SEND_RATE_PER_SEC = 2
_DEFAULT_MAX_SECONDS = 420
_DEFAULT_DAILY_LIMIT = 5000
_DEFAULT_OUTBOX_RETENTION_DAYS = 90

# Five failures with no success between them means SES or the network, not the
# recipients. Sending row six only lengthens the outage.
_CONSECUTIVE_FAILURE_LIMIT = 5

# Rows between daily-limit re-counts. Per row it would add a COUNT query to
# every send; only once would let a long run walk past the cap.
_DAILY_LIMIT_CHECK_EVERY = 50

# NotificationLog.channel for email. Slack alerts write "SLACK" rows, which
# must not count against the email cap.
_EMAIL_CHANNEL = "EMAIL"

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


# ------------------------------------------------------------------ run loop


@dataclass
class DrainSummary:
    claimed: int = 0
    sent: int = 0
    failed: int = 0
    suppressed: int = 0
    cancelled: int = 0
    render_blocked: int = 0
    pruned: int = 0
    stopped_reason: str | None = None


def _sent_in_last_24h(now) -> int:
    """Count email sends logged in the last 24 hours.

    Filtered to the EMAIL channel so the drainer's own Slack alerts do not eat
    the send budget.
    """
    return db.session.query(NotificationLog).filter(
        NotificationLog.channel == _EMAIL_CHANNEL,
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.created_at >= now - timedelta(hours=24),
    ).count()


def _alert(text: str, template_key: str) -> None:
    """Post one operator alert to Slack and commit its audit row.

    send_slack_message adds a NotificationLog row and leaves the commit to the
    caller (slack.py:88-110). Without this commit the alert's audit row
    disappears at the next rollback, including the drainer's own. A failed
    alert must not end the run; the queue matters more than the notice about
    it.
    """
    try:
        send_slack_message(text, template_key)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Drain alert could not be recorded: {e}")


def _release_claims(run_id: str) -> int:
    """Return this run's unprocessed claims to the queue. Commits.

    Re-queries by run id and SENDING rather than iterating the claimed list. A
    row process_row already terminated is no longer SENDING, and writing over
    it from the in-memory list would resurrect a sent email back into the
    queue. Attempt counts stay put: these rows never reached the transport.
    """
    rows = db.session.query(EmailOutbox).filter(
        EmailOutbox.claimed_by == run_id,
        EmailOutbox.status == OUTBOX_STATUS_SENDING,
    ).all()
    for row in rows:
        row.status = (OUTBOX_STATUS_RENDER_BLOCKED if row.blocked_since
                      else OUTBOX_STATUS_QUEUED)
        _clear_claim(row)
    db.session.commit()
    return len(rows)


def _prune_outbox(now) -> int:
    """Delete terminal outbox rows past the retention window. Commits.

    This is not the archive. NotificationLog and EmailMessageBody hold the
    long-term record; the outbox is a work queue, and a row still QUEUED at 90
    days is a stuck row an operator needs to see, not a row to delete.
    """
    cutoff = now - timedelta(days=_config("EMAIL_OUTBOX_RETENTION_DAYS",
                                          _DEFAULT_OUTBOX_RETENTION_DAYS))
    deleted = db.session.query(EmailOutbox).filter(
        EmailOutbox.status.in_(OUTBOX_TERMINAL_STATUSES),
        EmailOutbox.created_at < cutoff,
    ).delete(synchronize_session=False)
    db.session.commit()
    return deleted


@contextmanager
def _sigterm_watch():
    """Set a flag on SIGTERM so the loop stops between rows.

    Heroku sends SIGTERM about ten seconds before SIGKILL on a dyno restart.
    Finishing the row in flight and releasing claims beats being killed
    mid-send. signal.signal raises ValueError off the main thread; there the
    watch is a no-op and the run ends on the wall clock instead.
    """
    flag = {"stopped": False}

    def handler(signum, frame):
        flag["stopped"] = True

    previous = None
    installed = False
    try:
        previous = signal.signal(signal.SIGTERM, handler)
        installed = True
    except ValueError:
        pass
    try:
        yield flag
    finally:
        if installed:
            signal.signal(signal.SIGTERM, previous)


def drain_outbox(now=None) -> DrainSummary:
    """Send every due row in one batch, then prune. Commits throughout.

    The only code path that calls SES. Runs under Heroku Scheduler, so the run
    is bounded by EMAIL_DRAIN_MAX_SECONDS and ends before the next tick starts;
    whatever it did not reach is claimed by that tick.
    """
    now = now or datetime.utcnow()
    summary = DrainSummary()
    batch_size = _config("EMAIL_DRAIN_BATCH_SIZE", _DEFAULT_BATCH_SIZE)
    rate = max(1, _config("EMAIL_SEND_RATE_PER_SEC", _DEFAULT_SEND_RATE_PER_SEC))
    max_seconds = _config("EMAIL_DRAIN_MAX_SECONDS", _DEFAULT_MAX_SECONDS)
    daily_limit = _config("EMAIL_DAILY_LIMIT", _DEFAULT_DAILY_LIMIT)

    if batch_size / rate > max_seconds:
        current_app.logger.warning(
            f"EMAIL_DRAIN_BATCH_SIZE {batch_size} at {rate}/sec needs "
            f"{batch_size / rate:.0f}s, past the {max_seconds}s "
            f"EMAIL_DRAIN_MAX_SECONDS window. The run will stop on the clock "
            f"and release the rest of the batch."
        )

    reap_stale_claims(now=now)

    # uuid4, not a timestamp. Heroku Scheduler runs can overlap, and two runs
    # sharing a second-resolution id would each sweep up the other's claims.
    run_id = uuid4().hex
    rows = claim_due_rows(run_id, batch_size, now=now)
    summary.claimed = len(rows)

    started = time.monotonic()
    consecutive_failures = 0
    render_keys = set()

    try:
        with _sigterm_watch() as flag:
            if _sent_in_last_24h(now) >= daily_limit:
                summary.stopped_reason = (
                    f"EMAIL_DAILY_LIMIT of {daily_limit} sends in 24 hours reached"
                )
                rows = []

            for index, row in enumerate(rows):
                if flag["stopped"]:
                    summary.stopped_reason = "SIGTERM received"
                    break
                if time.monotonic() - started > max_seconds:
                    summary.stopped_reason = (
                        f"Run passed EMAIL_DRAIN_MAX_SECONDS ({max_seconds}s)"
                    )
                    break
                if index and index % _DAILY_LIMIT_CHECK_EVERY == 0:
                    if _sent_in_last_24h(datetime.utcnow()) >= daily_limit:
                        summary.stopped_reason = (
                            f"EMAIL_DAILY_LIMIT of {daily_limit} sends in 24 "
                            f"hours reached"
                        )
                        break

                try:
                    outcome = process_row(row, run_id)
                except AccountHaltError as e:
                    summary.stopped_reason = f"SES sending is paused account-wide: {e}"
                    _alert(
                        f"Email drain stopped: SES has paused sending for the "
                        f"whole account. {e}",
                        "email_account_halt",
                    )
                    break
                except ThrottleStopError as e:
                    summary.stopped_reason = f"SES throttled the account: {e}"
                    break

                if outcome == OUTBOX_STATUS_SENT:
                    summary.sent += 1
                elif outcome == OUTBOX_STATUS_SUPPRESSED:
                    summary.suppressed += 1
                elif outcome == OUTBOX_STATUS_CANCELLED:
                    summary.cancelled += 1
                elif outcome == OUTBOX_STATUS_RENDER_BLOCKED:
                    summary.render_blocked += 1
                    render_keys.add(row.template_key)
                elif outcome == OUTBOX_STATUS_FAILED:
                    summary.failed += 1
                    if row.blocked_since:
                        render_keys.add(row.template_key)

                # FAILED has two causes and only one is the transport. A render
                # block that aged past EMAIL_RENDER_MAX_AGE_DAYS still carries
                # blocked_since; process_row clears it the moment a template
                # renders, so a transport failure never does. Counting broken
                # templates here would stop the run on five bad templates while
                # SES was healthy.
                transport_failed = (
                    outcome in (OUTBOX_STATUS_QUEUED, OUTBOX_STATUS_FAILED)
                    and row.blocked_since is None
                )
                hit_transport = transport_failed or outcome == OUTBOX_STATUS_SENT

                if transport_failed:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= _CONSECUTIVE_FAILURE_LIMIT:
                    summary.stopped_reason = (
                        f"{_CONSECUTIVE_FAILURE_LIMIT} consecutive transport failures"
                    )
                    _alert(
                        f"Email drain stopped after "
                        f"{_CONSECUTIVE_FAILURE_LIMIT} consecutive transport "
                        f"failures. Last error: {row.last_error}",
                        "email_transport_failures",
                    )
                    break

                # Pace only the rows that reached SES. The rate limit exists to
                # protect the SES quota, and a cancelled or render-blocked row
                # spends none of it. Sleeping for those lets one broken template
                # fill a batch and burn the whole window on zero sends, starving
                # the email that would otherwise have gone out.
                if hit_transport and index < len(rows) - 1:
                    time.sleep(1.0 / rate)

        if render_keys:
            _alert(
                f"Email templates failed to render: "
                f"{', '.join(sorted(render_keys))}. "
                f"{summary.render_blocked} row(s) blocked; each retries in "
                f"{_config('EMAIL_RENDER_RETRY_MINUTES', _DEFAULT_RENDER_RETRY_MINUTES)} "
                f"minutes and fails for good after "
                f"{_config('EMAIL_RENDER_MAX_AGE_DAYS', _DEFAULT_RENDER_MAX_AGE_DAYS)} days.",
                "email_render_blocked",
            )
    finally:
        # Roll back first: if the run died with the session in a failed state,
        # the release query below would raise over the original exception.
        db.session.rollback()
        _release_claims(run_id)

    summary.pruned = _prune_outbox(now)
    return summary
