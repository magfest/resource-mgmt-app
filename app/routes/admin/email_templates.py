"""
Admin routes for email template management.

Allows budget admins to view and edit email templates stored in the database.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import EmailTemplate
from app.routes import h
from app.services.email_templates import (
    get_all_templates,
    get_template,
    validate_jinja2_template,
    preview_template,
    EMAIL_TEMPLATE_VARIABLES,
)
from app.services.email import send_email
from .helpers import (
    require_budget_admin,
    render_budget_admin_page,
    log_config_change,
    track_changes,
)
from app.models.constants import CONFIG_AUDIT_UPDATE


email_templates_bp = Blueprint('email_templates', __name__, url_prefix='/email-templates')


def _get_template_or_404(template_id: int) -> EmailTemplate:
    """Get email template by ID or abort with 404."""
    template = db.session.get(EmailTemplate, template_id)
    if not template:
        abort(404, "Email template not found")
    return template


def _template_to_dict(template: EmailTemplate) -> dict:
    """Convert email template to dict for change tracking."""
    return {
        "name": template.name,
        "description": template.description,
        "subject": template.subject,
        "body_text": template.body_text,
        "is_active": template.is_active,
    }


@email_templates_bp.get("/")
@require_budget_admin
def list_email_templates():
    """List all email templates."""
    templates = get_all_templates()

    return render_budget_admin_page(
        "admin/email_templates/list.html",
        templates=templates,
    )


@email_templates_bp.get("/<int:template_id>")
@require_budget_admin
def edit_email_template(template_id: int):
    """Show edit form for email template."""
    email_template = _get_template_or_404(template_id)

    # Get available variables for this template
    variables = EMAIL_TEMPLATE_VARIABLES.get(email_template.template_key, {})

    return render_budget_admin_page(
        "admin/email_templates/form.html",
        email_template=email_template,
        variables=variables,
    )


@email_templates_bp.post("/<int:template_id>")
@require_budget_admin
def update_email_template(template_id: int):
    """Update an email template."""
    email_template = _get_template_or_404(template_id)

    # Track old values
    old_values = _template_to_dict(email_template)

    # Get form values
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    subject = (request.form.get("subject") or "").strip()
    body_text = request.form.get("body_text") or ""
    is_active = request.form.get("is_active") == "1"

    # Validate required fields
    if not name:
        flash("Name is required", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    if not subject:
        flash("Subject is required", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    if not body_text:
        flash("Body text is required", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Validate Jinja2 syntax for subject
    is_valid, error = validate_jinja2_template(subject)
    if not is_valid:
        flash(f"Subject template error: {error}", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Validate Jinja2 syntax for body
    is_valid, error = validate_jinja2_template(body_text)
    if not is_valid:
        flash(f"Body template error: {error}", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Update template
    email_template.name = name
    email_template.description = description
    email_template.subject = subject
    email_template.body_text = body_text
    email_template.is_active = is_active
    email_template.version += 1
    email_template.updated_by_user_id = h.get_active_user_id()

    # Track and log changes
    new_values = _template_to_dict(email_template)
    changes = track_changes(old_values, new_values)
    if changes:
        log_config_change("email_template", email_template.id, CONFIG_AUDIT_UPDATE, changes)

    db.session.commit()
    flash(f"Updated email template: {email_template.name}", "success")
    return redirect(url_for(".list_email_templates"))


@email_templates_bp.post("/<int:template_id>/preview")
@require_budget_admin
def preview_email_template(template_id: int):
    """Preview an email template with sample data."""
    email_template = _get_template_or_404(template_id)

    # Get form values for preview (use current form state, not saved)
    subject = (request.form.get("subject") or "").strip() or email_template.subject
    body_text = request.form.get("body_text") or email_template.body_text

    # Create a temporary template object with form values
    temp_template = EmailTemplate(
        template_key=email_template.template_key,
        name=email_template.name,
        subject=subject,
        body_text=body_text,
        is_active=True,
    )

    # Render preview
    rendered = preview_template(temp_template)

    if not rendered:
        flash("Error rendering template. Check for syntax errors.", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Get available variables for this template
    variables = EMAIL_TEMPLATE_VARIABLES.get(email_template.template_key, {})

    flash("Preview rendered below", "success")

    # Convert newlines to <br> for HTML preview display
    preview_body_html = rendered.body_text.replace('\n', '<br>\n')

    return render_budget_admin_page(
        "admin/email_templates/form.html",
        email_template=email_template,
        variables=variables,
        preview_subject=rendered.subject,
        preview_body=preview_body_html,
        # Pass form values back to preserve unsaved changes
        form_subject=subject,
        form_body_text=body_text,
        form_name=request.form.get("name") or email_template.name,
        form_description=request.form.get("description") or email_template.description,
        form_is_active=request.form.get("is_active") == "1" if "is_active" in request.form else email_template.is_active,
    )


@email_templates_bp.post("/<int:template_id>/test")
@require_budget_admin
def test_email_template(template_id: int):
    """Send a test email using the template to the current user."""
    from flask import current_app
    from app.models import User
    from app.services.email_templates import get_sample_context
    from app.services.email import is_email_enabled

    email_template = _get_template_or_404(template_id)

    # Check if email is enabled first
    if not is_email_enabled():
        flash("Email sending is disabled in this environment. Enable EMAIL_ENABLED to test.", "warning")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Get current user's email
    user_id = h.get_active_user_id()
    user = db.session.query(User).filter_by(id=user_id).first()

    if not user or not user.email:
        flash("Could not find your email address to send test email", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Get form values for test (use current form state, not saved)
    subject = (request.form.get("subject") or "").strip() or email_template.subject
    body_text = request.form.get("body_text") or email_template.body_text

    # Validate templates first
    is_valid, error = validate_jinja2_template(subject)
    if not is_valid:
        flash(f"Subject template error: {error}", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    is_valid, error = validate_jinja2_template(body_text)
    if not is_valid:
        flash(f"Body template error: {error}", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Render with sample context
    from jinja2 import Environment, BaseLoader
    context = get_sample_context()

    try:
        env = Environment(loader=BaseLoader(), autoescape=True)
        rendered_subject = env.from_string(subject).render(**context)
        rendered_body = env.from_string(body_text).render(**context)
    except Exception as e:
        flash(f"Error rendering template: {e}", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Add test prefix to subject
    test_subject = f"[TEST] {rendered_subject}"

    # Send test email (skip rate limits for test emails)
    success = send_email(
        to=user.email,
        subject=test_subject,
        body_text=rendered_body,
        template_key=f"test_{email_template.template_key}",
        skip_rate_limit=True,
    )
    db.session.commit()  # Commit the notification log

    if success:
        flash(f"Test email sent to {user.email}", "success")
    else:
        flash("Failed to send test email. Check server logs for details.", "error")

    return redirect(url_for(".edit_email_template", template_id=template_id))
