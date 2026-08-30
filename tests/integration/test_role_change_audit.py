"""Role changes write a security audit row, and ALERT events reach Slack."""
import json

from app import db
from app.models import User
from app.security_audit import (
    log_security_event,
    EVENT_USER_MODIFY,
    CATEGORY_ADMIN,
    SEVERITY_ALERT,
    SEVERITY_INFO,
)


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def test_alert_event_posts_to_slack(app, monkeypatch):
    """An ALERT event attempts one Slack post carrying the event type."""
    calls = []

    def fake_send(text, template_key, work_item_id=None, blocks=None):
        calls.append({"text": text, "template_key": template_key})
        return True

    monkeypatch.setattr("app.security_audit.send_slack_message", fake_send)

    with app.app_context():
        log_security_event(
            EVENT_USER_MODIFY,
            category=CATEGORY_ADMIN,
            severity=SEVERITY_ALERT,
            user_id="test:admin",
            details={"target_email": "someone@magfest.org"},
        )
        db.session.commit()

    assert len(calls) == 1
    assert EVENT_USER_MODIFY in calls[0]["template_key"]


def test_info_event_does_not_post_to_slack(app, monkeypatch):
    """Routine events stay out of Slack."""
    calls = []

    def fake_send(text, template_key, work_item_id=None, blocks=None):
        calls.append(template_key)
        return True

    monkeypatch.setattr("app.security_audit.send_slack_message", fake_send)

    with app.app_context():
        log_security_event(
            EVENT_USER_MODIFY,
            category=CATEGORY_ADMIN,
            severity=SEVERITY_INFO,
            user_id="test:admin",
        )
        db.session.commit()

    assert calls == []


def test_slack_failure_does_not_lose_the_audit_row(app, monkeypatch):
    """Slack is a convenience. It must never break the control."""
    def exploding_send(text, template_key, work_item_id=None, blocks=None):
        raise RuntimeError("slack is down")

    monkeypatch.setattr("app.security_audit.send_slack_message", exploding_send)

    with app.app_context():
        log_security_event(
            EVENT_USER_MODIFY,
            category=CATEGORY_ADMIN,
            severity=SEVERITY_ALERT,
            user_id="test:admin",
            details={"target_email": "someone@magfest.org"},
        )
        db.session.commit()

        from app.models import SecurityAuditLog
        rows = db.session.query(SecurityAuditLog).filter_by(
            event_type=EVENT_USER_MODIFY
        ).all()
        assert len(rows) == 1
        assert rows[0].severity == SEVERITY_ALERT
