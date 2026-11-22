"""Scanners and print stations: who holds each, last seen, change station, revoke."""
from datetime import datetime, timedelta

from flask import flash, make_response, redirect, request, url_for
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app import db
from app.inventory.devices import create_enroll_code, revoke_device, set_station
from app.inventory.ledger import InventoryError
from app.inventory.people import shift_person
from app.inventory.printing import STATION_SEEN_WITHIN, local_clock, local_stamp
from app.inventory.shifts import IDLE_LIMIT
from app.models import InvDevice, InvPrinter, InvPrintJob, InvShift
from app.models.constants import (
    INV_JOB_QUEUED, INV_STATION_NAMES, INV_STATION_PRINT, INV_STATIONS,
)
from app.routes import get_user_ctx
from app.security_audit import CATEGORY_ADMIN, EVENT_CONFIG_MODIFY, log_security_event

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin
from .scan_pages import home_container_from_form


@inventory_bp.get("/admin/devices")
@require_inventory_admin
def devices():
    return _render_devices()


STALE_AFTER = timedelta(minutes=15)


def _seen_text(seen_at, now):
    if seen_at is None:
        return "never"
    age = now - seen_at
    if age < timedelta(minutes=1):
        return "just now"
    if age < timedelta(hours=1):
        return f"{int(age.total_seconds() // 60)} min ago"
    return local_stamp(seen_at)


def device_rows(now: datetime | None = None) -> list[dict]:
    """Return one row per device, active first, using a fixed number of queries.

    A shift counts as open by the rule in `shifts.current_shift`: not ended and
    active within IDLE_LIMIT. This page reads that state; it never closes a shift.
    """
    now = now or datetime.utcnow()
    devices = db.session.query(InvDevice).options(joinedload(InvDevice.home_container)) \
        .order_by(InvDevice.revoked_at.isnot(None), InvDevice.name).all()
    # Ascending id, so the newest open shift per device wins, as in `_open_shift`.
    shifts = {}
    for shift in db.session.query(InvShift).options(joinedload(InvShift.user)) \
            .filter(InvShift.ended_at.is_(None)).order_by(InvShift.id):
        if now - shift.last_activity_at <= IDLE_LIMIT:
            shifts[shift.device_id] = shift
        else:
            shifts.pop(shift.device_id, None)
    waiting = dict(db.session.query(InvPrintJob.printer_id, func.count(InvPrintJob.id))
                   .filter(InvPrintJob.status == INV_JOB_QUEUED)
                   .group_by(InvPrintJob.printer_id).all())
    printers = {}
    # printer.device needs no load option: the device query above put every device in the session.
    for printer in db.session.query(InvPrinter) \
            .filter(InvPrinter.is_active.is_(True), InvPrinter.device_id.isnot(None)) \
            .order_by(InvPrinter.name):
        seen = printer.device.last_seen_at
        printers.setdefault(printer.device_id, []).append({
            "name": printer.name,
            "online": seen is not None and now - seen <= STATION_SEEN_WITHIN,
            "waiting": waiting.get(printer.id, 0)})
    rows = []
    for device in devices:
        shift = shifts.get(device.id)
        seen = device.last_seen_at
        is_print = device.station_code == INV_STATION_PRINT
        if is_print:
            person, since = "Print station", None
        elif shift is not None:
            person, since = shift_person(shift), local_clock(shift.started_at)
        else:
            person, since = None, None
        rows.append({
            "device": device,
            "station_name": INV_STATION_NAMES.get(device.station_code, device.station_code),
            "person": person, "since": since,
            "is_print_station": is_print,
            "seen_text": _seen_text(seen, now),
            "stale": seen is None or now - seen > STALE_AFTER,
            "printers": printers.get(device.id, []) if is_print else []})
    return rows


def _render_devices(new_code=None):
    rows = device_rows()
    return render_inventory_page("inventory/devices.html",
                                 active=[r for r in rows if not r["device"].revoked_at],
                                 revoked=[r for r in rows if r["device"].revoked_at],
                                 stations=INV_STATIONS, station_names=INV_STATION_NAMES,
                                 new_code=new_code)


@inventory_bp.post("/admin/devices/codes")
@require_inventory_admin
def device_code():
    """Create an enrollment code and show it once, on this response only."""
    try:
        code = create_enroll_code(
            request.form.get("name"), get_user_ctx().user_id,
            station_code=request.form.get("station_code", ""),
            home_container=home_container_from_form())
    except InventoryError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("inventory.devices"))
    db.session.commit()
    response = make_response(_render_devices(new_code=code))
    response.headers["Cache-Control"] = "no-store"
    return response


@inventory_bp.post("/admin/devices/<int:device_id>/station")
@require_inventory_admin
def device_station(device_id: int):
    device = db.session.get(InvDevice, device_id)
    if device is None or device.revoked_at is not None:
        flash("That scanner is not active.", "error")
        return redirect(url_for("inventory.devices"))
    try:
        set_station(device, request.form.get("station_code", ""),
                    home_container=home_container_from_form(),
                    user_id=get_user_ctx().user_id)
    except InventoryError as exc:
        flash(str(exc), "error")
        return redirect(url_for("inventory.devices"))
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
