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


# ============================================================
# Task 4.3: the override edit form
# ============================================================

def _override_url(app, version=2):
    """Seed a base template and return the override form URL for it."""
    with app.app_context():
        t = _template(version=version)
        cycle = db.session.query(EventCycle).first()
        db.session.commit()
        return f"/admin/config/email-templates/events/{cycle.id}/{t.id}"


_BLANK = {"subject": "", "body_text": "", "window_start": "",
          "window_end": "", "is_active": "inherit"}


def test_override_saving_blank_fields_leaves_them_inheriting(
    app, client, seed_workflow_data
):
    """Blank is NULL, not an empty string.

    An empty-string subject would override the base with nothing, sending a
    subjectless email rather than inheriting the shared wording.
    """
    url = _override_url(app)
    _login(client, "test:admin")

    resp = client.post(url, data=dict(_BLANK))

    assert resp.status_code in (200, 302)
    with app.app_context():
        row = db.session.query(EmailTemplateEventOverride).one()
        assert row.subject is None
        assert row.body_text is None
        assert row.is_active is None


def test_override_window_dates_round_trip_across_a_dst_transition(
    app, client, seed_workflow_data
):
    """Save a March date and a November date, reload, see what was typed.

    Both values verified against zoneinfo: 2027-03-01 is EST (UTC-5), and
    2027-11-07 is the day DST ends, so 23:59:59 that evening is already EST.
    """
    url = _override_url(app)
    _login(client, "test:admin")

    client.post(url, data=dict(_BLANK, window_start="2027-03-01",
                               window_end="2027-11-07"))

    with app.app_context():
        row = db.session.query(EmailTemplateEventOverride).one()
        assert row.send_window_start == datetime(2027, 3, 1, 5, 0, 0)
        assert row.send_window_end == datetime(2027, 11, 8, 4, 59, 59)

    body = client.get(url).get_data(as_text=True)
    assert "2027-03-01" in body
    assert "2027-11-07" in body


def test_override_start_after_end_is_rejected(app, client, seed_workflow_data):
    """Always an operator error: nothing can fall inside the window, and the
    resulting silence looks identical to a working configuration."""
    url = _override_url(app)
    _login(client, "test:admin")

    resp = client.post(url, data=dict(_BLANK, window_start="2027-06-01",
                                      window_end="2027-05-01"),
                       follow_redirects=True)

    # A bare "start" appears on the 404 page too, so match the real message.
    assert resp.status_code == 200
    assert "starts after it ends" in resp.get_data(as_text=True).lower()
    with app.app_context():
        assert db.session.query(EmailTemplateEventOverride).count() == 0


def test_override_saving_stamps_the_base_version(app, client, seed_workflow_data):
    url = _override_url(app, version=2)
    _login(client, "test:admin")

    client.post(url, data=dict(_BLANK, subject="Custom"))

    with app.app_context():
        row = db.session.query(EmailTemplateEventOverride).one()
        assert row.base_version_at_override == 2


def test_override_crlf_from_the_textarea_is_normalised(
    app, client, seed_workflow_data
):
    """Browsers submit textarea content with CRLF. Stored unnormalised, an
    override differs from the base by invisible characters and every row reads
    as customised."""
    url = _override_url(app)
    _login(client, "test:admin")

    client.post(url, data=dict(_BLANK, body_text="line one\r\nline two"))

    with app.app_context():
        row = db.session.query(EmailTemplateEventOverride).one()
        assert row.body_text == "line one\nline two"


def test_override_edits_the_existing_row_rather_than_adding_one(
    app, client, seed_workflow_data
):
    """One override per (template, event); the table has a unique constraint.

    A second insert would raise IntegrityError, so saving twice must update.
    """
    url = _override_url(app)
    _login(client, "test:admin")

    client.post(url, data=dict(_BLANK, subject="First"))
    client.post(url, data=dict(_BLANK, subject="Second"))

    with app.app_context():
        row = db.session.query(EmailTemplateEventOverride).one()
        assert row.subject == "Second"


def test_override_three_way_active_stores_false_not_null(
    app, client, seed_workflow_data
):
    """is_active has three states. A checkbox would collapse "off" into
    "inherit" and silently re-enable a template an admin had silenced."""
    url = _override_url(app)
    _login(client, "test:admin")

    client.post(url, data=dict(_BLANK, is_active="no"))

    with app.app_context():
        row = db.session.query(EmailTemplateEventOverride).one()
        assert row.is_active is False


def test_override_form_requires_budget_admin(app, client, seed_workflow_data):
    url = _override_url(app)
    _login(client, "test:reviewer")

    assert client.get(url).status_code in (302, 403)
    assert client.post(url, data=dict(_BLANK)).status_code in (302, 403)
    with app.app_context():
        assert db.session.query(EmailTemplateEventOverride).count() == 0


def test_preview_posts_the_selected_event_through_to_the_render(
    app, client, seed_workflow_data
):
    """Covers the wiring, not the resolver: form field to request.form to
    preview_template. The unit tests prove the merge itself."""
    with app.app_context():
        t = _template()
        cycle = db.session.query(EventCycle).first()
        db.session.add(EmailTemplateEventOverride(
            email_template_id=t.id, event_cycle_id=cycle.id,
            subject="Event only subject"))
        db.session.commit()
        template_id, cycle_id = t.id, cycle.id

    _login(client, "test:admin")
    resp = client.post(
        f"/admin/config/email-templates/{template_id}/preview",
        data={"subject": "Typed subject", "body_text": "Typed body",
              "event_cycle_id": str(cycle_id)})

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Event only subject" in body
    # The body is not overridden, so the unsaved text still previews.
    assert "Typed body" in body
