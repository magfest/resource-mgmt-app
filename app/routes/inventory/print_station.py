"""The print station page and the JSON a station polls.

The station is an enrolled PRINT device, not a user. Nothing here reads or
creates a user session, so the page keeps working past any login timeout.
"""
from datetime import datetime, timedelta

from flask import g, jsonify, render_template, request

from app import db
from app.inventory.printing import collect_jobs, render_job, report_job
from app.models import InvPrinter, InvPrintJob
from app.models.constants import INV_JOB_FAILED

from . import inventory_bp
from .scan_auth import device_api, require_print_station

BROWSER_PRINT_ORIGIN = "http://localhost:9100"
FAILURE_WINDOW = timedelta(hours=1)
CANNOT_RENDER = "This code cannot be printed as a barcode."


@inventory_bp.get("/scan/print/station")
@require_print_station
def print_station():
    g.csp_connect_src_extra = [BROWSER_PRINT_ORIGIN]
    return render_template("inventory/print_station.html")


def _printer_rows(device) -> list[dict]:
    cutoff = datetime.utcnow() - FAILURE_WINDOW
    rows = []
    printers = (db.session.query(InvPrinter)
                .filter_by(device_id=device.id, is_active=True)
                .order_by(InvPrinter.name).all())
    for printer in printers:
        last = (db.session.query(InvPrintJob).filter_by(printer_id=printer.id)
                .order_by(InvPrintJob.id.desc()).first())
        failed = (db.session.query(InvPrintJob)
                  .filter(InvPrintJob.printer_id == printer.id,
                          InvPrintJob.status == INV_JOB_FAILED,
                          InvPrintJob.finished_at >= cutoff).count())
        rows.append({
            "id": printer.id, "name": printer.name, "device_name": printer.device_name,
            "last_job": {"code": last.code, "status": last.status,
                         "error": last.error} if last else None,
            "failed": failed,
        })
    return rows


@inventory_bp.post("/scan/print/api/collect")
@device_api
@require_print_station
def print_collect():
    device = g.scan_principal.device
    try:
        jobs, out = collect_jobs(device), []
        for job in jobs:
            try:
                out.append({"id": job.id, "device_name": job.printer.device_name,
                            "zpl": render_job(job)})
            except ValueError:
                report_job(job, False, CANNOT_RENDER)
        printers = _printer_rows(device)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify(ok=False, message="The app could not collect jobs."), 500
    return jsonify(ok=True, printers=printers, jobs=out)


@inventory_bp.post("/scan/print/api/report")
@device_api
@require_print_station
def print_report():
    data = request.get_json(silent=True) or {}
    job = db.session.get(InvPrintJob, data.get("job_id")) if isinstance(
        data.get("job_id"), int) else None
    if job is None or job.printer.device_id != g.scan_principal.device.id:
        return jsonify(ok=False, message="No such job."), 404
    try:
        report_job(job, bool(data.get("ok")), str(data.get("error") or "") or None)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify(ok=False, message="The app could not record the result."), 500
    return jsonify(ok=True)
