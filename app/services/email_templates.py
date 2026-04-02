"""
Email template rendering service.

Provides database-backed email template loading and Jinja2 rendering.
Replaces filesystem-based templates with editable database templates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from flask import current_app
from jinja2 import Environment, BaseLoader, TemplateSyntaxError, UndefinedError

from app import db
from app.models import EmailTemplate


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


def render_email_template(
    template_key: str,
    context: dict[str, Any],
) -> RenderedEmail | None:
    """
    Render an email template with the given context.

    Args:
        template_key: The unique template identifier
        context: Dictionary of variables to pass to the template
            (typically includes 'work_item' and 'base_url')

    Returns:
        RenderedEmail with subject and body_text, or None if template not found/inactive
    """
    template = get_template(template_key)

    if not template:
        logger.error(f"Email template not found: {template_key}")
        return None

    if not template.is_active:
        logger.warning(f"Email template is inactive: {template_key}")
        return None

    try:
        # Create a Jinja2 environment for string template rendering
        env = Environment(loader=BaseLoader(), autoescape=True)

        # Render subject
        subject_template = env.from_string(template.subject)
        rendered_subject = subject_template.render(**context)

        # Render body
        body_template = env.from_string(template.body_text)
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

    class MockWorkItem:
        public_id = "TECHOPS-001"
        portfolio = MockPortfolio()

    return {
        'work_item': MockWorkItem(),
        'base_url': current_app.config.get('BASE_URL', 'https://budget.magfest.org'),
    }


def preview_template(template: EmailTemplate) -> RenderedEmail | None:
    """
    Render a template with sample data for preview.

    Args:
        template: The EmailTemplate to preview

    Returns:
        RenderedEmail with sample data rendered, or None on error
    """
    context = get_sample_context()

    try:
        env = Environment(loader=BaseLoader(), autoescape=True)

        subject_template = env.from_string(template.subject)
        rendered_subject = subject_template.render(**context)

        body_template = env.from_string(template.body_text)
        rendered_body = body_template.render(**context)

        return RenderedEmail(
            subject=rendered_subject,
            body_text=rendered_body,
            template_key=template.template_key,
        )

    except (TemplateSyntaxError, UndefinedError) as e:
        logger.error(f"Error previewing template '{template.template_key}': {e}")
        return None
