"""
Shared helpers for admin configuration routes.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import wraps
from typing import Any

from flask import abort, render_template, flash, request

from app import db
from app.models import (
    ROLE_SUPER_ADMIN,
    ConfigAuditEvent,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
    CONFIG_AUDIT_ARCHIVE,
    CONFIG_AUDIT_RESTORE,
)
from app.routes import h, get_user_ctx


def require_super_admin(f):
    """Decorator to require SUPER_ADMIN role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_ctx = get_user_ctx()
        if ROLE_SUPER_ADMIN not in user_ctx.roles:
            abort(403, "Super admin access required")
        return f(*args, **kwargs)
    return decorated_function


def render_admin_config_page(template: str, **ctx):
    """Render an admin config page with user context."""
    user_ctx = get_user_ctx()
    if ROLE_SUPER_ADMIN not in user_ctx.roles:
        abort(403, "Super admin access required")
    return render_template(template, user_ctx=user_ctx, **ctx)


def log_config_change(
    entity_type: str,
    entity_id: int,
    action: str,
    changes: dict[str, Any] | None = None,
    user_id: str | None = None,
):
    """
    Log a configuration change to the audit table.

    Args:
        entity_type: Type of entity (expense_account, approval_group, etc.)
        entity_id: ID of the entity
        action: Action performed (CREATE, UPDATE, ARCHIVE, RESTORE)
        changes: Dictionary of changes (for UPDATE actions)
        user_id: User who made the change (defaults to current user)
    """
    if user_id is None:
        user_id = h.get_active_user_id()

    changes_json = json.dumps(changes) if changes else None

    event = ConfigAuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changes_json=changes_json,
        created_by_user_id=user_id,
    )
    db.session.add(event)


def track_changes(old_values: dict, new_values: dict) -> dict:
    """
    Compare old and new values and return a dict of changes.

    Returns:
        Dict with keys that changed, containing {"old": ..., "new": ...}
    """
    changes = {}
    all_keys = set(old_values.keys()) | set(new_values.keys())

    for key in all_keys:
        old_val = old_values.get(key)
        new_val = new_values.get(key)

        # Normalize None vs empty string
        if old_val == "":
            old_val = None
        if new_val == "":
            new_val = None

        if old_val != new_val:
            changes[key] = {"old": old_val, "new": new_val}

    return changes


def flash_errors(form_errors: dict[str, list[str]]):
    """Flash form validation errors."""
    for field, errors in form_errors.items():
        for error in errors:
            flash(f"{field}: {error}", "error")
