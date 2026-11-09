"""Who is scanning: a shared device or a signed-in operator.

The device cookie is read here and nowhere else. Every route outside
/inv/scan/ ignores it, so a lost scanner reaches nothing but scanning.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps

from flask import g, jsonify, redirect, render_template, request, url_for

from app import csrf, db
from app.inventory.devices import COOKIE_NAME, device_for_token
from app.inventory.ledger import Actor
from app.inventory.people import shift_person
from app.inventory.shifts import NameNeeded, current_shift
from app.models import InvDevice
from app.models.constants import INV_STATION_PRINT
from app.routes import get_user_ctx

from .helpers import is_inventory_operator


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


def _refuse_scanner():
    # A device guard answers 403 itself; the app's 403 handler redirects to login.
    if request.path.startswith("/inv/scan/api/"):
        return jsonify(ok=False, enrolled=False,
                       message="This scanner is not signed in. Reload the page."), 403
    return render_template(
        "inventory/scan/not_enrolled.html",
        has_cookie=bool(request.cookies.get(COOKIE_NAME))), 403


def _scanner_guard(f, allow_print):
    @wraps(f)
    def decorated(*args, **kwargs):
        principal = current_principal()
        # A PRINT device may only collect and report print jobs.
        if principal is not None and principal.is_device and not allow_print \
                and principal.device.station_code == INV_STATION_PRINT:
            if request.path.startswith("/inv/scan/api/"):
                return jsonify(ok=False, enrolled=True,
                               message="This computer only prints labels."), 403
            return redirect(url_for("inventory.print_station"))
        if principal is None:
            return _refuse_scanner()
        if principal.is_device:
            db.session.commit()  # persists the throttled last_seen_at
        g.scan_principal = principal
        return f(*args, **kwargs)
    return decorated


def require_scanner(f):
    """Allow a signed-in operator or an enrolled device that is not a PRINT station."""
    return _scanner_guard(f, allow_print=False)


def require_scanner_or_print(f):
    """Like require_scanner, but admits a PRINT device. Only the scan landing page uses it."""
    return _scanner_guard(f, allow_print=True)


def scan_actor() -> Actor:
    """The signed-in user, or the open shift on the device. Needs require_scanner.

    A device actor carries the shift's user as well as the shift. Ownership
    checks compare the shift first, so undo stays per shift. Call it before any
    write: with no open shift it commits the close that the lookup made, then
    raises NameNeeded.
    """
    principal = g.scan_principal
    if not principal.is_device:
        return Actor(user_id=principal.user_id)
    shift = current_shift(principal.device)
    if shift is None:
        db.session.commit()  # keep the IDLE or REVOKED close the lookup just made
        raise NameNeeded("Sign in first.")
    return Actor(user_id=shift.user_id, shift_id=shift.id, device_id=principal.device.id)


def who_text() -> str:
    """Short label for the page header: who is signed in on the scanner, or the RMS user."""
    principal = g.scan_principal
    if not principal.is_device:
        user = get_user_ctx().user
        return (user.display_name if user else None) or principal.user_id
    shift = current_shift(principal.device)
    return shift_person(shift) if shift else "Not signed in"


def device_api(f):
    """Mark a JSON endpoint for enrolled devices.

    The endpoint skips the form token, which dies with a user session that a
    device never has. Requiring application/json replaces it: the device cookie
    is SameSite=Lax, and a cross-site page cannot send that type without a
    preflight this app never approves.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.mimetype != "application/json":
            return jsonify(ok=False, message="Send JSON."), 415
        return f(*args, **kwargs)
    return csrf.exempt(decorated)


def require_print_station(f):
    """Allow only an enrolled PRINT device; refuse everything else in place.

    The app's 403 handler redirects to login. A print station never reaches a
    login page, so this guard answers 403 itself.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        principal = current_principal()
        if (principal is None or not principal.is_device
                or principal.device.station_code != INV_STATION_PRINT):
            if request.path.startswith("/inv/scan/print/api/"):
                return jsonify(ok=False, message="This computer is not an enrolled print station."), 403
            return render_template(
                "inventory/scan/not_enrolled.html",
                has_cookie=bool(request.cookies.get(COOKIE_NAME))), 403
        g.scan_principal = principal
        return f(*args, **kwargs)
    return decorated
