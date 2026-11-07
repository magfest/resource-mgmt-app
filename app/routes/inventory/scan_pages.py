"""Scanner HTML pages and device enrollment."""
from flask import current_app, flash, g, redirect, render_template, request, session, url_for

from app import db
from app.inventory.codes import normalize_code
from app.inventory.devices import COOKIE_MAX_AGE, COOKIE_NAME, enroll_device
from app.inventory.ledger import InventoryError
from app.models import InvContainer
from app.models.constants import INV_STATION_LOOKUP, INV_STATION_NAMES, INV_STATIONS
from app.routes import get_user_ctx
from app.security_audit import CATEGORY_ADMIN, EVENT_CONFIG_MODIFY, log_security_event

from . import inventory_bp
from .helpers import require_inventory_admin
from .scan_auth import require_scanner


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
@require_scanner
def scan_home():
    return render_template("inventory/scan/index.html", principal=g.scan_principal)


@inventory_bp.get("/scan/enroll")
@require_inventory_admin
def scan_enroll():
    return render_template("inventory/scan/enroll.html", stations=INV_STATIONS,
                           station_names=INV_STATION_NAMES, default_station=INV_STATION_LOOKUP)


@inventory_bp.post("/scan/enroll")
@require_inventory_admin
def scan_enroll_post():
    user_id = get_user_ctx().user_id
    try:
        device, raw = enroll_device(
            request.form.get("name"), user_id,
            station_code=request.form.get("station_code") or INV_STATION_LOOKUP,
            home_container=home_container_from_form())
    except InventoryError as exc:
        flash(str(exc), "error")
        return redirect(url_for("inventory.scan_enroll"))
    log_security_event(EVENT_CONFIG_MODIFY, category=CATEGORY_ADMIN, user_id=user_id,
                       details={"source": "inventory_device_enroll",
                                "device_id": device.id, "device_name": device.name,
                                "station_code": device.station_code})
    db.session.commit()
    # Sign the admin out of this browser so the shared scanner never holds
    # an admin session.
    session.clear()
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
