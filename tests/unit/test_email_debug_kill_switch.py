"""EMAIL_ENABLED kill switch for the admin-final email debug test-send route.

Regression test: email_test_send used to call send_via_ses unconditionally,
so a disabled environment with live AWS credentials sent real mail while
telling the admin it had only been logged.
"""
from unittest.mock import patch

from app.models import NotificationLog, NOTIF_STATUS_SUPPRESSED


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def test_email_test_send_suppressed_when_disabled(app, client, seed_workflow_data):
    app.config["EMAIL_ENABLED"] = False
    _login(client, "test:admin")

    # email_debug.py imports send_via_ses locally inside the view function
    # (`from app.services.email import ...`), so the patch target is the
    # defining module, not the route module's namespace.
    with patch("app.services.email.send_via_ses") as mock_send:
        resp = client.post(
            "/admin/email/test",
            data={"recipient": "someone@example.org"},
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert mock_send.call_count == 0

    log = NotificationLog.query.filter_by(
        recipient_email="someone@example.org", template_key="test",
    ).one()
    assert log.status == NOTIF_STATUS_SUPPRESSED
    assert log.error_message == "Email disabled"
