"""
Slack notification service for posting workflow updates to a channel.

One-way only: posts messages to a configured Slack channel via chat.postMessage.
Includes safety mechanisms mirroring email.py:
- Debounce: Skip duplicate notifications within 1 hour
- Circuit breaker: Pause sending if too many failures
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

import requests
from flask import current_app

from app import db
from app.models import NotificationLog, NOTIF_STATUS_SENT, NOTIF_STATUS_FAILED, NOTIF_STATUS_SUPPRESSED

NOTIF_STATUS_DEBOUNCED = "DEBOUNCED"
NOTIF_STATUS_CIRCUIT_OPEN = "CIRCUIT_OPEN"

SLACK_API_URL = "https://slack.com/api/chat.postMessage"
SLACK_CHANNEL = "SLACK"

DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 5
DEFAULT_CIRCUIT_BREAKER_WINDOW = 10  # minutes

logger = logging.getLogger(__name__)


def is_slack_enabled() -> bool:
    """Check if Slack notifications are enabled."""
    return current_app.config.get('SLACK_ENABLED', False)


def _check_circuit_breaker() -> Tuple[bool, Optional[str]]:
    """
    Check if circuit breaker is tripped (too many recent Slack failures).

    Filtered to channel="SLACK" so email failures don't affect Slack.
    Returns (allowed, reason) tuple.
    """
    window_minutes = current_app.config.get(
        'SLACK_CIRCUIT_BREAKER_WINDOW',
        DEFAULT_CIRCUIT_BREAKER_WINDOW,
    )
    threshold = current_app.config.get(
        'SLACK_CIRCUIT_BREAKER_THRESHOLD',
        DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
    )

    cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)

    recent_failures = db.session.query(NotificationLog).filter(
        NotificationLog.channel == SLACK_CHANNEL,
        NotificationLog.status == NOTIF_STATUS_FAILED,
        NotificationLog.created_at >= cutoff,
    ).count()

    if recent_failures >= threshold:
        return False, f"Slack circuit breaker open ({recent_failures} failures in {window_minutes} min)"

    return True, None


def _was_recently_sent(template_key: str, work_item_id: int, hours: int = 1) -> bool:
    """
    Check if we recently sent this Slack notification (debounce).

    Filtered to channel="SLACK". Since Slack posts go to one channel
    (not per-recipient), we debounce on template_key + work_item_id only.
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    existing = NotificationLog.query.filter(
        NotificationLog.channel == SLACK_CHANNEL,
        NotificationLog.template_key == template_key,
        NotificationLog.work_item_id == work_item_id,
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.created_at >= cutoff,
    ).first()

    return existing is not None


def _log_notification(
    template_key: str,
    status: str,
    work_item_id: Optional[int] = None,
    subject: Optional[str] = None,
    provider_message_id: Optional[str] = None,
    error: Optional[str] = None,
    channel_id: Optional[str] = None,
):
    """Record Slack notification in database."""
    channel_id = channel_id or current_app.config.get('SLACK_CHANNEL_ID', '')
    log = NotificationLog(
        channel=SLACK_CHANNEL,
        recipient_email=f"slack:{channel_id}",
        work_item_id=work_item_id,
        template_key=template_key,
        status=status,
        subject=subject,
        provider_message_id=provider_message_id,
        error_message=error,
        sent_at=datetime.utcnow() if status == NOTIF_STATUS_SENT else None,
    )
    db.session.add(log)


def send_slack_message(
    text: str,
    template_key: str,
    work_item_id: Optional[int] = None,
    blocks: Optional[List[dict]] = None,
    channel_id: Optional[str] = None,
) -> bool:
    """
    Post a message to the configured Slack channel.

    Args:
        text: Fallback text (shown in notifications and non-Block Kit clients)
        template_key: Identifier for debounce tracking (e.g. 'submitted')
        work_item_id: Optional work item ID for debounce tracking
        blocks: Optional Slack Block Kit blocks for rich formatting
        channel_id: Overrides SLACK_CHANNEL_ID. The feedback button posts to
            its own channel so triage is not buried under workflow posts.

    Returns True if sent (or safely skipped), False on error.
    """
    channel_id = channel_id or current_app.config.get('SLACK_CHANNEL_ID')

    # Check debounce
    if work_item_id and _was_recently_sent(template_key, work_item_id):
        _log_notification(
            channel_id=channel_id,
            template_key=template_key,
            status=NOTIF_STATUS_DEBOUNCED,
            work_item_id=work_item_id,
            subject=text[:256],
        )
        return True

    # Check if disabled
    if not is_slack_enabled():
        _log_notification(
            channel_id=channel_id,
            template_key=template_key,
            status=NOTIF_STATUS_SUPPRESSED,
            work_item_id=work_item_id,
            subject=text[:256],
            error="Slack disabled",
        )
        return True

    # Check circuit breaker
    allowed, reason = _check_circuit_breaker()
    if not allowed:
        _log_notification(
            channel_id=channel_id,
            template_key=template_key,
            status=NOTIF_STATUS_CIRCUIT_OPEN,
            work_item_id=work_item_id,
            subject=text[:256],
            error=reason,
        )
        logger.warning(f"Slack circuit breaker open: {reason}")
        return True

    # Send via Slack API
    token = current_app.config.get('SLACK_BOT_TOKEN')

    if not token or not channel_id:
        _log_notification(
            channel_id=channel_id,
            template_key=template_key,
            status=NOTIF_STATUS_FAILED,
            work_item_id=work_item_id,
            subject=text[:256],
            error="Missing SLACK_BOT_TOKEN or SLACK_CHANNEL_ID",
        )
        logger.error("Slack send failed: missing SLACK_BOT_TOKEN or SLACK_CHANNEL_ID")
        return False

    payload = {
        "channel": channel_id,
        "text": text,
    }
    if blocks:
        payload["blocks"] = blocks

    try:
        response = requests.post(
            SLACK_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        data = response.json()

        # Slack returns HTTP 200 even on errors — check the "ok" field
        if data.get("ok"):
            _log_notification(
                channel_id=channel_id,
                template_key=template_key,
                status=NOTIF_STATUS_SENT,
                work_item_id=work_item_id,
                subject=text[:256],
                provider_message_id=data.get("ts"),
            )
            return True
        else:
            error_msg = data.get("error", "unknown error")
            _log_notification(
                channel_id=channel_id,
                template_key=template_key,
                status=NOTIF_STATUS_FAILED,
                work_item_id=work_item_id,
                subject=text[:256],
                error=f"Slack API error: {error_msg}",
            )
            logger.error(f"Slack API error: {error_msg}")
            return False

    except Exception as e:
        _log_notification(
            channel_id=channel_id,
            template_key=template_key,
            status=NOTIF_STATUS_FAILED,
            work_item_id=work_item_id,
            subject=text[:256],
            error=str(e),
        )
        logger.error(f"Slack send failed: {e}")
        return False
