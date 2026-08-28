"""Run-loop behaviour of the drain command: claim, stop, release, prune."""
import logging
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app import db
from app.models import (
    Department,
    EmailMessageBody,
    EmailOutbox,
    EmailTemplate,
    EventCycle,
    NotificationLog,
)
from app.models.constants import (
    NOTIF_STATUS_SENT,
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_SENDING,
    OUTBOX_STATUS_SENT,
)
from app.services.email import EmailSendResult
from app.services.email_drainer import drain_outbox

_seq = [0]


def _next_seq():
    _seq[0] += 1
    return _seq[0]


def _setup(app, body="Hello <b>there</b>"):
    """Seed the one template and the two entities a reminder row renders from.

    Reminder rows are the simplest renderable kind: no work item, so no
    portfolio or event fixture is needed.
    """
    app.config["EMAIL_ENABLED"] = True
    # The loop paces itself at 1/rate seconds per row. Two per second would put
    # real seconds on the clock of the six-row test for no added coverage.
    app.config["EMAIL_SEND_RATE_PER_SEC"] = 200
    db.session.add(EmailTemplate(
        template_key="submission_reminder", name="Reminder",
        subject="Subj", body_text=body,
    ))
    dept = Department(code=f"D{_next_seq()}", name="Test Department")
    cycle = EventCycle(code=f"C{_next_seq()}", name="Test Event 2026")
    db.session.add_all([dept, cycle])
    db.session.commit()
    return dept, cycle


def _queued(dept, cycle, **kw):
    base = dict(
        template_key="submission_reminder",
        recipient_email="a@example.org",
        dispatch_at=datetime.utcnow() - timedelta(minutes=1),
        status=OUTBOX_STATUS_QUEUED,
        department_id=dept.id,
        event_cycle_id=cycle.id,
        created_at=datetime.utcnow(),
    )
    base.update(kw)
    row = EmailOutbox(**base)
    db.session.add(row)
    db.session.commit()
    return row


def _sent():
    return EmailSendResult(status="SENT", provider_message_id="mid-1")


def _failed(code):
    return EmailSendResult(status="FAILED", error=f"boom {code}", error_code=code)


def _sending_count():
    return db.session.query(EmailOutbox).filter(
        EmailOutbox.status == OUTBOX_STATUS_SENDING
    ).count()


def test_end_to_end_sends_logs_and_archives(app):
    with app.app_context():
        dept, cycle = _setup(app)
        row = _queued(dept, cycle)
        with patch("app.services.email_drainer.send_via_ses", return_value=_sent()):
            summary = drain_outbox()

        assert summary.claimed == 1
        assert summary.sent == 1
        assert row.status == OUTBOX_STATUS_SENT
        assert row.provider_message_id == "mid-1"
        assert db.session.query(NotificationLog).count() == 1
        body = db.session.query(EmailMessageBody).one()
        assert "<b>there</b>" in body.body_html


def test_startup_warns_when_batch_cannot_finish_in_the_window(app, caplog):
    """500 rows at 2/sec is 250s against a 420s budget. Raising the batch
    without redoing that arithmetic is the foreseeable operator error."""
    with app.app_context():
        app.config["EMAIL_DRAIN_BATCH_SIZE"] = 5000
        app.config["EMAIL_SEND_RATE_PER_SEC"] = 2
        app.config["EMAIL_DRAIN_MAX_SECONDS"] = 420
        with caplog.at_level(logging.WARNING):
            drain_outbox()
        warnings = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.WARNING]
        assert any("EMAIL_DRAIN_BATCH_SIZE" in m for m in warnings), warnings


def test_account_halt_stops_the_run_and_releases_claims(app):
    with app.app_context():
        dept, cycle = _setup(app)
        _queued(dept, cycle)
        _queued(dept, cycle)
        with patch("app.services.email_drainer.send_via_ses",
                   return_value=_failed("AccountSendingPaused")) as ses:
            summary = drain_outbox()

        assert ses.call_count == 1
        assert summary.stopped_reason is not None
        assert "paused" in summary.stopped_reason.lower()
        assert _sending_count() == 0


