"""The rename must move queue rows with it and leave history alone.

A queued outbox row resolves its template by key at send time
(email_drainer.py:383). A row left pointing at a key that no longer exists is
CANCELLED, which is terminal: the email is dropped once, silently, with no
retry and no alert.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app import db
from app.models import (
    EmailOutbox,
    EmailTemplate,
    NotificationLog,
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_SENT,
)
from app.services.email_templates import get_template

RENAMES = {
    "submitted": "budget_submitted",
    "dispatched": "budget_dispatched",
    "needs_attention": "budget_needs_attention",
    "response_received": "budget_response_received",
    "submission_confirmation": "budget_submission_confirmation",
    "finalized": "budget_finalized",
    "submission_reminder": "budget_submission_reminder",
}


@pytest.fixture
def pre_rename_rows(app):
    """The seven templates under their old keys, plus one outbox row in each
    of a terminal and a non-terminal state, and one notification log row."""
    for old_key in RENAMES:
        db.session.add(EmailTemplate(
            template_key=old_key, name=f"Budget {old_key}",
            subject=f"[MAGFest Budget] {old_key}",
            body_text=f"body for {old_key}", is_active=True,
        ))
    db.session.add(EmailOutbox(
        template_key="submitted", recipient_email="queued@test.local",
        status=OUTBOX_STATUS_QUEUED, created_at=datetime.utcnow(),
    ))
    db.session.add(EmailOutbox(
        template_key="submitted", recipient_email="sent@test.local",
        status=OUTBOX_STATUS_SENT, created_at=datetime.utcnow(),
    ))
    db.session.add(NotificationLog(
        template_key="submitted", recipient_email="logged@test.local",
        created_at=datetime.utcnow(),
    ))
    db.session.commit()


def _run_rename():
    """Apply the migration's data change against the test session."""
    from migrations.versions.em3315c9a74b_normalise_budget_template_keys import (
        rename_budget_template_keys,
    )
    rename_budget_template_keys(db.session.connection())
    db.session.commit()


def test_every_template_moves_to_its_prefixed_key(app, pre_rename_rows):
    _run_rename()

    for old_key, new_key in RENAMES.items():
        assert get_template(new_key) is not None, f"{new_key} missing"
        assert get_template(old_key) is None, f"{old_key} still present"


def test_a_queued_outbox_row_follows_the_rename(app, pre_rename_rows):
    _run_rename()

    queued = EmailOutbox.query.filter_by(recipient_email="queued@test.local").one()
    assert queued.template_key == "budget_submitted"


def test_a_sent_outbox_row_keeps_its_original_key(app, pre_rename_rows):
    """Terminal rows are history, not work."""
    _run_rename()

    sent = EmailOutbox.query.filter_by(recipient_email="sent@test.local").one()
    assert sent.template_key == "submitted"


def test_notification_logs_keep_their_original_key(app, pre_rename_rows):
    """An audit trail records what was used at the time."""
    _run_rename()

    logged = NotificationLog.query.filter_by(recipient_email="logged@test.local").one()
    assert logged.template_key == "submitted"


def test_the_rename_is_idempotent(app, pre_rename_rows):
    """Re-running must not create duplicates or raise on the unique index."""
    _run_rename()
    _run_rename()

    assert EmailTemplate.query.filter_by(template_key="budget_submitted").count() == 1


def test_wording_is_untouched(app, pre_rename_rows):
    """This PR renames keys. Any subject or body change here is a bug."""
    before = {
        t.template_key: (t.subject, t.body_text, t.name, t.is_active)
        for t in EmailTemplate.query.all()
    }

    _run_rename()

    for old_key, new_key in RENAMES.items():
        moved = get_template(new_key)
        assert (moved.subject, moved.body_text, moved.name, moved.is_active) == (
            before[old_key]
        )


def test_a_renamed_template_still_resolves_for_sending(app, pre_rename_rows):
    """The drainer resolves a queued row through get_effective_template. A
    rename that leaves that lookup empty blocks every queued row."""
    from app.services.email_templates import get_effective_template

    _run_rename()

    queued = EmailOutbox.query.filter_by(recipient_email="queued@test.local").one()
    assert get_effective_template(queued.template_key, None) is not None


def test_a_per_event_override_survives_the_rename(app, pre_rename_rows):
    """Overrides are keyed by email_template_id, not by key string. A broken
    one silently reverts an event to base wording."""
    from app.models import EmailTemplateEventOverride, EventCycle

    cycle = EventCycle(code="OVR2027", name="Override Event",
                       is_active=True, is_default=False, sort_order=9)
    db.session.add(cycle)
    db.session.flush()
    base = get_template("submitted")
    base_id = base.id
    db.session.add(EmailTemplateEventOverride(
        email_template_id=base_id, event_cycle_id=cycle.id,
        subject="Event-specific subject",
    ))
    db.session.commit()

    _run_rename()

    override = EmailTemplateEventOverride.query.filter_by(
        event_cycle_id=cycle.id).one()
    assert override.email_template_id == base_id
    assert override.subject == "Event-specific subject"
    assert get_template("budget_submitted").id == base_id
