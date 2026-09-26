"""
Admin routes for email template management.

Allows budget admins to view and edit email templates stored in the database.
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    EmailOutbox,
    EmailTemplate,
    EmailTemplateEventOverride,
    EventCycle,
    NotificationLog,
    NOTIF_STATUS_SENT,
)
from app.routes import h
from app.services.email_templates import (
    get_all_templates,
    get_effective_template,
    get_template,
    validate_jinja2_template,
    preview_template,
    variables_for_template_key,
)
from app.services.email import build_message_parts, send_via_ses, write_notification_log
from .helpers import (
    require_budget_admin,
    render_budget_admin_page,
    log_config_change,
    track_changes,
)
from app.models.constants import CONFIG_AUDIT_UPDATE, OUTBOX_CLAIMABLE_STATUSES
from app.services.email_windows import (
    eastern_date_to_utc_end,
    eastern_date_to_utc_start,
    utc_to_eastern_date,
)


email_templates_bp = Blueprint('email_templates', __name__, url_prefix='/email-templates')


def _preview_event_cycles():
    """Event cycles offered in the preview selector, newest event first."""
    return db.session.query(EventCycle).order_by(
        EventCycle.event_start_date.desc().nullslast(),
        EventCycle.code,
    ).all()


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
    variables = variables_for_template_key(email_template.template_key)

    return render_budget_admin_page(
        "admin/email_templates/form.html",
        email_template=email_template,
        variables=variables,
        event_cycles=_preview_event_cycles(),
        preview_event_cycle_id=None,
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

    # Which event to preview as. Blank means the base wording.
    event_cycle_id = request.form.get("event_cycle_id", type=int)

    # Render preview
    rendered = preview_template(temp_template, event_cycle_id=event_cycle_id)

    if not rendered:
        flash("Error rendering template. Check for syntax errors.", "error")
        return redirect(url_for(".edit_email_template", template_id=template_id))

    # Get available variables for this template
    variables = variables_for_template_key(email_template.template_key)

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
        event_cycles=_preview_event_cycles(),
        preview_event_cycle_id=event_cycle_id,
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

    # Send test email
    parts = build_message_parts(rendered_body)
    result = send_via_ses(to=user.email, subject=test_subject, parts=parts)
    # The transport writes no NotificationLog row, so this route writes its own.
    write_notification_log(
        recipient_email=user.email,
        template_key=f"test_{email_template.template_key}",
        status=result.status,
        subject=test_subject,
        provider_message_id=result.provider_message_id,
        error=result.error,
    )
    db.session.commit()
    success = result.status == NOTIF_STATUS_SENT

    if success:
        flash(f"Test email sent to {user.email}", "success")
    else:
        flash("Failed to send test email. Check server logs for details.", "error")

    return redirect(url_for(".edit_email_template", template_id=template_id))


# ============================================================
# Per-event email configuration
# ============================================================

def _window_status(effective, now: datetime) -> str:
    """Name what this template is doing for one event, right now.

    Silenced outranks the window: a template switched off is off whatever
    its dates say.
    """
    if not effective.is_active:
        return "silenced"
    if effective.send_window_start and effective.send_window_start > now:
        return "scheduled"
    if effective.send_window_end and effective.send_window_end < now:
        return "outside window"
    return "active"


@email_templates_bp.get("/events/")
@require_budget_admin
def list_event_cycles():
    """Go to the event already selected in the nav, or ask which one.

    Event first, not template first: the recurring task is configuring one
    event's email, and editing the wording every event shares is the rare
    one. Making someone pick an event they already picked is the friction
    that sends them to the shared templates instead.

    Imported inside the function: app.routes.home imports admin helpers, so a
    module-level import here would cycle.
    """
    from app.routes.home import get_selected_event_cycle

    selected, show_all_events = get_selected_event_cycle()
    if selected and not show_all_events:
        return redirect(url_for(".event_email_index", event_cycle_id=selected.id))

    # Nothing to resolve: "all events" mode, or no active cycle exists. Ask
    # rather than choosing for someone who asked to see everything.
    return render_budget_admin_page(
        "admin/email_templates/event_list.html", cycles=_preview_event_cycles())


@email_templates_bp.get("/events/<int:event_cycle_id>")
@require_budget_admin
def event_email_index(event_cycle_id: int):
    """What email goes out for one event, under what constraint, and how much."""
    cycle = db.session.get(EventCycle, event_cycle_id)
    if not cycle:
        abort(404, "Event cycle not found")

    now = datetime.utcnow()
    rows = []
    for base in get_all_templates():
        eff = get_effective_template(base.template_key, cycle.id)

        queued = db.session.query(EmailOutbox).filter(
            EmailOutbox.template_key == base.template_key,
            EmailOutbox.event_cycle_id == cycle.id,
            EmailOutbox.status.in_(OUTBOX_CLAIMABLE_STATUSES),
            EmailOutbox.dispatch_at <= now,
        ).count()
        scheduled = db.session.query(EmailOutbox).filter(
            EmailOutbox.template_key == base.template_key,
            EmailOutbox.event_cycle_id == cycle.id,
            EmailOutbox.status.in_(OUTBOX_CLAIMABLE_STATUSES),
            EmailOutbox.dispatch_at > now,
        ).count()
        # Reads notification_logs.event_cycle_id. Joining through work_item
        # would miss submission_reminder rows, whose work_item_id is NULL,
        # and counting from email_outbox undercounts after the 90-day prune.
        sent = db.session.query(NotificationLog).filter(
            NotificationLog.template_key == base.template_key,
            NotificationLog.event_cycle_id == cycle.id,
            NotificationLog.status == NOTIF_STATUS_SENT,
        ).count()

        rows.append({
            "template": base,
            "effective": eff,
            "custom_wording": eff.override_id is not None and (
                eff.subject != base.subject or eff.body_text != base.body_text),
            "window_start": utc_to_eastern_date(eff.send_window_start),
            "window_end": utc_to_eastern_date(eff.send_window_end),
            "status": _window_status(eff, now),
            "queued": queued,
            "scheduled": scheduled,
            "sent": sent,
        })

    return render_budget_admin_page(
        "admin/email_templates/event_index.html",
        cycle=cycle, rows=rows, all_cycles=_preview_event_cycles())


def _get_override(template_id: int, event_cycle_id: int):
    return db.session.query(EmailTemplateEventOverride).filter_by(
        email_template_id=template_id, event_cycle_id=event_cycle_id).first()


def _parse_window_date(raw: str):
    """Parse a date input. Empty means unbounded, not invalid."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return False