def test_five_consecutive_transport_failures_stop_the_run(app):
    with app.app_context():
        dept, cycle = _setup(app)
        for _ in range(6):
            _queued(dept, cycle)
        with patch("app.services.email_drainer.send_via_ses",
                   return_value=_failed("ServiceUnavailable")) as ses:
            summary = drain_outbox()

        assert summary.claimed == 6
        assert ses.call_count == 5
        assert summary.stopped_reason is not None
        assert _sending_count() == 0


def test_a_crash_mid_batch_still_releases_claims(app):
    """A crash must not strand a whole batch in SENDING until the reaper."""
    with app.app_context():
        dept, cycle = _setup(app)
        _queued(dept, cycle)
        _queued(dept, cycle)
        with patch("app.services.email_drainer.process_row",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                drain_outbox()
        assert _sending_count() == 0
        assert db.session.query(EmailOutbox).filter(
            EmailOutbox.status == OUTBOX_STATUS_QUEUED
        ).count() == 2


def test_prune_deletes_only_old_terminal_rows(app):
    with app.app_context():
        dept, cycle = _setup(app)
        old = datetime.utcnow() - timedelta(days=200)
        sent_row = _queued(dept, cycle, status=OUTBOX_STATUS_SENT,
                           created_at=old, dispatch_at=old, sent_at=old)
        # dispatch_at is in the future so this row is not claimable. The
        # assertion is about the prune's status filter, not about what the run
        # loop did to the row on its way past.
        queued_row = _queued(dept, cycle, created_at=old,
                             dispatch_at=datetime.utcnow() + timedelta(days=1))
        sent_id, queued_id = sent_row.id, queued_row.id

        with patch("app.services.email_drainer.send_via_ses", return_value=_sent()):
            summary = drain_outbox()

        assert summary.pruned == 1
        assert db.session.get(EmailOutbox, sent_id) is None
        assert db.session.get(EmailOutbox, queued_id) is not None


def test_broken_templates_do_not_stop_the_run(app):
    """A bad template must not look like an SES outage.

    FAILED and QUEUED are written by both paths, so the counter keys on
    blocked_since instead. Without that discriminator six broken templates trip
    the five-failure stop, all email halts, and the Slack alert blames SES for
    a Jinja problem.
    """
    with app.app_context():
        dept, cycle = _setup(app, body="{{ 1 / 0 }}")
        for _ in range(6):
            _queued(dept, cycle)
        with patch("app.services.email_drainer.send_via_ses") as ses:
            summary = drain_outbox()
        assert ses.call_count == 0
        assert summary.render_blocked == 6
        assert summary.stopped_reason is None


def test_an_aged_render_block_is_not_a_transport_failure(app):
    """The subtle half: a render block past its max age terminates as FAILED
    while still carrying blocked_since, so it must not be counted either."""
    with app.app_context():
        dept, cycle = _setup(app, body="{{ 1 / 0 }}")
        stale = datetime.utcnow() - timedelta(days=8)
        for _ in range(6):
            _queued(dept, cycle, blocked_since=stale)
        with patch("app.services.email_drainer.send_via_ses") as ses:
            summary = drain_outbox()
        assert ses.call_count == 0
        assert summary.failed == 6
        assert summary.stopped_reason is None


def test_a_success_resets_the_consecutive_failure_counter(app):
    """Four failures, a success, four more failures is a flaky endpoint, not an
    outage. Without the reset the run stops on the ninth row."""
    with app.app_context():
        dept, cycle = _setup(app)
        for _ in range(9):
            _queued(dept, cycle)
        results = ([_failed("ServiceUnavailable")] * 4 + [_sent()]
                   + [_failed("ServiceUnavailable")] * 4)
        with patch("app.services.email_drainer.send_via_ses", side_effect=results):
            summary = drain_outbox()
        assert summary.sent == 1
        assert summary.stopped_reason is None


def test_blocked_rows_do_not_spend_the_send_rate(app):
    """The rate limit protects the SES quota, and a blocked row spends none of
    it. At the real 2/sec, a batch of 500 broken rows would otherwise sleep out
    the whole 420s window while sending nothing."""
    with app.app_context():
        dept, cycle = _setup(app, body="{{ 1 / 0 }}")
        app.config["EMAIL_SEND_RATE_PER_SEC"] = 2
        for _ in range(4):
            _queued(dept, cycle)
        started = time.monotonic()
        with patch("app.services.email_drainer.send_via_ses"):
            summary = drain_outbox()
        assert summary.render_blocked == 4
        assert time.monotonic() - started < 0.5


def test_the_render_alert_reads_as_a_sentence(app):
    """These strings go to Slack and are read during an incident. The counts
    are configurable, so the singular case is reached in normal operation."""
    with app.app_context():
        app.config["EMAIL_RENDER_RETRY_MINUTES"] = 1
        app.config["EMAIL_RENDER_MAX_AGE_DAYS"] = 1
        dept, cycle = _setup(app, body="{{ 1 / 0 }}")
        _queued(dept, cycle)
        with patch("app.services.email_drainer.send_slack_message") as slack:
            drain_outbox()
        text = slack.call_args[0][0]
        assert "1 row blocked" in text
        assert "retries in 1 minute and" in text
        assert "after 1 day." in text
        assert "(s)" not in text


def test_the_render_alert_pluralises_above_one(app):
    with app.app_context():
        app.config["EMAIL_RENDER_RETRY_MINUTES"] = 60
        app.config["EMAIL_RENDER_MAX_AGE_DAYS"] = 7
        dept, cycle = _setup(app, body="{{ 1 / 0 }}")
        for _ in range(2):
            _queued(dept, cycle)
        with patch("app.services.email_drainer.send_slack_message") as slack:
            drain_outbox()
        text = slack.call_args[0][0]
        assert "2 rows blocked" in text
        assert "retries in 60 minutes and" in text
        assert "after 7 days." in text


def _logged_sends(count):
    """Seed the NotificationLog rows that _sent_in_last_24h counts."""
    for _ in range(count):
        db.session.add(NotificationLog(
            channel="EMAIL",
            template_key="submission_reminder",
            recipient_email="a@example.org",
            status=NOTIF_STATUS_SENT,
            created_at=datetime.utcnow(),
        ))
    db.session.commit()


def test_an_over_limit_run_claims_nothing(app):
    """Over the daily limit the run must skip the claim, not undo it.

    summary.claimed is the discriminator. Claiming first and emptying the list
    afterwards reports the batch as claimed and writes SENDING across every row
    in it, ten minutes apart, forever.
    """
    with app.app_context():
        dept, cycle = _setup(app)
        app.config["EMAIL_DAILY_LIMIT"] = 2
        _logged_sends(2)
        row_id = _queued(dept, cycle).id

        with patch("app.services.email_drainer.send_via_ses") as ses:
            summary = drain_outbox()

        assert ses.call_count == 0
        assert summary.claimed == 0
        assert "EMAIL_DAILY_LIMIT" in summary.stopped_reason
        assert db.session.get(EmailOutbox, row_id).status == OUTBOX_STATUS_QUEUED


def test_the_prune_still_runs_over_the_daily_limit(app):
    """Skipping the claim must not skip the prune.

    _prune_outbox runs after the try/finally, so returning early on the limit
    would stop outbox retention for as long as the limit held, with nothing
    reporting it.
    """
    with app.app_context():
        dept, cycle = _setup(app)
        app.config["EMAIL_DAILY_LIMIT"] = 1
        _logged_sends(1)
        old = datetime.utcnow() - timedelta(days=200)
        stale_id = _queued(dept, cycle, status=OUTBOX_STATUS_SENT,
                           created_at=old, dispatch_at=old, sent_at=old).id

        with patch("app.services.email_drainer.send_via_ses") as ses:
            summary = drain_outbox()

        assert ses.call_count == 0
        assert summary.pruned == 1
        assert db.session.get(EmailOutbox, stale_id) is None


def test_a_run_under_the_daily_limit_still_claims(app):
    """The gate must not be always-on; a normal run is unaffected."""
    with app.app_context():
        dept, cycle = _setup(app)
        app.config["EMAIL_DAILY_LIMIT"] = 5
        _logged_sends(2)
        _queued(dept, cycle)

        with patch("app.services.email_drainer.send_via_ses", return_value=_sent()):
            summary = drain_outbox()

        assert summary.claimed == 1
        assert summary.sent == 1
        assert summary.stopped_reason is None
