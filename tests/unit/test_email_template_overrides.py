"""The per-event override row. Every nullable column means inherit."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import EmailTemplate, EmailTemplateEventOverride, EventCycle


# seed_workflow_data seeds a "finalized" EmailTemplate already. This default
# avoids colliding with it.
def _base(key="dispatched"):
    t = EmailTemplate(
        template_key=key, name=key, subject="Base subject",
        body_text="Base body", is_active=True, version=3,
    )
    db.session.add(t)
    db.session.flush()
    return t


def test_one_override_per_template_and_event(app, seed_workflow_data):
    with app.app_context():
        t = _base()
        cycle = db.session.query(EventCycle).first()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id, subject="Custom"))
        db.session.commit()

        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id, subject="Second"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_every_column_defaults_to_null(app, seed_workflow_data):
    with app.app_context():
        t = _base("submitted")
        cycle = db.session.query(EventCycle).first()
        row = EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id)
        db.session.add(row)
        db.session.commit()
        assert row.subject is None
        assert row.body_text is None
        assert row.is_active is None
        assert row.send_window_start is None
        assert row.send_window_end is None
        assert row.base_version_at_override is None
