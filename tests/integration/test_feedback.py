"""Tests for the in-app feedback button, which posts to its own Slack channel."""
from unittest.mock import MagicMock, patch

import pytest

from app.models import NotificationLog


FEEDBACK_CHANNEL = "CFEEDBACK1"
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _slack_ok():
    response = MagicMock()
    response.json.return_value = {"ok": True, "ts": "1700000000.000100"}
    return response


def _slack_error():
    response = MagicMock()
    response.json.return_value = {"ok": False, "error": "channel_not_found"}
    return response


@pytest.fixture
def feedback_on(app):
    app.config.update(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_CHANNEL_ID="CWORKFLOW1",
        SLACK_FEEDBACK_CHANNEL_ID=FEEDBACK_CHANNEL,
    )


def _post(client, **overrides):
    data = {
        "notes": "The approve button did nothing",
        "page_url": "https://budget.magfest.org/SMF27/TECHOPS/budget/item/SMF27-TECHOPS-BUD-3",
        "event": "SMF27",
        "dept": "TECHOPS",
        "public_id": "SMF27-TECHOPS-BUD-3",
        "viewport": "1440x900",
        "page_title": "Budget Request - SMF27-TECHOPS-BUD-3",
        "page_messages": "",
    }
    data.update(overrides)
    return client.post("/feedback", data=data, headers={"User-Agent": CHROME_UA})


def test_feedback_posts_to_the_feedback_channel_with_context(app, client, seed_workflow_data, feedback_on):
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post", return_value=_slack_ok()) as post:
        resp = _post(client)

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    payload = post.call_args.kwargs["json"]
    assert payload["channel"] == FEEDBACK_CHANNEL
    rendered = str(payload["blocks"])
    assert "The approve button did nothing" in rendered
    assert "Test Admin" in rendered
    assert "admin@test.local" in rendered
    assert "SMF27-TECHOPS-BUD-3" in rendered
    assert "TECHOPS" in rendered
    assert "1440x900" in rendered
    assert "Budget Request - SMF27-TECHOPS-BUD-3" in rendered
    assert "localhost" in rendered
    assert "Chrome 154 on Windows" in rendered
    assert "AppleWebKit" not in rendered


def test_feedback_identity_comes_from_the_session_not_the_form(app, client, seed_workflow_data, feedback_on):
    _login(client, "test:reviewer")

    with patch("app.services.slack.requests.post", return_value=_slack_ok()) as post:
        _post(client, user="Test Admin", email="admin@test.local")

    rendered = str(post.call_args.kwargs["json"]["blocks"])
    assert "Test Reviewer" in rendered
    assert "admin@test.local" not in rendered


def test_feedback_escapes_slack_control_sequences(app, client, seed_workflow_data, feedback_on):
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post", return_value=_slack_ok()) as post:
        _post(client, notes="<!channel> see <https://evil.example|here> & more")

    payload = post.call_args.kwargs["json"]
    everything = str(payload["blocks"]) + payload["text"]
    assert "<!channel>" not in everything
    assert "&lt;!channel&gt;" in everything
    assert "&amp; more" in everything


def test_feedback_reports_failure_when_slack_rejects_it(app, client, seed_workflow_data, feedback_on):
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post", return_value=_slack_error()):
        resp = _post(client)

    assert resp.status_code == 502
    assert resp.get_json()["ok"] is False


def test_feedback_commits_its_notification_log_row(app, client, seed_workflow_data, feedback_on):
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post", return_value=_slack_ok()):
        _post(client)

    from app import db
    db.session.rollback()
    rows = db.session.query(NotificationLog).filter_by(template_key="feedback").all()
    assert len(rows) == 1


@pytest.mark.parametrize("notes", ["", "   \r\n  ", "x" * 4001], ids=["empty", "whitespace", "too-long"])
def test_feedback_rejects_empty_or_oversized_notes(app, client, seed_workflow_data, feedback_on, notes):
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post") as post:
        resp = _post(client, notes=notes)

    assert resp.status_code == 400
    post.assert_not_called()


def test_feedback_limit_counts_crlf_as_one_character(app, client, seed_workflow_data, feedback_on):
    _login(client, "test:admin")
    notes = "a\r\n" * 1333  # 3999 chars as submitted, 2666 once normalized

    with patch("app.services.slack.requests.post", return_value=_slack_ok()):
        resp = _post(client, notes=notes)

    assert resp.status_code == 200


def test_feedback_requires_a_signed_in_user(app, client, seed_workflow_data, feedback_on):
    with patch("app.services.slack.requests.post") as post:
        resp = _post(client)

    assert resp.status_code in (302, 403)
    post.assert_not_called()