def _override_to_dict(override) -> dict:
    """Values tracked for the config audit log.

    Window bounds become ISO strings: log_config_change serialises this dict
    to JSON, which has no datetime type.
    """
    return {
        "subject": override.subject,
        "body_text": override.body_text,
        "is_active": override.is_active,
        "send_window_start": (override.send_window_start.isoformat()
                              if override.send_window_start else None),
        "send_window_end": (override.send_window_end.isoformat()
                            if override.send_window_end else None),
    }


@email_templates_bp.get("/events/<int:event_cycle_id>/<int:template_id>")
@require_budget_admin
def edit_event_override(event_cycle_id: int, template_id: int):
    """Edit one template's wording and window for one event."""
    cycle = db.session.get(EventCycle, event_cycle_id)
    if not cycle:
        abort(404, "Event cycle not found")
    base = _get_template_or_404(template_id)
    override = _get_override(base.id, cycle.id)

    return render_budget_admin_page(
        "admin/email_templates/override_form.html",
        cycle=cycle,
        base=base,
        override=override,
        window_start=utc_to_eastern_date(
            override.send_window_start if override else None),
        window_end=utc_to_eastern_date(
            override.send_window_end if override else None),
    )


@email_templates_bp.post("/events/<int:event_cycle_id>/<int:template_id>")
@require_budget_admin
def update_event_override(event_cycle_id: int, template_id: int):
    """Save one event's override. Blank fields store NULL and inherit."""
    cycle = db.session.get(EventCycle, event_cycle_id)
    if not cycle:
        abort(404, "Event cycle not found")
    base = _get_template_or_404(template_id)

    back = redirect(url_for(".edit_event_override",
                            event_cycle_id=cycle.id, template_id=base.id))

    # Blank stores NULL, never "". An empty-string subject would override the
    # base with nothing and send a subjectless email.
    subject = (request.form.get("subject") or "").strip() or None
    # Browsers submit textarea content with CRLF. Unnormalised, the body
    # differs from the base by invisible characters and reads as customised.
    body_text = (request.form.get("body_text") or "").replace("\r\n", "\n")
    body_text = body_text.strip() or None

    # Three states, so a select rather than a checkbox: a checkbox cannot tell
    # "silenced for this event" from "inherit", and would re-enable a template
    # an admin had switched off.
    raw_active = request.form.get("is_active", "inherit")
    is_active = {"inherit": None, "yes": True, "no": False}.get(raw_active)

    for field in (subject, body_text):
        if field is None:
            continue
        is_valid, error = validate_jinja2_template(field)
        if not is_valid:
            flash(f"Template error: {error}", "error")
            return back

    start_date = _parse_window_date(request.form.get("window_start"))
    end_date = _parse_window_date(request.form.get("window_end"))
    if start_date is False or end_date is False:
        flash("Enter window dates as YYYY-MM-DD.", "error")
        return back
    if start_date and end_date and start_date > end_date:
        # Always an operator error: nothing can fall inside the window, and
        # the resulting silence looks like a working configuration.
        flash("The send window starts after it ends. Nothing was saved.",
              "error")
        return back

    override = _get_override(base.id, cycle.id)
    created = override is None
    if created:
        override = EmailTemplateEventOverride(
            email_template_id=base.id, event_cycle_id=cycle.id,
            created_by_user_id=h.get_active_user_id(),
        )
        db.session.add(override)
        old_values = {}
    else:
        old_values = _override_to_dict(override)

    override.subject = subject
    override.body_text = body_text
    override.is_active = is_active
    override.send_window_start = (
        eastern_date_to_utc_start(start_date) if start_date else None)
    override.send_window_end = (
        eastern_date_to_utc_end(end_date) if end_date else None)
    # Drift marker: records which base wording this override was written
    # against, so the index can flag one the shared copy has moved past.
    override.base_version_at_override = base.version
    override.updated_by_user_id = h.get_active_user_id()
    override.updated_at = datetime.utcnow()

    db.session.flush()
    changes = track_changes(old_values, _override_to_dict(override))
    if changes:
        log_config_change("email_template_event_override", override.id,
                          CONFIG_AUDIT_UPDATE, changes)

    db.session.commit()
    flash(f"Saved {base.template_key} settings for {cycle.code}", "success")
    return redirect(url_for(".event_email_index", event_cycle_id=cycle.id))
