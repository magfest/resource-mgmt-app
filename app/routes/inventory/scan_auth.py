"""Who is scanning: a shared device or a signed-in operator.

The device cookie is read here and nowhere else. Every route outside
/inv/scan/ ignores it, so a lost scanner reaches nothing but scanning.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps

from flask import g, jsonify, render_template, request

from app import db
from app.inventory.devices import COOKIE_NAME, device_for_token
from app.models import InvDevice
from app.routes import get_user_ctx

from .helpers import is_inventory_admin, is_inventory_operator


@dataclass
class ScanPrincipal:
    user_id: str | None
    device: InvDevice | None

    @property
    def is_device(self) -> bool:
        return self.device is not None


def current_principal() -> ScanPrincipal | None:
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        # A revoked or unknown cookie never falls back to a user session in
        # the same browser; the page offers Forget instead.
        device = device_for_token(raw)
        return ScanPrincipal(None, device) if device else None
    user_ctx = get_user_ctx()
    if user_ctx.user_id and is_inventory_operator(user_ctx):
        return ScanPrincipal(user_ctx.user_id, None)
    return None


def require_scanner(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        principal = current_principal()
        if principal is None:
            if request.path.startswith("/inv/scan/api/"):
                return jsonify(ok=False, message="This scanner is not signed in. Reload the page."), 403
            user_ctx = get_user_ctx()
            return render_template(
                "inventory/scan/not_enrolled.html",
                has_cookie=bool(request.cookies.get(COOKIE_NAME)),
                can_enroll=bool(user_ctx.user_id) and is_inventory_admin(user_ctx)), 403
        if principal.is_device:
            db.session.commit()  # persists the throttled last_seen_at
        g.scan_principal = principal
        return f(*args, **kwargs)
    return decorated
