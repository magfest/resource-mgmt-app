"""Printers, which printer takes each label type, and the print queue.

Heroku cannot reach the warehouse network, so jobs wait here until a print
station collects them. A printer record names the station and the name Browser
Print knows the printer by.
"""
import secrets

from flask import flash, redirect, request, url_for

from app import db
from app.inventory.ledger import InventoryError
from app.inventory.printing import cancel_batch, cancel_job, printer_status, request_label
from app.inventory.zpl import SUPPORTED_DPI, SUPPORTED_SIZES
from app.models import InvDevice, InvPrinter, InvPrintJob, InvPrintRoute, User
from app.models.constants import (
    INV_JOB_CLAIMED, INV_JOB_FAILED, INV_JOB_CONTAINER, INV_JOB_ITEM, INV_JOB_OWNED_UNIT,
    INV_JOB_QUEUED, INV_JOB_RENTED_UNIT, INV_JOB_SENT, INV_JOB_SOURCE_SCAN, INV_STATION_PRINT,
)

from . import inventory_bp
from .helpers import (
    admin_actor, render_inventory_page, require_inventory_admin, require_inventory_operator,
)

ROUTE_TYPES = (
    (INV_JOB_OWNED_UNIT, "Owned asset"), (INV_JOB_RENTED_UNIT, "Rented asset"),
    (INV_JOB_CONTAINER, "Location or container"), (INV_JOB_ITEM, "Item"),
)
QUEUE_LIMIT = 200
# "reprint-<job id>-<token>" must fit InvPrintJob.client_request_id, a String(64).
_TOKEN_MAX = 32


def _page_token() -> str:
    """Return the queue page's token, or a fresh one when it is missing or malformed."""
    token = (request.form.get("page_token") or "").strip()
    if token and len(token) <= _TOKEN_MAX and token.isalnum() and token.isascii():
        return token
    return secrets.token_hex(8)


def _size_value(size) -> str:
    return f"{size[0]:g}x{size[1]:g}"


SIZE_CHOICES = [(_size_value(s), f"{s[0]:g} x {s[1]:g} in") for s in sorted(SUPPORTED_SIZES)]


def _print_stations() -> list[InvDevice]:
    return (db.session.query(InvDevice)
            .filter(InvDevice.station_code == INV_STATION_PRINT, InvDevice.revoked_at.is_(None))
            .order_by(InvDevice.name).all())


def _fill_printer(printer: InvPrinter) -> None:
    """Copy the form onto the printer. Raises InventoryError on a bad value."""
    name = (request.form.get("name") or "").strip()
    if not name or len(name) > 64:
        raise InventoryError("Name the printer in 64 characters or fewer.")
    dpi = request.form.get("dpi", type=int)
    if dpi not in SUPPORTED_DPI:
        raise InventoryError(
            f"Choose a dpi of {' or '.join(str(d) for d in SUPPORTED_DPI)}.")
    size = next((s for s in SUPPORTED_SIZES if _size_value(s) == request.form.get("size")), None)
    if size is None:
        raise InventoryError("Choose one of the supported label sizes.")
    stock = (request.form.get("stock") or "").strip()
    if len(stock) > 64:
        raise InventoryError("Describe the stock in 64 characters or fewer.")
    device_name = (request.form.get("device_name") or "").strip()
    if len(device_name) > 128:
        raise InventoryError("The device name is limited to 128 characters.")
    device_id = request.form.get("device_id", type=int)
    # A printer keeps a station that was revoked since; only a change must pick an active one.
    if (device_id is not None and device_id != printer.device_id
            and device_id not in {d.id for d in _print_stations()}):
        raise InventoryError("Choose an active print station.")
    if device_id is not None:
        if not device_name:
            raise InventoryError("Enter the device name when a station is set.")
        twin = db.session.query(InvPrinter).filter(
            InvPrinter.device_id == device_id, InvPrinter.device_name == device_name)
        if printer.id is not None:
            twin = twin.filter(InvPrinter.id != printer.id)
        other = twin.first()
        if other is not None:
            raise InventoryError(
                f"{other.name} already uses that device name on this station.")
    printer.name, printer.dpi = name, dpi
    printer.width_in, printer.height_in = size
    printer.stock = stock or None
    printer.device_name = device_name or None
    printer.device_id = device_id


@inventory_bp.get("/admin/printers")
@require_inventory_admin
def printers():
    rows = db.session.query(InvPrinter).order_by(InvPrinter.is_active.desc(), InvPrinter.name).all()
    routes = {r.job_type: r.printer_id for r in db.session.query(InvPrintRoute).all()}
    return render_inventory_page(
        "inventory/printers.html", printers=rows, routes=routes, route_types=ROUTE_TYPES,
        stations=_print_stations(), sizes=SIZE_CHOICES, dpis=SUPPORTED_DPI,
        size_value=_size_value)


