"""Scanner HTML pages and device enrollment by one-time code."""
from flask import current_app, g, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError

from app import csrf, db
from app.inventory.codes import normalize_code
from app.inventory.devices import COOKIE_MAX_AGE, COOKIE_NAME, redeem_enroll_code
from app.inventory.ledger import InventoryError
from app.inventory.shifts import current_shift, recent_names
from app.models import InvContainer
from app.models.constants import INV_STATION_LOOKUP, INV_STATION_NAMES, INV_STATION_PRINT

from . import inventory_bp
from .scan_auth import require_scanner, require_scanner_or_print, who_text


STALE_FORM = "This page timed out. Type the code again."


def home_container_from_form() -> InvContainer | None:
    """Resolve home_container_code; blank means top level."""
    code = normalize_code(request.form.get("home_container_code"))
    if not code:
        return None
    container = db.session.query(InvContainer).filter_by(label_code=code).first()
    if container is None:
        raise InventoryError(f"No container has the label {code}.")
    return container


@inventory_bp.get("/scan/")
@require_scanner_or_print
def scan_home():
    principal = g.scan_principal
    if principal.is_device and principal.device.station_code == INV_STATION_PRINT:
        return redirect(url_for("inventory.print_station"))
    if principal.is_device and principal.device.station_code != INV_STATION_LOOKUP:
        shift = current_shift(principal.device)
        db.session.commit()  # keeps an IDLE close
        if shift is None:
            return redirect(url_for("inventory.scan_who"))
    if not principal.is_device or principal.device.station_code == INV_STATION_LOOKUP:
        # Look up needs no name, so a device with no open shift shows none.
        has_name = not principal.is_device or current_shift(principal.device) is not None
        who = who_text() if has_name else None
        db.session.commit()  # keeps an IDLE close
        return render_template(
            "inventory/scan/station.html", station=INV_STATION_LOOKUP,
            station_name=INV_STATION_NAMES[INV_STATION_LOOKUP], is_device=principal.is_device,
            who=who)
    who = who_text()
    db.session.commit()  # keeps an IDLE close
    return render_template("inventory/scan/index.html", principal=principal, who=who)


@inventory_bp.get("/scan/who")
@require_scanner
def scan_who():
    """Ask who is scanning. Never errors; an operator has no name to pick."""
    principal = g.scan_principal
    names = recent_names(principal.device) if principal.is_device else []
    who = who_text()
    db.session.commit()  # keeps an IDLE close that who_text made
    return render_template("inventory/scan/who.html", names=names,
                           is_device=principal.is_device, who=who)


@inventory_bp.get("/scan/enroll")
def scan_enroll():
    """Show the code form. Public: the device holds no login, and the code is the credential."""
    return render_template("inventory/scan/enroll.html", error=None)


@inventory_bp.post("/scan/enroll")
@csrf.exempt
def scan_enroll_post():
    # The app's error page sends a stale form to the login page, and no admin may
    # sign in on a shared device. This view checks the token itself and asks again.
    if current_app.config.get("WTF_CSRF_ENABLED", True):
        try:
            csrf.protect()
        except CSRFError:
            return render_template("inventory/scan/enroll.html", error=STALE_FORM), 400
    try:
        _, raw = redeem_enroll_code(request.form.get("code"))
    except InventoryError as exc:
        db.session.rollback()
        return render_template("inventory/scan/enroll.html", error=str(exc)), 400
    db.session.commit()
    response = redirect(url_for("inventory.scan_home"))
    response.set_cookie(COOKIE_NAME, raw, max_age=COOKIE_MAX_AGE, httponly=True,
                        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
                        samesite="Lax", path="/inv/scan/")
    return response


@inventory_bp.post("/scan/forget")
def scan_forget():
    """Drop a revoked device's cookie. Holds no privilege, so it needs no guard."""
    response = redirect(url_for("inventory.scan_home"))
    response.delete_cookie(COOKIE_NAME, path="/inv/scan/")
    return response
