"""
Email service using AWS SES.

Includes safety mechanisms:
- Debounce: Skip duplicate notifications within 1 hour
- Rate limit: Max emails per hour (default 50)
- Daily limit: Max emails per day (default 200)
- Circuit breaker: Pause sending if too many failures
"""
from __future__ import annotations

import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timedelta
from flask import current_app
from typing import Optional, Tuple

from app import db
from app.models import NotificationLog, NOTIF_STATUS_SENT, NOTIF_STATUS_FAILED, NOTIF_STATUS_SUPPRESSED


# Additional statuses
NOTIF_STATUS_DEBOUNCED = "DEBOUNCED"
NOTIF_STATUS_RATE_LIMITED = "RATE_LIMITED"
NOTIF_STATUS_CIRCUIT_OPEN = "CIRCUIT_OPEN"

# Default limits (can be overridden via config)
DEFAULT_HOURLY_LIMIT = 50
DEFAULT_DAILY_LIMIT = 200
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 5  # failures in last 10 minutes triggers circuit breaker
DEFAULT_CIRCUIT_BREAKER_WINDOW = 10  # minutes


def is_email_enabled() -> bool:
    """Check if email sending is enabled."""
    return current_app.config.get('EMAIL_ENABLED', False)


def get_from_address() -> str:
    """Get the from address."""
    return current_app.config.get('EMAIL_FROM_ADDRESS', 'noreply@magfest.org')


def get_hourly_limit() -> int:
    """Get max emails per hour."""
    return current_app.config.get('EMAIL_HOURLY_LIMIT', DEFAULT_HOURLY_LIMIT)


def get_daily_limit() -> int:
    """Get max emails per day."""
    return current_app.config.get('EMAIL_DAILY_LIMIT', DEFAULT_DAILY_LIMIT)


def check_rate_limits() -> Tuple[bool, Optional[str]]:
    """
    Check if we're within rate limits.

    Returns (allowed, reason) tuple.
    """
    now = datetime.utcnow()

    # Check hourly limit
    hour_ago = now - timedelta(hours=1)
    hourly_count = db.session.query(NotificationLog).filter(
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.created_at >= hour_ago,
    ).count()

    hourly_limit = get_hourly_limit()
    if hourly_count >= hourly_limit:
        return False, f"Hourly limit reached ({hourly_count}/{hourly_limit})"

    # Check daily limit
    day_ago = now - timedelta(days=1)
    daily_count = db.session.query(NotificationLog).filter(
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.created_at >= day_ago,
    ).count()

    daily_limit = get_daily_limit()
    if daily_count >= daily_limit:
        return False, f"Daily limit reached ({daily_count}/{daily_limit})"

    return True, None


def check_circuit_breaker() -> Tuple[bool, Optional[str]]:
    """
    Check if circuit breaker is tripped (too many recent failures).

    Returns (allowed, reason) tuple.
    """
    window_minutes = current_app.config.get(
        'EMAIL_CIRCUIT_BREAKER_WINDOW',
        DEFAULT_CIRCUIT_BREAKER_WINDOW
    )
    threshold = current_app.config.get(
        'EMAIL_CIRCUIT_BREAKER_THRESHOLD',
        DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    )

    cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)

    recent_failures = db.session.query(NotificationLog).filter(
        NotificationLog.status == NOTIF_STATUS_FAILED,
        NotificationLog.created_at >= cutoff,
    ).count()

    if recent_failures >= threshold:
        return False, f"Circuit breaker open ({recent_failures} failures in {window_minutes} min)"

    return True, None


