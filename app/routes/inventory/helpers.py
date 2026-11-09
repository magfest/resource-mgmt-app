"""Role checks and render helpers for inventory pages."""
from __future__ import annotations

from functools import wraps

from flask import abort, current_app, redirect, render_template, session, url_for

from app.inventory.item_rights import item_rights
from app.inventory.ledger import Actor
from app.models.constants import INV_NOTE_TYPES
from app.models import ROLE_INVENTORY_ADMIN, ROLE_INVENTORY_OPERATOR
from app.routes import get_user_ctx


def is_inventory_admin(user_ctx) -> bool:
    if user_ctx.is_super_admin:
        return True
    return ROLE_INVENTORY_ADMIN in user_ctx.roles


def is_inventory_operator(user_ctx) -> bool:
    """Admins operate scanners too."""
    return is_inventory_admin(user_ctx) or ROLE_INVENTORY_OPERATOR in user_ctx.roles


def note_types_for(user_ctx) -> tuple[str, ...]:
    """Note types this user may write; empty for anyone who is not an operator."""
    return INV_NOTE_TYPES if is_inventory_operator(user_ctx) else ()


def _guard(check, message):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("active_user_id") and not current_app.config.get("DEV_LOGIN_ENABLED"):
                return redirect(url_for("auth.login_page"))
            user_ctx = get_user_ctx()
            if user_ctx.user_id is None:
                return redirect(url_for("auth.login_page"))
            if not check(user_ctx):
                abort(403, message)
            return f(*args, **kwargs)
        return decorated
    return decorator


require_inventory_admin = _guard(is_inventory_admin, "Inventory admin access required")
require_inventory_operator = _guard(is_inventory_operator, "Inventory access required")
require_item_viewer = _guard(lambda u: item_rights(u).can_view, "Item access required")


def render_inventory_page(template: str, **ctx):
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx,
                           is_inv_admin=is_inventory_admin(user_ctx), **ctx)


def admin_actor() -> Actor:
    return Actor(user_id=get_user_ctx().user_id)
