"""Scanner HTML pages and device enrollment by one-time code."""
from flask import current_app, g, redirect, render_template, request, url_for

from app import db
from app.inventory.codes import normalize_code
from app.inventory.devices import COOKIE_MAX_AGE, COOKIE_NAME, redeem_enroll_code
from app.inventory.ledger import InventoryError
from app.models import InvContainer

from . import inventory_bp
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
def scan_enroll():
    """Public on purpose: the device holds no login, and the code is the credential."""
    return render_template("inventory/scan/enroll.html", error=None)


@inventory_bp.post("/scan/enroll")
def scan_enroll_post():
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
