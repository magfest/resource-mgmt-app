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
    is_super_admin: Callable[[], bool]
    active_user_approval_group_ids: Callable[[], set[int]]
    can_review_group: Callable[[int], bool]
    has_super_admin_role: Callable[[], bool]  # Raw DB check, ignores role override


class _HelpersProxy:
    """Proxy that forwards attribute access to the real RouteHelpers instance.

    This exists so that `from app.routes import h` works regardless of import
    order.  Every module that imports `h` gets a reference to this same proxy
    object.  When register_all_routes() later calls ``h._set(helpers)``, the
    proxy starts delegating to the real helpers — and every module sees the
    change because the proxy object identity never changes.
    """

    def __init__(self):
        object.__setattr__(self, '_instance', None)

    def _set(self, instance: RouteHelpers):
        object.__setattr__(self, '_instance', instance)

    def __getattr__(self, name):
        inst = object.__getattribute__(self, '_instance')
        if inst is None:
            raise RuntimeError("Route helpers not initialized.")
        return getattr(inst, name)

    def __bool__(self):
        return object.__getattribute__(self, '_instance') is not None


# Global helpers proxy - populated by register_all_routes()
h: _HelpersProxy = _HelpersProxy()


@dataclass(frozen=True)
class UserContext:
    """Pre-computed permission context for the current user.

    Attributes:
        user_id: The user's ID
        user: The User database object (or None if not found)
        roles: Tuple of role codes the user has
        is_super_admin: True if user has SUPER_ADMIN role (respects beta overrides)
        approval_group_ids: Set of approval group IDs the user can review
    """
    user_id: str
    user: object | None
    roles: tuple[str, ...]
    is_super_admin: bool
    approval_group_ids: Set[int]


def _require_helpers():
    if not h:
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
        is_super_admin=h.is_super_admin(),
        approval_group_ids=set(h.active_user_approval_group_ids() or []),
    )


def _require_super_admin():
    """Abort with 403 if user is not a super admin."""
    if not h.is_super_admin():
        abort(403)


def render_page(template: str, **ctx):
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx, **ctx)


def render_admin_page(template: str, **ctx):
    _require_super_admin()
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx, **ctx)


def register_all_routes(app: Flask, helpers: RouteHelpers) -> None:
    """Register all blueprints with the Flask app."""
    h._set(helpers)

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

