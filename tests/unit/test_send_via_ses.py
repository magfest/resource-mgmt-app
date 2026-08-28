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


def test_the_text_part_carries_no_html_entities():
    """Templates render with autoescape on, which is right for the HTML part
    and wrong for text/plain. A department named "Promo & Misc" reached
    recipients as "Promo &amp; Misc" until the text part was unescaped."""
    parts = build_message_parts("Event: FY27 Promo &amp; Misc Events")
    assert "FY27 Promo & Misc Events" in parts.text
    assert "&amp;" not in parts.text


def test_the_html_part_keeps_its_entities():
    """Unescaping the HTML part would turn escaped user input back into live
    markup, which is the injection autoescape exists to stop."""
    parts = build_message_parts("Hello <b>there</b>, &lt;script&gt; &amp; co")
    assert "&lt;script&gt;" in parts.html
    assert "&amp;" in parts.html
    assert "<script>" not in parts.html
