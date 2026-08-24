"""
Email debug and testing routes for system admins.
"""
from datetime import datetime, timedelta

from flask import render_template, redirect, url_for, request, flash

from app import db
from app.models import NotificationLog, User
from app.routes import get_user_ctx
from app.routes.admin_final.helpers import require_admin
from . import admin_final_bp


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

    # Get counts by status, with SENT split by channel
    status_counts = {}
    for status in ["SUPPRESSED", "DEBOUNCED", "FAILED", "QUEUED"]:
        count = db.session.query(NotificationLog).filter(
            NotificationLog.status == status,
            NotificationLog.created_at >= cutoff,
        ).count()
        if count > 0:
            status_counts[status] = count

    # Split SENT by channel
    email_sent = db.session.query(NotificationLog).filter(
        NotificationLog.status == "SENT",
        NotificationLog.channel == "EMAIL",
        NotificationLog.created_at >= cutoff,
    ).count()
    slack_sent = db.session.query(NotificationLog).filter(
        NotificationLog.status == "SENT",
        NotificationLog.channel == "SLACK",
        NotificationLog.created_at >= cutoff,
    ).count()
    if email_sent > 0:
        status_counts["SENT (Email)"] = email_sent
    if slack_sent > 0:
        status_counts["SENT (Slack)"] = slack_sent

    # Check email config
    from flask import current_app
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
