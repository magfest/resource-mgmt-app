"""Shared scanners: list with last-seen time, change station, revoke."""
from flask import flash, redirect, request, url_for

from app import db
from app.inventory.devices import revoke_device, set_station
from app.inventory.ledger import InventoryError
from app.models import InvDevice
from app.models.constants import INV_STATION_NAMES, INV_STATIONS
from app.routes import get_user_ctx
from app.security_audit import CATEGORY_ADMIN, EVENT_CONFIG_MODIFY, log_security_event

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin
from .scan_pages import home_container_from_form


@inventory_bp.get("/admin/devices")
@require_inventory_admin
def devices():
    rows = db.session.query(InvDevice).order_by(InvDevice.revoked_at.isnot(None),
                                                InvDevice.name).all()
    return render_inventory_page("inventory/devices.html", devices=rows,
                                 stations=INV_STATIONS, station_names=INV_STATION_NAMES)


@inventory_bp.post("/admin/devices/<int:device_id>/station")
@require_inventory_admin
def device_station(device_id: int):
    device = db.session.get(InvDevice, device_id)
    if device is None or device.revoked_at is not None:
        flash("That scanner is not active.", "error")
        return redirect(url_for("inventory.devices"))
    try:
        set_station(device, request.form.get("station_code", ""),
                    home_container=home_container_from_form())
    except InventoryError as exc:
        flash(str(exc), "error")
        return redirect(url_for("inventory.devices"))
    log_security_event(EVENT_CONFIG_MODIFY, category=CATEGORY_ADMIN,
                       user_id=get_user_ctx().user_id,
                       details={"source": "inventory_device_station", "device_id": device.id,
                                "station_code": device.station_code})
    db.session.commit()
    flash(f"{device.name} is now a {INV_STATION_NAMES[device.station_code]} scanner.", "success")
    return redirect(url_for("inventory.devices"))


@inventory_bp.post("/admin/devices/<int:device_id>/revoke")
@require_inventory_admin
def revoke_device_route(device_id: int):
    device = db.session.get(InvDevice, device_id)
    if device is not None and device.revoked_at is None:
        user_id = get_user_ctx().user_id
        revoke_device(device, user_id)
        log_security_event(EVENT_CONFIG_MODIFY, category=CATEGORY_ADMIN, user_id=user_id,
                           details={"source": "inventory_device_revoke",
                                    "device_id": device.id, "device_name": device.name})
        db.session.commit()
    return redirect(url_for("inventory.devices"))
