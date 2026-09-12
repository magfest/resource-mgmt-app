"""The per-event email index."""
from datetime import datetime, timedelta

from app import db
from app.models import (
    EmailOutbox, EmailTemplate, EmailTemplateEventOverride, EventCycle,
)
from app.models.constants import OUTBOX_STATUS_QUEUED

INDEX = "/admin/config/email-templates/events/{}"


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def _template(key="dispatched", version=2):
    t = EmailTemplate(template_key=key, name="Dispatched", subject="Base subject",
                      body_text="Base body", is_active=True, version=version)
    db.session.add(t)
    db.session.flush()
    return t


def test_index_shows_custom_wording_and_window(app, client, seed_workflow_data):
    with app.app_context():
        t = _template()
        cycle = db.session.query(EventCycle).first()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id,
            subject="Custom subject", base_version_at_override=1,
            send_window_end=datetime.utcnow() - timedelta(days=1)))
        db.session.commit()
        cycle_id = cycle.id

    _login(client, "test:admin")
    resp = client.get(INDEX.format(cycle_id))

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Custom subject" in body
    assert "outside window" in body.lower()
    # The override records base version 1 while the base row is at 2.
    assert "out of date" in body.lower()


def test_index_counts_queued_scheduled_and_sent_separately(
    app, client, seed_workflow_data
):
    """A scheduled row is not backlog, and a sent one is neither.

    Counting all three from one status would report a deferred row as
    overdue, which is the reading the queue-health panel exists to avoid.
    """
    from app.models import NotificationLog
    from app.models.constants import NOTIF_STATUS_SENT

    with app.app_context():
        _template()
        cycle = db.session.query(EventCycle).first()
        now = datetime.utcnow()
        for dispatch_at in (now - timedelta(hours=1), now + timedelta(days=30)):
            db.session.add(EmailOutbox(
                template_key="dispatched", recipient_email="a@example.org",
                event_cycle_id=cycle.id, status=OUTBOX_STATUS_QUEUED,
                dispatch_at=dispatch_at, created_at=now, attempt_count=0))
        db.session.add(NotificationLog(
            recipient_email="b@example.org", template_key="dispatched",
            status=NOTIF_STATUS_SENT, event_cycle_id=cycle.id, sent_at=now))
        db.session.commit()
        cycle_id = cycle.id

    _login(client, "test:admin")
    resp = client.get(INDEX.format(cycle_id))

    assert resp.status_code == 200
    row = _dispatched_row(resp.get_data(as_text=True))
    assert row["queued"] == "1"
    assert row["scheduled"] == "1"
    assert row["sent"] == "1"


def test_index_requires_budget_admin(app, client, seed_workflow_data):
    with app.app_context():
        cycle_id = db.session.query(EventCycle).first().id

    _login(client, "test:reviewer")
    resp = client.get(INDEX.format(cycle_id))

    assert resp.status_code in (302, 403)


def test_unknown_event_cycle_is_404(app, client, seed_workflow_data):
    _login(client, "test:admin")
    assert client.get(INDEX.format(999999)).status_code == 404


def _dispatched_row(body):
    """Pull the counts out of the dispatched row by its data attributes."""
    import re

    match = re.search(
        r'data-template-key="dispatched"(.*?)</tr>', body, re.S)
    assert match, "no row rendered for the dispatched template"
    cells = match.group(1)
    return {
        name: re.search(rf'data-count="{name}">\s*(\d+)', cells).group(1)
        for name in ("queued", "scheduled", "sent")
    }
