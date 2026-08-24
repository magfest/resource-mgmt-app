"""Per-row outcomes, audit rows, and stored bodies."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app import db
from app.models import (
    Department,
    EmailMessageBody,
    EmailOutbox,
    EmailSuppression,
    EmailTemplate,
    EventCycle,
    NotificationLog,
    WorkItem,
)
from app.models.constants import (
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_SUBMITTED,
    OUTBOX_STATUS_CANCELLED,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_RENDER_BLOCKED,
    OUTBOX_STATUS_SENDING,
    OUTBOX_STATUS_SENT,
    OUTBOX_STATUS_SUPPRESSED,
)
from app.services.email import EmailSendResult
from app.services.email_drainer import process_row
from app.services.email_errors import AccountHaltError, ThrottleStopError


def _template(key="submitted", body="Hello {{ recipient_email }}"):
    t = EmailTemplate(template_key=key, name=key, subject="Subj", body_text=body)
    db.session.add(t)
    db.session.commit()
    return t


def _row(**kw):
    base = dict(template_key="submitted", recipient_email="a@example.org",
                dispatch_at=datetime.utcnow(), status=OUTBOX_STATUS_SENDING,
                created_at=datetime.utcnow())
    base.update(kw)
    row = EmailOutbox(**base)
    db.session.add(row)
    db.session.commit()
    return row


def _reminder_row(**kw):
    """A reminder row that can actually render.

    Reminders carry no work item and render from department plus event cycle,
    so a reminder missing either one cancels. Tests that want the cancel path
    build the row with _row and pass the ids as None explicitly.
    """
    dept = Department(code=f"D{_next_seq()}", name="Test Department")
    cycle = EventCycle(code=f"C{_next_seq()}", name="Test Event 2026")
    db.session.add_all([dept, cycle])
    db.session.commit()
    base = dict(template_key="submission_reminder", work_item_id=None,
                department_id=dept.id, event_cycle_id=cycle.id)
    base.update(kw)
    return _row(**base)


_seq = [0]


def _next_seq():
    _seq[0] += 1
    return _seq[0]


def _sent():
    return EmailSendResult(status="SENT", provider_message_id="mid-1")


def _failed(code):
    return EmailSendResult(status="FAILED", error=f"boom {code}", error_code=code)


def test_missing_template_cancels_and_still_writes_an_audit_row(app):
    """A cancelled email must leave a permanent trace. The outbox row is pruned
    at 90 days; NotificationLog is the four-year record."""
    with app.app_context():
        row = _row(template_key="nope")
        assert process_row(row, "run-1") == OUTBOX_STATUS_CANCELLED
        assert db.session.query(NotificationLog).count() == 1


def test_inactive_template_cancels(app):
    with app.app_context():
        t = _template()
        t.is_active = False
        db.session.commit()
        row = _row()
        assert process_row(row, "run-1b") == OUTBOX_STATUS_CANCELLED


def test_deleted_work_item_cancels_and_names_the_reason(app):
    """work_item_id is ON DELETE SET NULL, so a deleted request leaves the row
    looking like one that never had a work item."""
    with app.app_context():
        _template()
        row = _row(work_item_id=None, department_id=None)
        assert process_row(row, "run-1c") == OUTBOX_STATUS_CANCELLED
        log = db.session.query(NotificationLog).one()
        assert "work item" in log.error_message


def test_suppressed_recipient_stores_a_body_and_makes_no_ses_call(app):
    with app.app_context():
        _template(key="submission_reminder", body="Hi")
        db.session.add(EmailSuppression(email="a@example.org"))
        db.session.commit()
        row = _reminder_row()
        with patch("app.services.email_drainer.send_via_ses") as ses:
            assert process_row(row, "run-2") == OUTBOX_STATUS_SUPPRESSED
        assert ses.call_count == 0
        assert db.session.query(EmailMessageBody).count() == 1


def test_render_exception_blocks_the_row_and_does_not_kill_the_run(app):
    """render_email_template catches only Jinja errors. A TypeError from a
    filter escapes it, and uncaught it would stall the whole queue forever."""
    with app.app_context():
        _template(key="submission_reminder", body="{{ 1 / 0 }}")
        row = _reminder_row()
        assert process_row(row, "run-x") == OUTBOX_STATUS_RENDER_BLOCKED
        assert row.blocked_since is not None


def test_corrupt_context_json_blocks_instead_of_raising(app):
    with app.app_context():
        _template(key="submission_reminder", body="Hi")
        row = _reminder_row(context_json="{not json")
        assert process_row(row, "run-x2") == OUTBOX_STATUS_RENDER_BLOCKED


def test_reminder_row_renders_from_department_and_event_cycle(app, seed_workflow_data):
    """Reminder rows carry no work_item. Loading only work_item would make
    every reminder fail to render and blame a template that is fine."""
    with app.app_context():
        app.config["EMAIL_ENABLED"] = True
        _template(key="submission_reminder",
                  body="Hi {{ department.name }} for {{ event_cycle.name }}")
        dept = seed_workflow_data["department"]
        cycle = seed_workflow_data["cycle"]
        row = _row(template_key="submission_reminder", work_item_id=None,
                   department_id=dept.id, event_cycle_id=cycle.id)
        with patch("app.services.email_drainer.send_via_ses") as ses:
            ses.return_value = _sent()
            assert process_row(row, "run-y") == OUTBOX_STATUS_SENT
        body = db.session.query(EmailMessageBody).one()
        assert "Test Department" in body.body_text
        assert "Test Event 2026" in body.body_text


def test_reminder_with_deleted_department_cancels(app):
    with app.app_context():
        _template(key="submission_reminder", body="Hi")
        row = _row(template_key="submission_reminder", work_item_id=None,
                   department_id=None, event_cycle_id=None)
        assert process_row(row, "run-y2") == OUTBOX_STATUS_CANCELLED
        log = db.session.query(NotificationLog).one()
        assert "department" in log.error_message


def test_repeated_render_failure_writes_only_one_audit_row(app):
    """Logging every retry would bury the audit trail under one broken template."""
    with app.app_context():
        _template(key="submission_reminder", body="{{ undefined_thing.explode() }}")
        row = _reminder_row()
        for _ in range(3):
            row.status = OUTBOX_STATUS_SENDING
            assert process_row(row, "run-3") == OUTBOX_STATUS_RENDER_BLOCKED
        assert row.blocked_since is not None
        assert row.attempt_count == 0
        assert db.session.query(NotificationLog).count() == 1


def test_render_block_older_than_the_max_age_fails_the_row(app):
    with app.app_context():
        _template(key="submission_reminder", body="{{ 1 / 0 }}")
        row = _reminder_row(dedup_key="k-old")
        row.blocked_since = datetime.utcnow() - timedelta(days=8)
        db.session.commit()
        assert process_row(row, "run-4") == OUTBOX_STATUS_FAILED
        assert row.dedup_key is None


def test_a_repaired_template_clears_the_block_marker(app):
    with app.app_context():
        app.config["EMAIL_ENABLED"] = True
        t = _template(key="submission_reminder", body="{{ 1 / 0 }}")
        row = _reminder_row()
        assert process_row(row, "run-5") == OUTBOX_STATUS_RENDER_BLOCKED
        t.body_text = "Fixed"
        row.status = OUTBOX_STATUS_SENDING
        db.session.commit()
        with patch("app.services.email_drainer.send_via_ses", return_value=_sent()):
            assert process_row(row, "run-5") == OUTBOX_STATUS_SENT
        assert row.blocked_since is None


# ---------------------------------------------------------------- transport


def _transport_row(app):
    app.config["EMAIL_ENABLED"] = True
    _template(key="submission_reminder", body="Hi")
    return _reminder_row(dedup_key="k-1")


def test_the_ladder_fails_on_the_seventh_attempt_not_the_eighth(app):
    """EMAIL_MAX_ATTEMPTS counts total attempts including the first. Testing the
    count before incrementing gives eight attempts and doubles the 21-hour
    retry window."""
    with app.app_context():
        app.config["EMAIL_MAX_ATTEMPTS"] = 7
        row = _transport_row(app)
        with patch("app.services.email_drainer.send_via_ses",
                   return_value=_failed("ServiceUnavailable")):
            for expected_attempt in range(1, 7):
                row.status = OUTBOX_STATUS_SENDING
                assert process_row(row, "run-6") == OUTBOX_STATUS_QUEUED
                assert row.attempt_count == expected_attempt
            row.status = OUTBOX_STATUS_SENDING
            assert process_row(row, "run-6") == OUTBOX_STATUS_FAILED
        assert row.attempt_count == 7


def test_transient_failure_backs_off_from_twenty_minutes(app):
    with app.app_context():
        row = _transport_row(app)
        before = datetime.utcnow()
        with patch("app.services.email_drainer.send_via_ses",
                   return_value=_failed("ServiceUnavailable")):
            assert process_row(row, "run-7") == OUTBOX_STATUS_QUEUED
        assert row.dispatch_at - before >= timedelta(minutes=19)
        assert row.dispatch_at - before < timedelta(minutes=21)


def test_permanent_failure_terminates_without_burning_the_ladder(app):
    with app.app_context():
        row = _transport_row(app)
        with patch("app.services.email_drainer.send_via_ses",
                   return_value=_failed("MessageRejected")):
            assert process_row(row, "run-8") == OUTBOX_STATUS_FAILED
        assert row.attempt_count == 0
        # Body is stored even for a rejected message; the operator needs to see
        # what SES refused.
        assert db.session.query(EmailMessageBody).count() == 1


def test_account_halt_requeues_without_an_attempt_and_stops_the_run(app):
    with app.app_context():
        row = _transport_row(app)
        with patch("app.services.email_drainer.send_via_ses",
                   return_value=_failed("AccountSendingPaused")):
            with pytest.raises(AccountHaltError):
                process_row(row, "run-9")
        assert row.status == OUTBOX_STATUS_QUEUED
        assert row.attempt_count == 0


def test_throttling_requeues_with_backoff_and_stops_the_run(app):
    with app.app_context():
        row = _transport_row(app)
        with patch("app.services.email_drainer.send_via_ses",
                   return_value=_failed("Throttling")):
            with pytest.raises(ThrottleStopError):
                process_row(row, "run-10")
        assert row.status == OUTBOX_STATUS_QUEUED
        assert row.attempt_count == 1


# ---------------------------------------------------------------- dedup key


def test_failed_and_cancelled_release_the_dedup_key(app):
    """A terminal row otherwise owns its key until the 90-day prune, so the
    documented re-enqueue recovery returns DUPLICATE and does nothing."""
    with app.app_context():
        row = _transport_row(app)
        with patch("app.services.email_drainer.send_via_ses",
                   return_value=_failed("MessageRejected")):
            assert process_row(row, "run-11") == OUTBOX_STATUS_FAILED
        assert row.dedup_key is None

        cancelled = _row(template_key="nope", dedup_key="k-2")
        assert process_row(cancelled, "run-11") == OUTBOX_STATUS_CANCELLED
        assert cancelled.dedup_key is None


def test_sent_and_suppressed_keep_the_dedup_key(app):
    with app.app_context():
        row = _transport_row(app)
        with patch("app.services.email_drainer.send_via_ses", return_value=_sent()):
            assert process_row(row, "run-12") == OUTBOX_STATUS_SENT
        assert row.dedup_key == "k-1"
        assert row.provider_message_id == "mid-1"
        assert row.sent_at is not None

        app.config["EMAIL_ENABLED"] = False
        other = _reminder_row(dedup_key="k-3")
        assert process_row(other, "run-12") == OUTBOX_STATUS_SUPPRESSED
        assert other.dedup_key == "k-3"


def test_email_disabled_still_archives_the_rendered_body(app):
    """EMAIL_ENABLED false on a dev box fills the archive rather than writing
    empty SUPPRESSED rows. The admin body viewer is the local inbox."""
    with app.app_context():
        app.config["EMAIL_ENABLED"] = False
        _template(key="submission_reminder", body="Hello from the drainer")
        row = _reminder_row()
        with patch("app.services.email_drainer.send_via_ses") as ses:
            assert process_row(row, "run-13") == OUTBOX_STATUS_SUPPRESSED
        assert ses.call_count == 0
        body = db.session.query(EmailMessageBody).one()
        assert "Hello from the drainer" in body.body_text


def test_stored_context_reaches_the_template(app, seed_workflow_data):
    """context_json carries the values only the enqueuing code can compute.

    submission_confirmation renders line_count and total_requested_dollars,
    which the drainer cannot recompute; a work item edited after enqueue would
    give a different answer than the email promised. Losing the merge would not
    raise, because Jinja renders an undefined name as an empty string.
    """
    with app.app_context():
        app.config["EMAIL_ENABLED"] = True
        _template(key="submission_confirmation",
                  body="{{ line_count }} lines, ${{ total_requested_dollars }}")
        row = _row(template_key="submission_confirmation",
                   work_item_id=None, department_id=None,
                   context_json='{"line_count": 4, "total_requested_dollars": 250.5}')
        item = WorkItem(
            portfolio_id=seed_workflow_data["portfolio"].id,
            request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED,
            public_id="TST2026-TESTDEPT-BUD-9",
            created_by_user_id="test:admin",
        )
        db.session.add(item)
        db.session.flush()
        row.work_item_id = item.id
        db.session.commit()
        with patch("app.services.email_drainer.send_via_ses", return_value=_sent()):
            assert process_row(row, "run-14") == OUTBOX_STATUS_SENT
        body = db.session.query(EmailMessageBody).one()
        assert "4 lines, $250.5" in body.body_text