@inventory_bp.post("/admin/printers")
@require_inventory_admin
def save_printer():
    """Add a printer, or edit one when printer_id is posted."""
    printer_id = request.form.get("printer_id", type=int)
    printer = db.session.get(InvPrinter, printer_id) if printer_id else InvPrinter()
    if printer is None:
        flash("That printer does not exist.", "error")
        return redirect(url_for("inventory.printers"))
    try:
        _fill_printer(printer)
    except InventoryError as exc:
        flash(str(exc), "error")
        return redirect(url_for("inventory.printers"))
    if not printer_id:
        db.session.add(printer)
    db.session.commit()
    flash(f"Saved {printer.name}.", "success")
    return redirect(url_for("inventory.printers"))


@inventory_bp.post("/admin/printers/<int:printer_id>/active")
@require_inventory_admin
def printer_active(printer_id: int):
    """Deactivate or reactivate a printer. Printers are never deleted; jobs refer to them."""
    printer = db.session.get(InvPrinter, printer_id)
    if printer is not None:
        printer.is_active = request.form.get("active") == "1"
        db.session.commit()
    return redirect(url_for("inventory.printers"))


@inventory_bp.post("/admin/printers/routes")
@require_inventory_admin
def save_routes():
    """Set the printer for each label type; a blank choice clears the route."""
    existing = {r.job_type: r for r in db.session.query(InvPrintRoute).all()}
    for job_type, _ in ROUTE_TYPES:
        key = f"job_type_{job_type}"
        if key not in request.form:
            continue
        printer_id = request.form.get(key, type=int)
        route = existing.get(job_type)
        if printer_id is None:
            if route is not None:
                db.session.delete(route)
        elif db.session.get(InvPrinter, printer_id) is None:
            db.session.rollback()
            flash("That printer does not exist.", "error")
            return redirect(url_for("inventory.printers"))
        elif route is None:
            db.session.add(InvPrintRoute(job_type=job_type, printer_id=printer_id))
        else:
            route.printer_id = printer_id
    db.session.commit()
    flash("Saved the label routes.", "success")
    return redirect(url_for("inventory.printers"))


@inventory_bp.get("/admin/print-queue")
@require_inventory_operator
def print_queue():
    jobs = (db.session.query(InvPrintJob)
            .filter(InvPrintJob.status.in_((INV_JOB_QUEUED, INV_JOB_CLAIMED, INV_JOB_FAILED)))
            .order_by(InvPrintJob.created_at.desc(), InvPrintJob.id.desc())
            .limit(QUEUE_LIMIT).all())
    users = {u.id: u.display_name or u.email for u in db.session.query(User).filter(
        User.id.in_({j.performed_by_user_id for j in jobs if j.performed_by_user_id})).all()}
    printers_ = db.session.query(InvPrinter).order_by(InvPrinter.name).all()
    return render_inventory_page(
        "inventory/print_queue.html", jobs=jobs, users=users,
        statuses=[(p, printer_status(p)) for p in printers_ if p.is_active],
        queued=INV_JOB_QUEUED, failed=INV_JOB_FAILED, page_token=secrets.token_hex(8))


@inventory_bp.post("/admin/print-queue/<int:job_id>/reprint")
@require_inventory_operator
def reprint_job(job_id: int):
    """Copy a finished job into a new SCAN job, which prints ahead of batches."""
    job = db.session.get(InvPrintJob, job_id)
    if job is None:
        flash("That label does not exist.", "error")
        return redirect(url_for("inventory.print_queue"))
    if job.status not in (INV_JOB_FAILED, INV_JOB_SENT):
        flash("Only a failed or sent label can be printed again.", "error")
        return redirect(url_for("inventory.print_queue"))
    try:
        request_label(job.job_type, job.code, job.fields, admin_actor(), INV_JOB_SOURCE_SCAN,
                      printed_date=job.printed_date, unit=job.unit, container=job.container,
                      client_request_id=f"reprint-{job.id}-{_page_token()}")
    except InventoryError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("inventory.print_queue"))
    db.session.commit()
    flash(f"Queued {job.code} again.", "success")
    return redirect(url_for("inventory.print_queue"))


@inventory_bp.post("/admin/print-queue/<int:job_id>/cancel")
@require_inventory_admin
def cancel_print_job(job_id: int):
    job = db.session.get(InvPrintJob, job_id)
    if job is not None:
        try:
            cancel_job(job)
            db.session.commit()
        except InventoryError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return redirect(url_for("inventory.print_queue"))


@inventory_bp.post("/admin/print-queue/batch/<batch_id>/cancel")
@require_inventory_admin
def cancel_print_batch(batch_id: str):
    count = cancel_batch(batch_id)
    db.session.commit()
    flash(f"Cancelled {count} waiting label{'' if count == 1 else 's'}.", "success")
    return redirect(url_for("inventory.print_queue"))
