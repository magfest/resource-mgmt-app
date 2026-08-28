"""SES transport and the NotificationLog writer.

This module no longer decides whether to send. Debounce, rate limiting, and
the circuit breaker went with the outbox rebuild; dedup now lives in
email_outbox.dedup_key and pacing belongs to the drainer. send_via_ses hands
one message to SES and never raises, so a bad recipient cannot abort a batch.
"""
from __future__ import annotations

import re
from html import unescape

import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import ClientError
from dataclasses import dataclass
from datetime import datetime
from flask import current_app
from typing import Optional

from app import db
from app.models import NotificationLog, NOTIF_STATUS_SENT, NOTIF_STATUS_FAILED


DEFAULT_DAILY_LIMIT = 200


def is_email_enabled() -> bool:
    """Check if email sending is enabled."""
    return current_app.config.get('EMAIL_ENABLED', False)


def get_from_address() -> str:
    """Get the from address."""
    return current_app.config.get('EMAIL_FROM_ADDRESS', 'noreply@magfest.org')


def get_daily_limit() -> int:
    """Get max emails per day."""
    return current_app.config.get('EMAIL_DAILY_LIMIT', DEFAULT_DAILY_LIMIT)


@dataclass(frozen=True)
class MessageParts:
    text: str
    html: str | None = None


@dataclass(frozen=True)
class EmailSendResult:
    status: str                      # SENT or FAILED, nothing else
    provider_message_id: str | None = None
    error: str | None = None
    error_code: str | None = None    # raw botocore code, for classification


_FOOTER = (
    "\n\n---\n"
    "This is an automated message from the MAGFest Budget System "
    "— replies here disappear into the void! "
    "For help, reach out on Slack or email accounting@magfest.org."
)


def build_message_parts(body_text: str) -> MessageParts:
    """Turn rendered body text into the parts SES will be handed.

    Split out of the SES call so the drainer can store a body for a row it
    never sends. A suppressed recipient still gets an archived record of what
    would have gone to them, and that path makes no SES call at all.
    """
    body_text = body_text + _FOOTER
    # Undo Jinja's autoescape for the text part. Templates render with
    # autoescape on, which is right for the HTML part below and wrong here: a
    # department named "Promo & Misc" reaches the recipient as "Promo &amp;
    # Misc" in a text/plain message. The HTML part keeps the entities.
    plain_text = unescape(re.sub(r'<[^>]+>', '', body_text))
    html = None
    if re.search(r'<(b|strong|u|i|em|a|br|p)[\s>]', body_text, re.IGNORECASE):
        html_body = body_text.replace('\n', '<br>\n')
        html = (
            '<!DOCTYPE html>\n<html>\n<head><meta charset="UTF-8"></head>\n'
            '<body style="font-family: -apple-system, BlinkMacSystemFont, '
            "'Segoe UI', Roboto, sans-serif; font-size: 14px; line-height: 1.5; "
            f'color: #333;">\n{html_body}\n</body>\n</html>'
        )
    return MessageParts(text=plain_text, html=html)


# botocore defaults to 60s connect, 60s read, and 4 retries. An unreachable SES
# endpoint would then hold one row for about five minutes and consume the
# drainer's whole 420s window on every tick while the queue grows behind it.
# The drainer runs its own retry ladder, so botocore retrying underneath it
# multiplies the delay without adding reliability.
_SES_CONFIG = BotocoreConfig(
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 2},
)


def _ses_client():
    """Build a boto3 SES client from configured credentials or the default chain."""
    access_key = current_app.config.get('AWS_SES_ACCESS_KEY')
    secret_key = current_app.config.get('AWS_SES_SECRET_KEY')
    region = current_app.config.get('AWS_SES_REGION', 'us-east-1')
    if access_key and secret_key:
        return boto3.client(
            'ses',
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=_SES_CONFIG,
        )
    # Default credential chain: IAM role, env vars, etc.
    return boto3.client('ses', region_name=region, config=_SES_CONFIG)


def send_via_ses(to: str, subject: str, parts: MessageParts) -> EmailSendResult:
    """Hand one message to SES. No database reads, no debounce, no rate limit.

    Never raises. A transport that raised would abort the drainer's batch loop
    on one bad recipient, so every failure comes back as a FAILED result.
    """
    body_content = {'Text': {'Data': parts.text, 'Charset': 'UTF-8'}}
    if parts.html:
        body_content['Html'] = {'Data': parts.html, 'Charset': 'UTF-8'}
    try:
        client = _ses_client()
        response = client.send_email(
            Source=get_from_address(),
            Destination={'ToAddresses': [to]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': body_content,
            },
        )
        return EmailSendResult(status=NOTIF_STATUS_SENT,
                               provider_message_id=response.get('MessageId'))
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code')
        current_app.logger.error(f"SES send failed for {to}: {e}")
        return EmailSendResult(status=NOTIF_STATUS_FAILED, error=str(e), error_code=code)
    except Exception as e:  # connection errors, credential errors
        current_app.logger.error(f"SES send errored for {to}: {e}")
        return EmailSendResult(status=NOTIF_STATUS_FAILED, error=str(e), error_code=None)


def write_notification_log(
    recipient_email: str,
    template_key: str,
    status: str,
    work_item_id: Optional[int] = None,
    recipient_user_id: Optional[str] = None,
    subject: Optional[str] = None,
    provider_message_id: Optional[str] = None,
    error: Optional[str] = None,
    event_cycle_id: Optional[int] = None,
):
    """Record notification in database.

    Returns the log so a caller can attach a stored message body to it by id.
    Caller handles commit.
    """
    log = NotificationLog(
        recipient_email=recipient_email,
        recipient_user_id=recipient_user_id,
        work_item_id=work_item_id,
        template_key=template_key,
        status=status,
        subject=subject,
        provider_message_id=provider_message_id,
        error_message=error,
        event_cycle_id=event_cycle_id,
        sent_at=datetime.utcnow() if status == NOTIF_STATUS_SENT else None,
    )
    db.session.add(log)
    return log
