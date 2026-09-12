"""
Email template rendering service.

Provides database-backed email template loading and Jinja2 rendering.
Replaces filesystem-based templates with editable database templates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from flask import current_app
from jinja2 import Environment, BaseLoader, TemplateSyntaxError, UndefinedError

from app import db
from app.models import EmailTemplate, EmailTemplateEventOverride


logger = logging.getLogger(__name__)


# Documentation of available template variables per template type
EMAIL_TEMPLATE_VARIABLES = {
    'submitted': {
        'work_item': 'The WorkItem being submitted',
        'work_item.public_id': 'Public ID of the request (e.g., "TECH-001")',
        'work_item.portfolio.department.name': 'Department name',
        'work_item.portfolio.department.code': 'Department code',
        'work_item.portfolio.event_cycle.name': 'Event name (e.g., "MAGFest 2027")',
        'work_item.portfolio.event_cycle.code': 'Event code (e.g., "MAG2027")',
        'base_url': 'Base URL of the application',
    },
    'submission_confirmation': {
        'work_item': 'The WorkItem just submitted (BUDGET only)',
        'work_item.public_id': 'Public ID of the request',
        'work_item.reason': 'Optional reason text (used by supplementals)',
        'work_item.portfolio.department.name': 'Department name',
        'work_item.portfolio.department.code': 'Department code',
        'work_item.portfolio.event_cycle.name': 'Event name',
        'work_item.portfolio.event_cycle.code': 'Event code',
        'line_count': 'Number of budget lines on the submission',
        'total_requested_dollars': 'Sum of unit_price * quantity across lines, in dollars (float)',
        'base_url': 'Base URL of the application',
    },
    'dispatched': {
        'work_item': 'The WorkItem being dispatched',
        'work_item.public_id': 'Public ID of the request',
        'work_item.portfolio.department.name': 'Department name',
        'work_item.portfolio.department.code': 'Department code',
        'work_item.portfolio.event_cycle.name': 'Event name',
        'work_item.portfolio.event_cycle.code': 'Event code',
        'base_url': 'Base URL of the application',
    },
    'needs_attention': {
        'work_item': 'The WorkItem needing attention',
        'work_item.public_id': 'Public ID of the request',
        'work_item.portfolio.department.name': 'Department name',
        'work_item.portfolio.department.code': 'Department code',
        'work_item.portfolio.event_cycle.name': 'Event name',
        'work_item.portfolio.event_cycle.code': 'Event code',
        'base_url': 'Base URL of the application',
    },
    'response_received': {
        'work_item': 'The WorkItem with the response',
        'work_item.public_id': 'Public ID of the request',
        'work_item.portfolio.department.name': 'Department name',
        'work_item.portfolio.department.code': 'Department code',
        'work_item.portfolio.event_cycle.name': 'Event name',
        'work_item.portfolio.event_cycle.code': 'Event code',
        'base_url': 'Base URL of the application',
    },
    'finalized': {
        'work_item': 'The WorkItem that was finalized',
        'work_item.public_id': 'Public ID of the request',
        'work_item.portfolio.department.name': 'Department name',
        'work_item.portfolio.department.code': 'Department code',
        'work_item.portfolio.event_cycle.name': 'Event name',
        'work_item.portfolio.event_cycle.code': 'Event code',
        'base_url': 'Base URL of the application',
    },
    'submission_reminder': {
        'department': 'The Department being reminded',
        'department.name': 'Department name (e.g., "Tech Operations")',
        'department.code': 'Department code (e.g., "TECHOPS")',
        'event_cycle': 'The EventCycle',
        'event_cycle.name': 'Event name (e.g., "MAGFest 2027")',
        'event_cycle.code': 'Event code (e.g., "MAG2027")',
        'base_url': 'Base URL of the application',
    },
}


@dataclass
class RenderedEmail:
    """Result of rendering an email template."""
    subject: str
    body_text: str
    template_key: str


def get_template(template_key: str) -> EmailTemplate | None:
    """
    Fetch an email template by its key.

    Args:
        template_key: The unique template identifier (e.g., "submitted", "dispatched")

    Returns:
        EmailTemplate or None if not found
    """
    return db.session.query(EmailTemplate).filter_by(
        template_key=template_key
    ).first()


def get_all_templates() -> list[EmailTemplate]:
    """
    Fetch all email templates, ordered by name.

    Returns:
        List of all EmailTemplate records
    """
    return db.session.query(EmailTemplate).order_by(EmailTemplate.name).all()


@dataclass(frozen=True)
class EffectiveTemplate:
    """One template as a given event cycle sees it.

    This is not an EmailTemplate. It is the base row merged with its per-event
    override, and it is the only shape the send path should read; a caller
    holding an EmailTemplate is reading the wording no event necessarily gets.
    """
    template_key: str
    subject: str
    body_text: str
    is_active: bool
    send_window_start: datetime | None
    send_window_end: datetime | None
    base_version: int
    override_id: int | None
    is_stale: bool


def get_effective_template(
    template_key: str,
    event_cycle_id: int | None = None,
) -> EffectiveTemplate | None:
    """Merge a base template with its override for one event.

    Every NULL override column inherits, for subject, body_text, and
    is_active. Send windows have no base column to inherit from; a NULL
    window column means unbounded, not inherited. Returns None only when the
    base template does not exist; an inactive template still resolves,
    because the caller decides what silence means.
    """
    base = get_template(template_key)
    if base is None:
        return None

    override = None
    if event_cycle_id is not None:
        override = db.session.query(EmailTemplateEventOverride).filter_by(
            email_template_id=base.id, event_cycle_id=event_cycle_id
        ).first()

    if override is None:
        return EffectiveTemplate(
            template_key=template_key,
            subject=base.subject,
            body_text=base.body_text,
            is_active=bool(base.is_active),
            send_window_start=None,
            send_window_end=None,
            base_version=base.version,
            override_id=None,
            is_stale=False,
        )

    # AND, not override-wins. The base flag stays a dependable kill switch:
    # an event may silence a template further, never re-enable one the base
    # has retired.
    effective_active = bool(base.is_active) and (
        True if override.is_active is None else bool(override.is_active)
    )
    return EffectiveTemplate(
        template_key=template_key,
        subject=override.subject if override.subject is not None else base.subject,
        body_text=(override.body_text
                   if override.body_text is not None else base.body_text),
        is_active=effective_active,
        send_window_start=override.send_window_start,
        send_window_end=override.send_window_end,
        base_version=base.version,
        override_id=override.id,
        is_stale=(override.base_version_at_override is not None
                  and override.base_version_at_override != base.version),
    )


def render_email_template(
    template_key: str,
    context: dict[str, Any],
    event_cycle_id: int | None = None,
) -> RenderedEmail | None:
    """Render a template as one event cycle sees it.

    Returns None when the template is missing, inactive for this event, or
    raises a syntax or undefined-variable error. The caller distinguishes
    those; process_row cancels the first and parks the last.

    Args:
        context: Template variables, typically including 'work_item' and
            'base_url'.
        event_cycle_id: Resolves the per-event override. Omitting it renders
            the base wording, which is what a row with no event cycle gets.
    """
    effective = get_effective_template(template_key, event_cycle_id)

    if effective is None:
        logger.error(f"Email template not found: {template_key}")
        return None

    if not effective.is_active:
        logger.warning(f"Email template is inactive: {template_key}")
        return None

    try:
        # Create a Jinja2 environment for string template rendering
        env = Environment(loader=BaseLoader(), autoescape=True)

        # Render subject
        subject_template = env.from_string(effective.subject)
        rendered_subject = subject_template.render(**context)

        # Render body
        body_template = env.from_string(effective.body_text)
        rendered_body = body_template.render(**context)

        return RenderedEmail(
            subject=rendered_subject,
            body_text=rendered_body,
            template_key=template_key,
        )

    except (TemplateSyntaxError, UndefinedError) as e:
        logger.error(f"Error rendering email template '{template_key}': {e}")
        return None


def validate_jinja2_template(template_str: str) -> tuple[bool, str | None]:
    """
    Validate Jinja2 template syntax without rendering.

    Args:
        template_str: The template string to validate

    Returns:
        Tuple of (is_valid, error_message)
        - (True, None) if valid
        - (False, "error description") if invalid
    """
    try:
        env = Environment(loader=BaseLoader(), autoescape=True)
        env.parse(template_str)
        return True, None
    except TemplateSyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.message}"


def get_sample_context() -> dict[str, Any]:
    """
    Generate sample context for template preview/testing.

    Returns a mock context that mimics a real work_item for previewing templates.
    """
    class MockDepartment:
        name = "Technology Operations"
        code = "TECHOPS"

    class MockEventCycle:
        name = "MAGFest 2027"
        code = "MAG2027"

    class MockPortfolio:
        department = MockDepartment()
        event_cycle = MockEventCycle()

    class MockWorkType:
        name = "Budget"
        code = "BUDGET"

    class MockWorkItem:
        public_id = "TECHOPS-001"
        portfolio = MockPortfolio()
        # Documented for submission_confirmation. Without it Jinja renders a
        # blank, which reads as "this template has no reason line" rather than
        # "the preview has no sample".
        reason = "Additional equipment for the second stage"

    return {
        'work_item': MockWorkItem(),
        'department': MockDepartment(),
        'event_cycle': MockEventCycle(),
        'base_url': current_app.config.get('BASE_URL', 'https://budget.magfest.org'),
        # Documented in EMAIL_TEMPLATE_VARIABLES and supplied on a real send by
        # notifications.py. Absent here, submission_confirmation previewed with
        # blanks where its numbers go.
        'line_count': 12,
        'total_requested_dollars': 4820.50,
        # Supplied by the drainer on every send, undocumented but reachable.
        'work_type': MockWorkType(),
        'recipient_email': 'volunteer@example.org',
    }


def preview_template(
    template: EmailTemplate,
    event_cycle_id: int | None = None,
) -> RenderedEmail | None:
    """Render a template with sample data, optionally as one event sees it.

    The passed template may carry unsaved edits from the admin form, so its
    text is what renders for any field the event does not override. An
    overridden field wins instead: that is what the event receives whatever
    the base says. Reading the stored row for both would discard the edit the
    admin is looking at.
    """
    context = get_sample_context()
    subject = template.subject
    body_text = template.body_text

    if event_cycle_id is not None:
        base = get_template(template.template_key)
        if base is not None:
            override = db.session.query(EmailTemplateEventOverride).filter_by(
                email_template_id=base.id, event_cycle_id=event_cycle_id,
            ).first()
            if override is not None:
                if override.subject is not None:
                    subject = override.subject
                if override.body_text is not None:
                    body_text = override.body_text

    try:
        env = Environment(loader=BaseLoader(), autoescape=True)
        rendered_subject = env.from_string(subject).render(**context)
        rendered_body = env.from_string(body_text).render(**context)
        return RenderedEmail(
            subject=rendered_subject,
            body_text=rendered_body,
            template_key=template.template_key,
        )
    except (TemplateSyntaxError, UndefinedError) as e:
        logger.error(f"Error previewing template '{template.template_key}': {e}")
        return None
