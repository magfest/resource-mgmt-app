"""Tests for EmailSendResult refactor of send_email()."""
from unittest.mock import MagicMock, patch

from app.services.email import send_email, EmailSendResult


class TestEmailSendResult:
    def test_disabled_returns_suppressed(self, app):
        """When EMAIL_ENABLED=False, returns SUPPRESSED with sent=False."""
        app.config["EMAIL_ENABLED"] = False
        result = send_email(
            to="test@example.com",
            subject="x",
            body_text="x",
            template_key="test",
        )
        assert isinstance(result, EmailSendResult)
        assert result.sent is False
        assert result.status == "SUPPRESSED"

    def test_successful_send_returns_sent(self, app):
        """Mock SES success; verify SENT status and provider_message_id."""
        app.config["EMAIL_ENABLED"] = True
        with patch("app.services.email.boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.send_email.return_value = {"MessageId": "abc-123"}
            mock_boto.return_value = mock_client

            result = send_email(
                to="test@example.com",
                subject="x",
                body_text="x",
                template_key="test_send",
                skip_debounce=True,
            )

        assert result.sent is True
        assert result.status == "SENT"
        assert result.provider_message_id == "abc-123"

    def test_ses_failure_returns_failed(self, app):
        """When boto3 raises ClientError, returns FAILED with error message."""
        from botocore.exceptions import ClientError
        app.config["EMAIL_ENABLED"] = True

        with patch("app.services.email.boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.send_email.side_effect = ClientError(
                {"Error": {"Code": "MessageRejected", "Message": "bad recipient"}},
                "SendEmail",
            )
            mock_boto.return_value = mock_client

            result = send_email(
                to="bad@example.com",
                subject="x",
                body_text="x",
                template_key="test_fail",
                skip_debounce=True,
            )

        assert result.sent is False
        assert result.status == "FAILED"
        assert "bad recipient" in (result.error or "")
