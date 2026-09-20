"""get_effective_template: every inherit-versus-override combination."""
from datetime import datetime

from app import db
from app.models import EmailTemplate, EmailTemplateEventOverride, EventCycle
from app.services.email_templates import get_effective_template


def _seed(app, **override_kwargs):
    # Not "finalized": seed_workflow_data already seeds that key and
    # template_key is unique.
    t = EmailTemplate(
        template_key="dispatched", name="Dispatched", subject="Base subject",
        body_text="Base body", is_active=True, version=3,
    )
    db.session.add(t)
    db.session.flush()
    cycle = db.session.query(EventCycle).first()
    if override_kwargs:
        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id, **override_kwargs))
    db.session.commit()
    return t, cycle


def test_missing_base_returns_none(app, seed_workflow_data):
    with app.app_context():
        assert get_effective_template("no_such_key") is None


def test_no_override_row_returns_base_and_no_window(app, seed_workflow_data):
    with app.app_context():
        t, cycle = _seed(app)
        eff = get_effective_template("dispatched", cycle.id)
        assert eff.subject == "Base subject"
        assert eff.body_text == "Base body"
        assert eff.is_active is True
        assert eff.send_window_start is None
        assert eff.send_window_end is None
        assert eff.override_id is None
        assert eff.base_version == 3
        assert eff.is_stale is False


def test_null_event_cycle_id_returns_base(app, seed_workflow_data):
    with app.app_context():
        _seed(app, subject="Custom")
        eff = get_effective_template("dispatched", None)
        assert eff.subject == "Base subject"
        assert eff.override_id is None


def test_each_column_overrides_independently(app, seed_workflow_data):
    with app.app_context():
        t, cycle = _seed(app, subject="Custom subject")
        eff = get_effective_template("dispatched", cycle.id)
        assert eff.subject == "Custom subject"
        assert eff.body_text == "Base body"   # NULL inherits


def test_is_active_is_an_and_not_an_override(app, seed_workflow_data):
    """A per-event row can silence further; it can never re-enable."""
    with app.app_context():
        t, cycle = _seed(app, is_active=True)
        t.is_active = False
        db.session.commit()
        assert get_effective_template("dispatched", cycle.id).is_active is False


def test_override_can_silence_an_active_base(app, seed_workflow_data):
    with app.app_context():
        t, cycle = _seed(app, is_active=False)
        assert get_effective_template("dispatched", cycle.id).is_active is False


def test_unset_override_is_active_does_not_silence_the_base(app, seed_workflow_data):
    """A row present only for a window or wording must not be a kill switch."""
    with app.app_context():
        t, cycle = _seed(app, send_window_start=datetime(2027, 3, 1, 5, 0, 0))
        assert get_effective_template("dispatched", cycle.id).is_active is True


def test_windows_come_through_as_stored(app, seed_workflow_data):
    with app.app_context():
        start = datetime(2027, 3, 1, 5, 0, 0)
        end = datetime(2027, 3, 9, 4, 59, 59)
        t, cycle = _seed(app, send_window_start=start, send_window_end=end)
        eff = get_effective_template("dispatched", cycle.id)
        assert eff.send_window_start == start
        assert eff.send_window_end == end


def test_stale_only_when_the_recorded_version_differs(app, seed_workflow_data):
    with app.app_context():
        t, cycle = _seed(app, subject="Custom", base_version_at_override=3)
        assert get_effective_template("dispatched", cycle.id).is_stale is False
        t.version = 4
        db.session.commit()
        assert get_effective_template("dispatched", cycle.id).is_stale is True


def test_untracked_override_is_not_stale(app, seed_workflow_data):
    """NULL means the override predates version tracking, not out of date."""
    with app.app_context():
        t, cycle = _seed(app, subject="Custom", base_version_at_override=None)
        assert get_effective_template("dispatched", cycle.id).is_stale is False


def test_untracked_override_stays_not_stale_after_base_version_bumps(
    app, seed_workflow_data
):
    """Untracked and out of date can both look true; NULL must win as untracked."""
    with app.app_context():
        t, cycle = _seed(app, subject="Custom", base_version_at_override=None)
        t.version = 4
        db.session.commit()
        assert get_effective_template("dispatched", cycle.id).is_stale is False
