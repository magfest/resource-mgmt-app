"""
Email debug and testing routes for system admins.

Also the operator health page for the email outbox: queue depth, backlog age,
render failures, the suppression list, and the stored copy of any message that
went out. Every read of outbox state comes from app.services.email_health.
"""
from datetime import datetime, timedelta

from flask import (
    Response, abort, current_app, flash, redirect, render_template, request, url_for,
)

from app import db
from app.models import EmailMessageBody, EmailOutbox, EmailSuppression, NotificationLog, User
from app.models.constants import (
    OUTBOX_CLAIMABLE_STATUSES,
    NOTIF_STATUS_CANCELLED,
    NOTIF_STATUS_FAILED,
    NOTIF_STATUS_QUEUED,
    NOTIF_STATUS_RENDER_BLOCKED,
    NOTIF_STATUS_SENT,
    NOTIF_STATUS_SUPPRESSED,
    OUTBOX_STATUS_FAILED,
)
from app.routes import get_user_ctx
from app.routes.admin_final.helpers import require_admin
from app.services.email_health import (
    get_queue_health,
    lookup_messages,
    pending_messages,
)
from . import admin_final_bp


# Phase 1 has no bounce or complaint feedback, so SES accepting the message is
# the furthest the record goes. "Sent" and "Delivered" both claim knowledge
# nobody here has, and an operator reading "Delivered" stops looking.
NOTIF_STATUS_LABELS = {
    NOTIF_STATUS_QUEUED: "Queued",
    NOTIF_STATUS_FAILED: "Failed",
    NOTIF_STATUS_SUPPRESSED: "Suppressed",
    NOTIF_STATUS_CANCELLED: "Cancelled",
    NOTIF_STATUS_RENDER_BLOCKED: "Render blocked",
    "DEBOUNCED": "Debounced",
    "RATE_LIMITED": "Rate limited",
}


def notif_status_label(status, channel="EMAIL"):
    """Human label for a notification status. Slack confirms a post; SES does not."""
    if status == NOTIF_STATUS_SENT:
        return "Posted to Slack" if channel == "SLACK" else "Accepted by SES"
    return NOTIF_STATUS_LABELS.get(status, status)


def _ses_quota():
    """Return the SES 24-hour quota, or None if it cannot be read.

    An operator opens this page when email is already broken. A quota call that
    raises must cost the panel, not the page.
    """
    if not current_app.config.get("EMAIL_ENABLED", False):
        return None
    try:
        from app.services.email import _ses_client

        quota = _ses_client().get_send_quota()
        return {
            "max_24_hour": quota.get("Max24HourSend"),
            "sent_last_24_hours": quota.get("SentLast24Hours"),
            "max_send_rate": quota.get("MaxSendRate"),
        }
    except Exception as exc:
        current_app.logger.warning("SES quota unavailable: %s", exc)
        return None