def test_feedback_refuses_when_the_channel_is_not_configured(app, client, seed_workflow_data, feedback_on):
    app.config["SLACK_FEEDBACK_CHANNEL_ID"] = None
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post") as post:
        resp = _post(client)

    assert resp.status_code == 503
    post.assert_not_called()


def test_feedback_refuses_when_slack_is_disabled(app, client, seed_workflow_data, feedback_on):
    app.config["SLACK_ENABLED"] = False
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post") as post:
        resp = _post(client)

    assert resp.status_code == 503
    post.assert_not_called()


def test_nav_shows_feedback_button_when_configured(app, client, seed_workflow_data, feedback_on):
    _login(client, "test:admin")

    html = client.get("/whats-new").get_data(as_text=True)

    assert 'id="feedback-open"' in html
    assert 'id="feedback-dialog"' in html


def test_nav_hides_feedback_button_when_not_configured(app, client, seed_workflow_data, feedback_on):
    app.config["SLACK_FEEDBACK_CHANNEL_ID"] = None
    _login(client, "test:admin")

    html = client.get("/whats-new").get_data(as_text=True)

    assert 'id="feedback-open"' not in html
    assert 'id="feedback-dialog"' not in html


def test_workflow_slack_messages_still_use_the_default_channel(app, feedback_on):
    from app.services.slack import send_slack_message

    with patch("app.services.slack.requests.post", return_value=_slack_ok()) as post:
        send_slack_message(text="hello", template_key="submitted")

    assert post.call_args.kwargs["json"]["channel"] == "CWORKFLOW1"


def test_feedback_reports_failure_when_the_circuit_breaker_is_open(app, client, seed_workflow_data, feedback_on):
    """send_slack_message returns True when the breaker is open; feedback must not."""
    from app import db
    from app.models import NOTIF_STATUS_FAILED

    for _ in range(5):
        db.session.add(NotificationLog(
            channel="SLACK", recipient_email="slack:CWORKFLOW1",
            template_key="submitted", status=NOTIF_STATUS_FAILED,
        ))
    db.session.commit()
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post") as post:
        resp = _post(client)

    assert resp.status_code == 502
    post.assert_not_called()


def test_request_page_puts_its_public_id_in_the_dialog(app, client, seed_draft_work_item, feedback_on):
    work_item = seed_draft_work_item["work_item"]
    portfolio = work_item.portfolio
    url = (f"/{portfolio.event_cycle.code}/{portfolio.department.code}"
           f"/budget/item/{work_item.public_id}")
    _login(client, "test:admin")

    html = client.get(url).get_data(as_text=True)

    assert f'name="public_id" value="{work_item.public_id}"' in html
    assert f'name="dept" value="{portfolio.department.code}"' in html


def test_feedback_names_the_real_user_while_impersonating(app, client, seed_workflow_data, feedback_on):
    with client.session_transaction() as sess:
        sess["real_user_id"] = "test:admin"
        sess["active_user_id"] = "test:reviewer"

    with patch("app.services.slack.requests.post", return_value=_slack_ok()) as post:
        _post(client)

    rendered = str(post.call_args.kwargs["json"]["blocks"])
    assert "Test Admin (admin@test.local)" in rendered
    assert "viewing as Test Reviewer" in rendered
    assert "reviewer@test.local" not in rendered


def test_feedback_notes_a_role_override(app, client, seed_workflow_data, feedback_on):
    app.config["BETA_TESTING_MODE"] = True
    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"
        sess["role_override"] = "approver"

    with patch("app.services.slack.requests.post", return_value=_slack_ok()) as post:
        _post(client)

    assert "role override: approver" in str(post.call_args.kwargs["json"]["blocks"])


def test_feedback_includes_on_screen_messages_escaped(app, client, seed_workflow_data, feedback_on):
    _login(client, "test:admin")

    with patch("app.services.slack.requests.post", return_value=_slack_ok()) as post:
        _post(client, page_messages="Error: Line 3 <b>could not</b> be saved")

    rendered = str(post.call_args.kwargs["json"]["blocks"])
    assert "Error: Line 3 &lt;b&gt;could not&lt;/b&gt; be saved" in rendered


@pytest.mark.parametrize("ua, expected", [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
     "Safari 18 on macOS"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0", "Firefox 140 on Windows"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
     "Edge 140 on Windows"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
     "Safari 18 on iPhone"),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
     "Chrome 140 on Android"),
    ("curl/8.4.0", "curl/8.4.0"),
], ids=["safari-mac", "firefox", "edge", "iphone", "android", "unknown"])
def test_short_browser_label(ua, expected):
    from app.services.feedback import short_browser
    assert short_browser(ua) == expected
