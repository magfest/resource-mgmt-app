"""Send-window enforcement at enqueue time."""
import logging
from datetime import datetime, timedelta

from app import db
from app.models import (
    EmailOutbox, EmailTemplate, EmailTemplateEventOverride, EventCycle,
)
from app.models.constants import (
    ENQUEUE_OUTCOME_BLOCKED_INACTIVE,
    ENQUEUE_OUTCOME_BLOCKED_WINDOW,
    ENQUEUE_OUTCOME_CREATED,
    ENQUEUE_OUTCOME_DEFERRED,
)
from app.services.email_enqueue import enqueue_email


def _seed(**override_kwargs):
    # Not "finalized": seed_workflow_data seeds that key and template_key is
    # unique. Every other notification kind is seeded per test.
    t = EmailTemplate(template_key="dispatched", name="Dispatched",
                      subject="S", body_text="B", is_active=True, version=1)
    db.session.add(t)
    db.session.flush()
    cycle = db.session.query(EventCycle).first()
    if override_kwargs:
        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id, **override_kwargs))
    db.session.commit()
    return t, cycle


def test_inside_the_window_creates_a_row(app, seed_workflow_data):
    with app.app_context():
        now = datetime.utcnow()
        t, cycle = _seed(send_window_start=now - timedelta(days=1),
                         send_window_end=now + timedelta(days=1))
        outcome = enqueue_email("dispatched", "a@example.org", event_cycle=cycle)
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_CREATED
        assert db.session.query(EmailOutbox).count() == 1


def test_before_the_window_defers_to_the_start(app, seed_workflow_data):
    with app.app_context():
        start = datetime.utcnow() + timedelta(days=30)
        t, cycle = _seed(send_window_start=start)
        outcome = enqueue_email("dispatched", "a@example.org", event_cycle=cycle)
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_DEFERRED
        row = db.session.query(EmailOutbox).one()
        assert row.dispatch_at == start


def test_after_the_window_writes_no_row(app, seed_workflow_data):
    with app.app_context():
        t, cycle = _seed(send_window_end=datetime.utcnow() - timedelta(days=1))
        outcome = enqueue_email("dispatched", "a@example.org", event_cycle=cycle)
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_BLOCKED_WINDOW
        assert db.session.query(EmailOutbox).count() == 0


def test_effective_inactive_writes_no_row(app, seed_workflow_data):
    with app.app_context():
        t, cycle = _seed(is_active=False)
        outcome = enqueue_email("dispatched", "a@example.org", event_cycle=cycle)
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_BLOCKED_INACTIVE
        assert db.session.query(EmailOutbox).count() == 0


def test_missing_base_template_writes_no_row(app, seed_workflow_data):
    with app.app_context():
        cycle = db.session.query(EventCycle).first()
        outcome = enqueue_email("no_such_key", "a@example.org", event_cycle=cycle)
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_BLOCKED_INACTIVE
        assert db.session.query(EmailOutbox).count() == 0


def test_no_event_cycle_means_no_window(app, seed_workflow_data):
    """A row with no event cycle cannot resolve an override, and sends now."""
    with app.app_context():
        _seed(send_window_end=datetime.utcnow() - timedelta(days=1))
        outcome = enqueue_email("dispatched", "a@example.org")
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_CREATED


def test_a_window_start_never_pulls_a_later_date_forward(app, seed_workflow_data):
    """The window start is a floor, not a replacement.

    The outcome is CREATED, not DEFERRED: the window moved nothing. DEFERRED
    records that the window held an email back, so a caller's own later date
    must not report it; a future-dated row with no window at all returns
    CREATED for the same reason.
    """
    with app.app_context():
        start = datetime.utcnow() + timedelta(days=10)
        later = datetime.utcnow() + timedelta(days=20)
        t, cycle = _seed(send_window_start=start)
        outcome = enqueue_email("dispatched", "a@example.org",
                                event_cycle=cycle, dispatch_at=later)
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_CREATED
        assert db.session.query(EmailOutbox).one().dispatch_at == later


def test_enqueue_emails_counts_deferred_as_queued(app, seed_draft_work_item):
    """A deferred row exists and will send. Counting it as not-queued would
    make the finalize flash under-report what the department will receive."""
    from app.services.notifications import _enqueue_emails

    with app.app_context():
        start = datetime.utcnow() + timedelta(days=30)
        t, cycle = _seed(send_window_start=start)
        item = seed_draft_work_item["work_item"]
        # The override is keyed to this cycle. A work item on another cycle
        # would resolve no override and pass whatever the counting rule was.
        assert item.portfolio.event_cycle_id == cycle.id

        queued = _enqueue_emails(
            ["a@example.org"], "dispatched", item, "no recipients")

        assert queued == 1
        assert db.session.query(EmailOutbox).one().dispatch_at == start


def test_enqueue_emails_reports_blocked_separately(app, seed_draft_work_item, caplog):
    """Blocked rows get their own log line.

    Asserting on the summary wording, not on "blocked" alone: enqueue_email
    already logs a per-recipient Blocked warning, so a looser assertion would
    pass without the count ever being reported.
    """
    from app.services.notifications import _enqueue_emails

    with app.app_context():
        t, cycle = _seed(is_active=False)
        item = seed_draft_work_item["work_item"]

        with caplog.at_level(logging.WARNING, logger="app.services.notifications"):
            queued = _enqueue_emails(
                ["a@example.org"], "dispatched", item, "no recipients")

        assert queued == 0
        assert db.session.query(EmailOutbox).count() == 0
        assert "silenced or outside its window" in caplog.text
        assert "1/1" in caplog.text