@admin_final_bp.get("/admin/email/")
def email_debug():
    """
    Email debug page - view notification log and send test emails.
    System-wide tool accessible to all admins.
    """
    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    # Get filter params
    status_filter = request.args.get("status", "")
    template_filter = request.args.get("template", "")
    channel_filter = request.args.get("channel", "")
    days = int(request.args.get("days", "7"))

    # Build query
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = db.session.query(NotificationLog).filter(
        NotificationLog.created_at >= cutoff
    )

    if status_filter:
        query = query.filter(NotificationLog.status == status_filter)
    if template_filter:
        query = query.filter(NotificationLog.template_key == template_filter)
    if channel_filter:
        query = query.filter(NotificationLog.channel == channel_filter)

    # Get logs (most recent first)
    logs = query.order_by(NotificationLog.created_at.desc()).limit(200).all()

    # Get unique statuses, templates, and channels for filter dropdowns
    all_statuses = db.session.query(NotificationLog.status).distinct().all()
    all_templates = db.session.query(NotificationLog.template_key).distinct().all()
    all_channels = db.session.query(NotificationLog.channel).distinct().all()

    # Counts by status, with SENT split by channel. A list of (label, count,
    # tone) rather than a dict keyed on the raw status: the tile label is
    # channel-aware, and "SENT" is never a label an operator reads.
    status_counts = []
    tones = {
        NOTIF_STATUS_SUPPRESSED: "warn",
        "DEBOUNCED": "info",
        NOTIF_STATUS_FAILED: "bad",
        NOTIF_STATUS_QUEUED: "neutral",
    }
    for status, tone in tones.items():
        count = db.session.query(NotificationLog).filter(
            NotificationLog.status == status,
            NotificationLog.created_at >= cutoff,
        ).count()
        if count > 0:
            status_counts.append((notif_status_label(status), count, tone))

    # Split SENT by channel
    email_sent = db.session.query(NotificationLog).filter(
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.channel == "EMAIL",
        NotificationLog.created_at >= cutoff,
    ).count()
    slack_sent = db.session.query(NotificationLog).filter(
        NotificationLog.status == NOTIF_STATUS_SENT,
        NotificationLog.channel == "SLACK",
        NotificationLog.created_at >= cutoff,
    ).count()
    if email_sent > 0:
        status_counts.append((notif_status_label(NOTIF_STATUS_SENT, "EMAIL"), email_sent, "good"))
    if slack_sent > 0:
        status_counts.append((notif_status_label(NOTIF_STATUS_SENT, "SLACK"), slack_sent, "good"))

    health = get_queue_health()

    # Queued rows have no Notification Log entry; that table is written only
    # when a row terminates. Read them from the outbox so the page answers
    # "what is waiting" as well as "what happened". Hidden when a status
    # filter excludes them, since they are all non-terminal.
    pending = []
    if not status_filter or status_filter in OUTBOX_CLAIMABLE_STATUSES:
        if channel_filter in ("", "EMAIL"):
            pending = pending_messages()
            if template_filter:
                pending = [p for p in pending
                           if p["template_key"] == template_filter]

    recent_failures = (
        db.session.query(EmailOutbox)
        .filter(EmailOutbox.status == OUTBOX_STATUS_FAILED)
        .order_by(EmailOutbox.id.desc())
        .limit(20)
        .all()
    )

    suppressions = (
        db.session.query(EmailSuppression)
        .order_by(EmailSuppression.email)
        .all()
    )

    # Check email config
    email_config = {
        "enabled": current_app.config.get("EMAIL_ENABLED", False),
        "from_address": current_app.config.get("EMAIL_FROM_ADDRESS", "not set"),
        "base_url": current_app.config.get("BASE_URL", "not set"),
        "ses_region": current_app.config.get("AWS_SES_REGION", "us-east-1"),
        "has_credentials": bool(
            current_app.config.get("AWS_SES_ACCESS_KEY") and
            current_app.config.get("AWS_SES_SECRET_KEY")
        ),
    }

    # Check Slack config
    slack_config = {
        "enabled": current_app.config.get("SLACK_ENABLED", False),
        "has_token": bool(current_app.config.get("SLACK_BOT_TOKEN")),
        "channel_id": current_app.config.get("SLACK_CHANNEL_ID") or "not set",
    }

    # No rate-limit panel: the limiter went with the outbox rebuild. Task 12
    # puts queue health here instead.
    return render_template(
        "admin_final/email_debug.html",
        user_ctx=user_ctx,
        logs=logs,
        status_filter=status_filter,
        template_filter=template_filter,
        channel_filter=channel_filter,
        days=days,
        all_statuses=[s[0] for s in all_statuses],
        all_templates=[t[0] for t in all_templates],
        all_channels=[c[0] for c in all_channels],
        status_counts=status_counts,
        email_config=email_config,
        slack_config=slack_config,
        health=health,
        pending=pending,
        recent_failures=recent_failures,
        suppressions=suppressions,
        ses_quota=_ses_quota(),
        status_label=notif_status_label,
    )


@admin_final_bp.post("/admin/email/test")
def email_test_send():
    """
    Send a test email to the current user.
    """
    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    # Get recipient email
    recipient = (request.form.get("recipient") or "").strip()
    if not recipient:
        # Default to current user's email
        user = db.session.query(User).filter_by(id=user_ctx.user_id).first()
        if user and user.email:
            recipient = user.email
        else:
            flash("No recipient email provided and current user has no email.", "error")
            return redirect(url_for("admin_final.email_debug"))

    # Send test email
    from app.services.email import build_message_parts, send_via_ses, write_notification_log, is_email_enabled
    from app.models import NOTIF_STATUS_SENT, NOTIF_STATUS_SUPPRESSED

    subject = "[MAGFest Budget] Test Email"
    body = f"""This is a test email from the MAGFest Budget system.

Sent at: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
Sent to: {recipient}
Email enabled: {is_email_enabled()}

If you received this email, your email configuration is working correctly.
"""

    # The kill switch belongs here, not in the transport: send_via_ses stays a
    # thin call with no config reads, since the drainer relies on that.
    if not is_email_enabled():
        write_notification_log(
            recipient_email=recipient,
            template_key="test",
            status=NOTIF_STATUS_SUPPRESSED,
            subject=subject,
            error="Email disabled",
        )
        db.session.commit()
        flash(f"Test email logged (EMAIL_ENABLED=false). Check log below.", "info")
        return redirect(url_for("admin_final.email_debug"))

    parts = build_message_parts(body)
    result = send_via_ses(to=recipient, subject=subject, parts=parts)
    # The transport writes no NotificationLog row, so this route writes its own.
    write_notification_log(
        recipient_email=recipient,
        template_key="test",
        status=result.status,
        subject=subject,
        provider_message_id=result.provider_message_id,
        error=result.error,
    )
    db.session.commit()
    success = result.status == NOTIF_STATUS_SENT

    if success:
        flash(f"Test email sent to {recipient}", "success")
    else:
        flash("Failed to send test email. Check the log for details.", "error")

    return redirect(url_for("admin_final.email_debug"))


