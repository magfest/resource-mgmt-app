"""
Shared route helpers and blueprint registration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any, Set
from flask import Flask, render_template, abort

from .. import db


@dataclass
class RouteHelpers:
    ensure_demo_users: Callable[[], None]
    ensure_demo_budget_data: Callable[[], None]
    ensure_demo_org_data: Callable[[], None]
    get_active_user_id: Callable[[], str]
    get_active_user: Callable[[], Any]
    active_user_roles: Callable[[], list[str]]
    is_admin: Callable[[], bool]
    is_finance: Callable[[], bool]
    active_user_approval_group_ids: Callable[[], set[int]]
    can_review_group: Callable[[int], bool]
    real_is_admin: Callable[[], bool]  # Ignores role override


# Global helpers reference - set by register_all_routes()
h: RouteHelpers | None = None


@dataclass(frozen=True)
class UserContext:
    user_id: str
    user: object | None
    roles: tuple[str, ...]
    is_admin: bool
    is_finance: bool
    approval_group_ids: Set[int]


def _require_helpers():
    if h is None:
        raise RuntimeError("Route helpers not initialized.")


def get_user_ctx() -> UserContext:
    _require_helpers()
    uid = h.get_active_user_id()
    u = h.get_active_user()
    roles = tuple(h.active_user_roles() or [])
    return UserContext(
        user_id=uid,
        user=u,
        roles=roles,
        is_admin=h.is_admin(),
        is_finance=h.is_finance(),
        approval_group_ids=set(h.active_user_approval_group_ids() or []),
    )


def _require_admin_or_finance():
    if (not h.is_admin()) and (not h.is_finance()):
        abort(403)


def render_page(template: str, **ctx):
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx, **ctx)


def render_admin_page(template: str, **ctx):
    _require_admin_or_finance()
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx, **ctx)


def register_all_routes(app: Flask, helpers: RouteHelpers) -> None:
    """Register all blueprints with the Flask app."""
    global h
    h = helpers

    # Current/active routes
    from .home import home_bp
    from .admin import admin_config_bp
    from .admin.security_logs import security_logs_bp
    from .work import work_bp
    from .approvals import approvals_bp
    from .admin_final import admin_final_bp
    from .dispatch import dispatch_bp
    from .dev import dev_bp
    from .auth import auth_bp

    app.register_blueprint(home_bp)
    app.register_blueprint(admin_config_bp)
    app.register_blueprint(security_logs_bp)
    app.register_blueprint(work_bp)
    app.register_blueprint(approvals_bp)
    app.register_blueprint(admin_final_bp)
    app.register_blueprint(dispatch_bp)
    app.register_blueprint(dev_bp)
    app.register_blueprint(auth_bp)

