"""Send-time resolution: one template key, per-event wording and windows."""
from datetime import datetime, timedelta

from app import db
from app.models import EmailTemplate, EmailTemplateEventOverride, EventCycle
from app.services.email_templates import render_email_template


def _base(key="dispatched", body="Base wording"):
    t = EmailTemplate(template_key=key, name="F", subject="S",
                      body_text=body, is_active=True, version=1)
    db.session.add(t)
    db.session.flush()
    return t


def test_two_events_render_different_bodies_from_one_key(app, seed_workflow_data):
    with app.app_context():
        t = _base()
        cycles = db.session.query(EventCycle).limit(2).all()
        assert len(cycles) == 2, "seed two event cycles for this test"
        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycles[0].id,
            body_text="Custom wording"))
        db.session.commit()

        a = render_email_template("dispatched", {}, event_cycle_id=cycles[0].id)
        b = render_email_template("dispatched", {}, event_cycle_id=cycles[1].id)

        assert a.body_text == "Custom wording"
        assert b.body_text == "Base wording"


def test_no_event_cycle_renders_the_base(app, seed_workflow_data):
    """A row with no event cycle cannot resolve an override."""
    with app.app_context():
        t = _base()
        cycle = db.session.query(EventCycle).first()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id,
            body_text="Custom wording"))
        db.session.commit()

        assert render_email_template("dispatched", {}).body_text == "Base wording"


def test_an_override_silencing_the_template_renders_nothing(app, seed_workflow_data):
    """is_active is an AND. A silenced event renders None, not the base."""
    with app.app_context():
        t = _base()
        cycle = db.session.query(EventCycle).first()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id, is_active=False))
        db.session.commit()

        assert render_email_template(
            "dispatched", {}, event_cycle_id=cycle.id) is None
        assert render_email_template("dispatched", {}) is not None


def test_a_deferred_row_whose_window_closed_is_cancelled(app, seed_draft_work_item):
    """The window was narrowed while the row waited. It must not send.

    The row carries a work_item_id because _missing_required_entities rejects
    a non-reminder key without one, which would cancel the row before the
    window check ever ran and make this test pass for the wrong reason.
    """
    from app.models import EmailOutbox
    from app.models.constants import OUTBOX_STATUS_CANCELLED, OUTBOX_STATUS_QUEUED
    from app.services.email_drainer import process_row

    with app.app_context():
        data = seed_draft_work_item
        cycle = data["cycle"]
        template = db.session.query(EmailTemplate).filter_by(
            template_key="finalized").one()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=template.id, event_cycle_id=cycle.id,
            send_window_end=datetime.utcnow() - timedelta(days=1)))
        row = EmailOutbox(
            template_key="finalized", recipient_email="a@example.org",
            work_item_id=data["work_item"].id,
            event_cycle_id=cycle.id, status=OUTBOX_STATUS_QUEUED,
            dispatch_at=datetime.utcnow(), created_at=datetime.utcnow(),
            attempt_count=0)
        db.session.add(row)
        db.session.commit()

        status = process_row(row, run_id="test")

        assert status == OUTBOX_STATUS_CANCELLED
        assert "window" in (row.last_error or "").lower(), (
            f"cancelled for the wrong reason: {row.last_error}"
        )


def test_an_open_window_gets_past_the_send_time_check(app, seed_draft_work_item):
    """The send-time check must not cancel a row inside its window.

    The row stops at SUPPRESSED because is_email_enabled() is false under
    test config, which is two steps past the window check and proves the row
    resolved, rendered, and was never cancelled.
    """
    from app.models import EmailOutbox
    from app.models.constants import (
        OUTBOX_STATUS_QUEUED, OUTBOX_STATUS_SUPPRESSED,
    )
    from app.services import email_drainer

    with app.app_context():
        data = seed_draft_work_item
        cycle = data["cycle"]
        template = db.session.query(EmailTemplate).filter_by(
            template_key="finalized").one()
        now = datetime.utcnow()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=template.id, event_cycle_id=cycle.id,
            send_window_start=now - timedelta(days=1),
            send_window_end=now + timedelta(days=1)))
        row = EmailOutbox(
            template_key="finalized", recipient_email="a@example.org",
            work_item_id=data["work_item"].id,
            event_cycle_id=cycle.id, status=OUTBOX_STATUS_QUEUED,
            dispatch_at=now, created_at=now, attempt_count=0)
        db.session.add(row)
        db.session.commit()

        status = email_drainer.process_row(row, run_id="test")

        assert status == OUTBOX_STATUS_SUPPRESSED, (
            f"expected the suppression stop, got {status}: {row.last_error}"
        )
        assert "window" not in (row.last_error or "").lower()