def was_recently_sent(
    template_key: str,
    work_item_id: int,
    recipient_email: str,
    hours: int = 1,
) -> bool:
    """
    Check if we recently sent this notification (debounce).

    Returns True if we should SKIP sending.
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    existing = NotificationLog.query.filter(
        NotificationLog.template_key == template_key,
        NotificationLog.work_item_id == work_item_id,
        NotificationLog.recipient_email == recipient_email,
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.created_at >= cutoff,
    ).first()

    return existing is not None


def get_rate_limit_status() -> dict:
    """
    Get current rate limit status for display.

    Returns dict with hourly/daily counts and limits.
    """
    now = datetime.utcnow()

    hour_ago = now - timedelta(hours=1)
    hourly_sent = db.session.query(NotificationLog).filter(
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.created_at >= hour_ago,
    ).count()

    day_ago = now - timedelta(days=1)
    daily_sent = db.session.query(NotificationLog).filter(
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.created_at >= day_ago,
    ).count()

    window_minutes = current_app.config.get(
        'EMAIL_CIRCUIT_BREAKER_WINDOW',
        DEFAULT_CIRCUIT_BREAKER_WINDOW
    )
    cutoff = now - timedelta(minutes=window_minutes)
    recent_failures = db.session.query(NotificationLog).filter(
        NotificationLog.status == NOTIF_STATUS_FAILED,
        NotificationLog.created_at >= cutoff,
    ).count()

    return {
        "hourly_sent": hourly_sent,
        "hourly_limit": get_hourly_limit(),
        "daily_sent": daily_sent,
        "daily_limit": get_daily_limit(),
        "recent_failures": recent_failures,
        "circuit_breaker_threshold": current_app.config.get(
            'EMAIL_CIRCUIT_BREAKER_THRESHOLD',
            DEFAULT_CIRCUIT_BREAKER_THRESHOLD
        ),
        "circuit_breaker_window": window_minutes,
    }


def send_email(
    to: str,
    subject: str,
    body_text: str,
    template_key: str,
    work_item_id: Optional[int] = None,
    recipient_user_id: Optional[str] = None,
    skip_debounce: bool = False,
    skip_rate_limit: bool = False,
) -> bool:
    """
    Send an email via AWS SES.

    Returns True if sent (or skipped due to debounce/limits), False on error.

    Safety mechanisms:
    - Debounce: Same template+recipient+work_item within 1 hour = skip
    - Rate limit: Max 50/hour and 200/day by default
    - Circuit breaker: Pauses if 5+ failures in 10 minutes
    """
    # Check debounce
    if not skip_debounce and work_item_id:
        if was_recently_sent(template_key, work_item_id, to):
            _log_notification(
                recipient_email=to,
                template_key=template_key,
                status=NOTIF_STATUS_DEBOUNCED,
                work_item_id=work_item_id,
                recipient_user_id=recipient_user_id,
                subject=subject,
            )
            return True

    # Check if disabled
    if not is_email_enabled():
        _log_notification(
            recipient_email=to,
            template_key=template_key,
            status=NOTIF_STATUS_SUPPRESSED,
            work_item_id=work_item_id,
            recipient_user_id=recipient_user_id,
            subject=subject,
            error="Email disabled",
        )
        return True

    # Check rate limits (unless bypassed for test emails)
    if not skip_rate_limit:
        allowed, reason = check_rate_limits()
        if not allowed:
            _log_notification(
                recipient_email=to,
                template_key=template_key,
                status=NOTIF_STATUS_RATE_LIMITED,
                work_item_id=work_item_id,
                recipient_user_id=recipient_user_id,
                subject=subject,
                error=reason,
            )
            current_app.logger.warning(f"Email rate limited: {reason}")
            return True  # Return True so callers don't retry immediately

        # Check circuit breaker
        allowed, reason = check_circuit_breaker()
        if not allowed:
            _log_notification(
                recipient_email=to,
                template_key=template_key,
                status=NOTIF_STATUS_CIRCUIT_OPEN,
                work_item_id=work_item_id,
                recipient_user_id=recipient_user_id,
                subject=subject,
                error=reason,
            )
            current_app.logger.warning(f"Email circuit breaker open: {reason}")
            return True  # Return True so callers don't retry immediately

    try:
        # Get AWS credentials - use environment if keys not provided
        access_key = current_app.config.get('AWS_SES_ACCESS_KEY')
        secret_key = current_app.config.get('AWS_SES_SECRET_KEY')
        region = current_app.config.get('AWS_SES_REGION', 'us-east-1')

        # Create client - if no explicit keys, boto3 will use IAM role / env vars
        if access_key and secret_key:
            client = boto3.client(
                'ses',
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        else:
            # Use default credential chain (IAM role, env vars, etc.)
            client = boto3.client('ses', region_name=region)

        # Append standard footer to all emails
        footer = (
            "\n\n---\n"
            "This is an automated message from the MAGFest Budget System "
            "\u2014 replies here disappear into the void! "
            "For help, reach out on Slack or email accounting@magfest.org."
        )
        body_text = body_text + footer

        # Build email body - support both HTML and plain text
        # If body contains HTML tags, send as HTML with plain text fallback
        body_content = {}

        # Always include plain text version (strip HTML tags for fallback)
        import re
        plain_text = re.sub(r'<[^>]+>', '', body_text)
        body_content['Text'] = {'Data': plain_text, 'Charset': 'UTF-8'}

        # If body contains basic HTML tags, also send as HTML
        if re.search(r'<(b|strong|u|i|em|a|br|p)[\s>]', body_text, re.IGNORECASE):
            # Wrap in basic HTML structure and convert newlines to <br>
            html_body = body_text.replace('\n', '<br>\n')
            html_wrapped = f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; line-height: 1.5; color: #333;">
{html_body}
</body>
</html>'''
            body_content['Html'] = {'Data': html_wrapped, 'Charset': 'UTF-8'}

        response = client.send_email(
            Source=get_from_address(),
            Destination={'ToAddresses': [to]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': body_content,
            }
        )

        _log_notification(
            recipient_email=to,
            template_key=template_key,
            status=NOTIF_STATUS_SENT,
            work_item_id=work_item_id,
            recipient_user_id=recipient_user_id,
            subject=subject,
            provider_message_id=response.get('MessageId'),
        )
        return True

    except ClientError as e:
        _log_notification(
            recipient_email=to,
            template_key=template_key,
            status=NOTIF_STATUS_FAILED,
            work_item_id=work_item_id,
            recipient_user_id=recipient_user_id,
            subject=subject,
            error=str(e),
        )
        current_app.logger.error(f"SES send failed: {e}")
        return False


def _log_notification(
    recipient_email: str,
    template_key: str,
    status: str,
    work_item_id: Optional[int] = None,
    recipient_user_id: Optional[str] = None,
    subject: Optional[str] = None,
    provider_message_id: Optional[str] = None,
    error: Optional[str] = None,
):
    """Record notification in database."""
    log = NotificationLog(
        recipient_email=recipient_email,
        recipient_user_id=recipient_user_id,
        work_item_id=work_item_id,
        template_key=template_key,
        status=status,
        subject=subject,
        provider_message_id=provider_message_id,
        error_message=error,
        sent_at=datetime.utcnow() if status == NOTIF_STATUS_SENT else None,
    )
    db.session.add(log)
    # Caller handles commit
