"""Transport tests. The transport reads no database and never raises."""
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from app.services.email import build_message_parts, send_via_ses


def test_build_message_parts_detects_html():
    parts = build_message_parts("Hello <b>there</b>")
    assert parts.html is not None
    assert "<b>there</b>" in parts.html
    assert "<b>" not in parts.text


def test_build_message_parts_plain_text_has_no_html_part():
    parts = build_message_parts("Hello there")
    assert parts.html is None
    assert "Hello there" in parts.text


def test_send_via_ses_returns_failed_not_raises(app):
    """A raising transport would abort the drainer batch on one bad address."""
    err = ClientError({"Error": {"Code": "MessageRejected", "Message": "no"}}, "SendEmail")
    with app.app_context():
        with patch("app.services.email.boto3.client") as mk:
            mk.return_value.send_email.side_effect = err
            result = send_via_ses("a@example.org", "s", build_message_parts("body"))
    assert result.status == "FAILED"
    assert result.error_code == "MessageRejected"
