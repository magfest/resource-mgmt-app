"""
Email debug and testing routes for budget admins.
"""
from datetime import datetime, timedelta

from flask import render_template, redirect, url_for, request, flash

from app import db
from app.models import NotificationLog, User
from app.routes import get_user_ctx
from app.routes.admin_final.helpers import require_budget_admin
from . import admin_final_bp


@admin_final_bp.get("/admin/budget/email/")
def email_debug():
    """
    Email debug page - view notification log and send test emails.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    # Get filter params
    status_filter = request.args.get("status", "")
    template_filter = request.args.get("template", "")
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

    # Get logs (most recent first)
    logs = query.order_by(NotificationLog.created_at.desc()).limit(200).all()

    # Get unique statuses and templates for filter dropdowns
    all_statuses = db.session.query(NotificationLog.status).distinct().all()
    all_templates = db.session.query(NotificationLog.template_key).distinct().all()

    # Get counts by status
    status_counts = {}
    for status in ["SENT", "SUPPRESSED", "DEBOUNCED", "FAILED", "QUEUED"]:
        count = db.session.query(NotificationLog).filter(
            NotificationLog.status == status,
            NotificationLog.created_at >= cutoff,
        ).count()
        if count > 0:
            status_counts[status] = count

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

    # Get rate limit status
    from app.services.email import get_rate_limit_status
    rate_limits = get_rate_limit_status()

    return render_template(
        "admin_final/email_debug.html",
        user_ctx=user_ctx,
        logs=logs,
        status_filter=status_filter,
        template_filter=template_filter,
        days=days,
        all_statuses=[s[0] for s in all_statuses],
        all_templates=[t[0] for t in all_templates],
        status_counts=status_counts,
        email_config=email_config,
        rate_limits=rate_limits,
    )


@admin_final_bp.post("/admin/budget/email/test")
def email_test_send():
    """
    Send a test email to the current user.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

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
    from app.services.email import send_email, is_email_enabled

    subject = "[MAGFest Budget] Test Email"
    body = f"""This is a test email from the MAGFest Budget system.

Sent at: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
Sent to: {recipient}
Email enabled: {is_email_enabled()}

If you received this email, your email configuration is working correctly.
"""

    success = send_email(
        to=recipient,
        subject=subject,
        body_text=body,
        template_key="test",
        skip_debounce=True,  # Always send test emails
    )
    db.session.commit()

    if success:
        if is_email_enabled():
            flash(f"Test email sent to {recipient}", "success")
        else:
            flash(f"Test email logged (EMAIL_ENABLED=false). Check log below.", "info")
    else:
        flash("Failed to send test email. Check the log for details.", "error")

    return redirect(url_for("admin_final.email_debug"))
