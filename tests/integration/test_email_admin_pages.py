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
    OUTBOX_STATUS_SENT,
    OUTBOX_STATUS_SUPPRESSED,
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


def test_a_view_cannot_exempt_itself_from_the_security_headers(app, client):
    """Only the allowlist in add_security_headers grants an exemption.

    The earlier fix let any response keep a policy it had already set, which
    made opting out of the app's security headers a one-line change invisible
    from app/__init__.py. The allowlist names its exemptions in the same place
    the policy is written.
    """
    @app.route("/__policy_probe__")
    def _policy_probe():
        resp = app.response_class("ok")
        resp.headers["Content-Security-Policy"] = "default-src 'none'"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        return resp

    resp = client.get("/__policy_probe__")
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_the_page_lists_mail_that_is_still_waiting(app, client, seed_workflow_data):
    """A queued row has no Notification Log entry, so without this section the
    page shows nothing at all for mail that has not drained yet."""
    db.session.add(EmailOutbox(
        template_key="submitted", recipient_email="waiting@example.org",
        dispatch_at=datetime.utcnow(), status=OUTBOX_STATUS_QUEUED,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    _login(client, "test:admin")

    html = client.get("/admin/email/").get_data(as_text=True)
    assert "Waiting to send" in html
    assert "waiting@example.org" in html


def test_finished_rows_are_not_counted_as_queue_depth(app, client, seed_workflow_data):
    """SENT and SUPPRESSED rows linger up to 90 days. Listing them under one
    depth heading tells an operator the queue is backed up when it is empty."""
    for status in (OUTBOX_STATUS_SENT, OUTBOX_STATUS_SUPPRESSED):
        db.session.add(EmailOutbox(
            template_key="submitted", recipient_email="done@example.org",
            dispatch_at=datetime.utcnow(), status=status,
            created_at=datetime.utcnow(),
        ))
    db.session.commit()
    _login(client, "test:admin")

    html = client.get("/admin/email/").get_data(as_text=True)
    live = html.split("Outcomes, last 90 days")[0]
    assert "In the queue now" in live
    assert "Nothing waiting." in live
    assert OUTBOX_STATUS_SENT not in live.split("In the queue now")[1]


def test_a_slack_row_is_not_labelled_with_an_ses_id(app, client, seed_workflow_data):
    """The provider id column carried a hardcoded SES label, so a Slack
    message timestamp was displayed as though SES had returned it."""
    db.session.add(NotificationLog(
        channel="SLACK", template_key="submitted",
        recipient_email="slack:C06CK56CW5Q", status=NOTIF_STATUS_SENT,
        provider_message_id="1787623427.862909", created_at=datetime.utcnow(),
    ))
    db.session.commit()
    _login(client, "test:admin")

    html = client.get("/admin/email/").get_data(as_text=True)
    assert "Slack ts" in html
    assert "SES: 1787623427" not in html


def _panel(html, key):
    """Return the markup inside one tab panel."""
    start = html.index(f'id="panel-{key}"')
    rest = html[start:]
    nxt = rest.find('class="tab-panel')
    return rest if nxt == -1 else rest[:nxt]


def test_every_section_lands_in_exactly_one_tab(app, client, seed_workflow_data):
    """The page is assembled by moving whole blocks between panels, so the
    failure mode is a section landing in two tabs or none rather than a
    rendering error. Neither raises."""
    _login(client, "test:admin")
    html = client.get("/admin/email/").get_data(as_text=True)

    homes = {
        "queue": ["In the queue now", "Outcomes, last 90 days",
                  "Recent Failed Outbox Rows"],
        "log": ["Notification Log"],
        "suppression": ["Suppression List"],
        "config": ["SES sending quota", "Send Test Notifications"],
    }
    for tab, headings in homes.items():
        panel = _panel(html, tab)
        for heading in headings:
            assert f">{heading}" in panel or f"{heading}</h3>" in panel, \
                f"{heading!r} is not in the {tab} panel"
        for other, other_headings in homes.items():
            if other == tab:
                continue
            for heading in other_headings:
                assert f"{heading}</h3>" not in panel, \
                    f"{heading!r} also appears in the {tab} panel"


def test_the_health_tiles_sit_outside_every_tab(app, client, seed_workflow_data):
    """The five health numbers are the reason the page gets opened. Putting
    them in a panel would hide four of them behind a click."""
    _login(client, "test:admin")
    html = client.get("/admin/email/").get_data(as_text=True)

    before_tabs = html.split('class="tab-row"')[0]
    for label in ["Queued", "Oldest overdue", "Render blocked",
                  "Failed, 90 days", "Email sending"]:
        assert label in before_tabs, f"{label!r} is not in the always-visible header"


def test_exactly_one_tab_starts_open(app, client, seed_workflow_data):
    """Two active panels render stacked; zero renders a blank page under the
    tab row. Both look like a broken page rather than a broken template."""
    _login(client, "test:admin")
    html = client.get("/admin/email/").get_data(as_text=True)

    assert html.count('class="tab-panel active"') == 1
    assert html.count('class="tab-btn active"') == 1
    assert html.count('class="tab-panel') == 4
    assert html.count('class="tab-btn') == 4