@admin_final_bp.post("/admin/email/test-slack")
def slack_test_send():
    """
    Send a test Slack message to the configured channel.
    """
    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    from app.services.slack import send_slack_message, is_slack_enabled

    text = ":test_tube: This is a test message from the MAGFest Budget system."
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":test_tube: *Test Message*\nSent at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\nSent by: {user_ctx.user.display_name if user_ctx.user else 'Unknown'}",
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "If you see this, Slack notifications are working correctly."}],
        },
    ]

    success = send_slack_message(
        text=text,
        blocks=blocks,
        template_key="test",
    )
    db.session.commit()

    if success:
        if is_slack_enabled():
            flash("Test Slack message sent. Check the channel.", "success")
        else:
            flash("Test Slack message logged (SLACK_ENABLED=false). Check log below.", "info")
    else:
        flash("Failed to send test Slack message. Check the log for details.", "error")

    return redirect(url_for("admin_final.email_debug"))


@admin_final_bp.get("/admin/email/lookup")
def email_message_lookup():
    """Answer "did this person get the email" from the notification log.

    Reads NotificationLog through email_health, not the outbox: the outbox is
    pruned at 90 days and would go blind on day 91 without saying so.
    """
    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    recipient = (request.args.get("recipient") or "").strip()
    public_id = (request.args.get("public_id") or "").strip()

    searched = bool(recipient or public_id)
    results = []
    if searched:
        results = lookup_messages(
            recipient_email=recipient or None,
            public_id=public_id or None,
        )

    return render_template(
        "admin_final/email_lookup.html",
        user_ctx=user_ctx,
        recipient=recipient,
        public_id=public_id,
        searched=searched,
        results=results,
        status_label=notif_status_label,
    )


@admin_final_bp.get("/admin/email/message/<int:log_id>")
def email_message(log_id):
    """Show one message's metadata and its stored body in an isolated frame."""
    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    log = db.session.get(NotificationLog, log_id)
    if log is None:
        abort(404)

    body = (
        db.session.query(EmailMessageBody)
        .filter(EmailMessageBody.notification_log_id == log_id)
        .first()
    )

    return render_template(
        "admin_final/email_message.html",
        user_ctx=user_ctx,
        log=log,
        body=body,
        status_label=notif_status_label,
    )


@admin_final_bp.get("/admin/email/message/<int:log_id>/body")
def email_message_body(log_id):
    """Serve one stored HTML body as its own isolated document.

    The body is attacker-influenced text; a requester controls line
    descriptions that end up in it. It never passes through the admin page's
    own template, so the page cannot be scripted by its own content. The
    response overrides the app-wide policy set in add_security_headers: that
    policy allows 'self' scripts and sets frame-ancestors 'none', which would
    both permit script here and blank the iframe that embeds it.
    """
    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    body = (
        db.session.query(EmailMessageBody)
        .filter(EmailMessageBody.notification_log_id == log_id)
        .first()
    )
    if body is None or not body.body_html:
        abort(404)

    response = Response(body.body_html, mimetype="text/html")
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    )
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@admin_final_bp.post("/admin/email/suppression/add")
def email_suppression_add():
    """Add one address to the suppression list."""
    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    email = (request.form.get("email") or "").strip().lower()
    reason = (request.form.get("reason") or "").strip() or None

    if not email:
        flash("Enter an email address to suppress.", "error")
        return redirect(url_for("admin_final.email_debug"))

    # The drainer matches on lower(email), so the stored value is lowercased
    # here. A mixed-case row would suppress nothing and look like it worked.
    existing = db.session.query(EmailSuppression).filter(
        db.func.lower(EmailSuppression.email) == email
    ).first()
    if existing:
        flash(f"{email} is already suppressed.", "info")
        return redirect(url_for("admin_final.email_debug"))

    db.session.add(EmailSuppression(
        email=email, reason=reason, created_by_user_id=user_ctx.user_id,
    ))
    db.session.commit()
    flash(f"Suppressed {email}.", "success")
    return redirect(url_for("admin_final.email_debug"))


@admin_final_bp.post("/admin/email/suppression/remove")
def email_suppression_remove():
    """Remove one address from the suppression list."""
    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    email = (request.form.get("email") or "").strip().lower()
    row = db.session.query(EmailSuppression).filter(
        db.func.lower(EmailSuppression.email) == email
    ).first()

    if row is None:
        flash(f"{email or 'That address'} is not suppressed.", "info")
        return redirect(url_for("admin_final.email_debug"))

    db.session.delete(row)
    db.session.commit()
    flash(f"Removed {email} from the suppression list.", "success")
    return redirect(url_for("admin_final.email_debug"))
