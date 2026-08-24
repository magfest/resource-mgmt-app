"""Operator-facing email admin pages: health, lookup, message body, suppression.

The body view is a disclosure boundary. Stored bodies are attacker-influenced
text, so they reach the operator through a sandboxed iframe fed by its own
route, never interpolated into the page.
"""
import re
from unittest.mock import patch

from app import db
from app.models import (
    EmailMessageBody,
    EmailOutbox,
    EmailSuppression,
    NotificationLog,
)
from app.models.constants import (
    NOTIF_STATUS_FAILED,
    NOTIF_STATUS_SENT,
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_RENDER_BLOCKED,
)
from datetime import datetime, timedelta


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def _log(recipient="who@example.org", template_key="submitted", status=NOTIF_STATUS_SENT):
    row = NotificationLog(
        channel="EMAIL",
        template_key=template_key,
        recipient_email=recipient,
        status=status,
        subject="Your request",
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_health_page_says_accepted_not_delivered(app, client, seed_workflow_data):
    """Phase 1 knows SES took the message and nothing more.

    There is no bounce or complaint handling, so "Sent" and "Delivered" both
    claim knowledge the system does not have.
    """
    _log()
    _login(client, "test:admin")

    resp = client.get("/admin/email/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Accepted by SES" in html
    assert "Delivered" not in html
    assert ">SENT<" not in html


def test_health_panels_report_queue_depth_and_blocked_templates(app, client, seed_workflow_data):
    now = datetime.utcnow()
    db.session.add_all([
        EmailOutbox(
            template_key="supply_submitted",
            recipient_email="a@example.org",
            dispatch_at=now - timedelta(minutes=5),
            status=OUTBOX_STATUS_RENDER_BLOCKED,
            blocked_since=now - timedelta(minutes=5),
        ),
        EmailOutbox(
            template_key="budget_finalized",
            recipient_email="b@example.org",
            dispatch_at=now - timedelta(minutes=90),
            status=OUTBOX_STATUS_QUEUED,
        ),
    ])
    db.session.commit()
    _login(client, "test:admin")

    resp = client.get("/admin/email/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "supply_submitted" in html
    assert re.search(r"\b(89|90|91) min", html), "backlog age not rendered"


def test_body_view_requires_super_admin(app, client, seed_workflow_data):
    """A user with no roles cannot read a stored body.

    This assertion is load-bearing, not inherited. `require_admin` is a
    callable each view invokes for itself; `admin_final_bp` has no
    before_request and no URL-prefix guard, so nothing gates `/admin/email/`
    as a surface. Deleting the two body routes' own `require_admin` calls
    turns both of these 403s into 200s.
    """
    log = _log()
    db.session.add(EmailMessageBody(
        notification_log_id=log.id, subject="s", body_text="t", body_html="<p>h</p>",
    ))
    db.session.commit()

    _login(client, "test:reviewer")
    assert client.get(f"/admin/email/message/{log.id}").status_code == 403
    assert client.get(f"/admin/email/message/{log.id}/body").status_code == 403


def test_body_renders_in_a_sandboxed_iframe(app, client, seed_workflow_data):
    log = _log()
    db.session.add(EmailMessageBody(
        notification_log_id=log.id,
        subject="Your request",
        body_text="plain",
        body_html="<b>SECRET-BODY-MARKER</b><script>alert(1)</script>",
    ))
    db.session.commit()
    _login(client, "test:admin")

    resp = client.get(f"/admin/email/message/{log.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "sandbox=" in html
    # Neither raw nor HTML-escaped. The body reaches the operator only through
    # the iframe's own route.
    assert "SECRET-BODY-MARKER" not in html


def test_body_route_serves_the_stored_html(app, client, seed_workflow_data):
    log = _log()
    db.session.add(EmailMessageBody(
        notification_log_id=log.id,
        subject="Your request",
        body_text="plain",
        body_html="<b>SECRET-BODY-MARKER</b>",
    ))
    db.session.commit()
    _login(client, "test:admin")

    resp = client.get(f"/admin/email/message/{log.id}/body")
    assert resp.status_code == 200
    assert "SECRET-BODY-MARKER" in resp.get_data(as_text=True)
    assert "default-src 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_lookup_by_recipient_lists_attempts(app, client, seed_workflow_data):
    _log(recipient="wanted@example.org", template_key="tpl_first")
    _log(recipient="wanted@example.org", template_key="tpl_second")
    _log(recipient="other@example.org", template_key="tpl_other")
    _login(client, "test:admin")

    resp = client.get("/admin/email/lookup?recipient=wanted@example.org")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "tpl_first" in html
    assert "tpl_second" in html
    assert "tpl_other" not in html
    assert "other@example.org" not in html


def test_suppression_add_and_remove(app, client, seed_workflow_data):
    _login(client, "test:admin")

    resp = client.post(
        "/admin/email/suppression/add",
        data={"email": "blocked@example.org", "reason": "hard bounce"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # The remove control's hidden input, not the flash message: this asserts
    # the address is listed, not merely that the POST said so.
    assert 'value="blocked@example.org"' in resp.get_data(as_text=True)
    assert EmailSuppression.query.filter_by(email="blocked@example.org").count() == 1

    resp = client.post(
        "/admin/email/suppression/remove",
        data={"email": "blocked@example.org"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert EmailSuppression.query.filter_by(email="blocked@example.org").count() == 0
    assert 'value="blocked@example.org"' not in resp.get_data(as_text=True)


def test_a_quota_failure_still_renders_the_page(app, client, seed_workflow_data):
    """The health page is what an operator opens when email is already broken."""
    app.config["EMAIL_ENABLED"] = True
    _log(status=NOTIF_STATUS_FAILED, template_key="failed_tpl")
    _login(client, "test:admin")

    with patch("app.services.email._ses_client", side_effect=Exception("no creds")):
        resp = client.get("/admin/email/")

    assert resp.status_code == 200


def test_the_body_routes_headers_survive_the_app_wide_ones(app, client, seed_workflow_data):
    """add_security_headers runs after the view and used to overwrite both.

    The app-wide policy sets script-src 'self' and frame-ancestors 'none'. On
    this route the first would permit script inside attacker-influenced HTML
    and the second would blank the iframe that embeds it, since X-Frame-Options
    DENY blocks same-origin framing too. A browser would show an empty box; the
    test client reads headers without enforcing them, so only asserting on the
    header values catches a regression here.
    """
    log = _log()
    db.session.add(EmailMessageBody(
        notification_log_id=log.id, body_html="<b>hi</b>",
    ))
    db.session.commit()
    _login(client, "test:admin")

    resp = client.get(f"/admin/email/message/{log.id}/body")
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" not in csp
    assert "frame-ancestors 'none'" not in csp
    assert resp.headers["X-Frame-Options"] != "DENY"


def test_other_pages_keep_the_app_wide_policy(app, client, seed_workflow_data):
    """The body route is the one exemption. Everything else stays locked down."""
    _login(client, "test:admin")
    resp = client.get("/admin/email/")
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"
