"""The print station page and the JSON a station polls.

The station is an enrolled PRINT device, not a user. Nothing here reads or
creates a user session, and polling never renews one, so the page keeps working
past any login timeout.
"""
from datetime import datetime, timedelta

from flask import g, jsonify, render_template, request

from app import db
from app.inventory.printing import collect_jobs, render_job, report_job
from app.models import InvPrinter, InvPrintJob
from app.models.constants import INV_JOB_FAILED, INV_JOB_SIGNIN_CARD

from . import inventory_bp
from .scan_auth import device_api, require_print_station

BROWSER_PRINT_ORIGIN = "http://localhost:9100"
FAILURE_WINDOW = timedelta(hours=1)
RENDER_FAILED = "This label could not be rendered."
MAX_JOB_ID = 2**63 - 1


def _skip_ids(data) -> list[int]:
    """Return the printer ids a station says are still sending; ignore anything else."""
    raw = data.get("skip_printer_ids") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [v for v in raw if isinstance(v, int) and not isinstance(v, bool)]


def _refuse():
    # 409, not 404: the app's flood blocker counts 404s per IP.
    return jsonify(ok=False, message="No such job."), 409


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
            "last_job": {"code": ("Sign-in card" if last.job_type == INV_JOB_SIGNIN_CARD
                                  else last.code), "status": last.status,
                         "error": last.error} if last else None,
            "failed": failed,
        })
    return rows


@inventory_bp.post("/scan/print/api/collect")
@device_api
@require_print_station
def print_collect():
    device = g.scan_principal.device
    skip = _skip_ids(request.get_json(silent=True))
    try:
        jobs, out = collect_jobs(device, skip_printer_ids=skip), []
        for job in jobs:
            try:
                out.append({"id": job.id, "printer_id": job.printer_id,
                            "device_name": job.printer.device_name,
                            "zpl": render_job(job)})
            except ValueError as exc:
                report_job(job, False, str(exc))
            except Exception:
                report_job(job, False, RENDER_FAILED)
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
    data = request.get_json(silent=True)
    job_id = data.get("job_id") if isinstance(data, dict) else None
    if (not isinstance(job_id, int) or isinstance(job_id, bool)
            or not 1 <= job_id <= MAX_JOB_ID):
        return _refuse()
    job = db.session.get(InvPrintJob, job_id)
    if job is None or job.printer.device_id != g.scan_principal.device.id:
        return _refuse()
    try:
        report_job(job, data.get("ok") is True, str(data.get("error") or "") or None)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify(ok=False, message="The app could not record the result."), 500
    return jsonify(ok=True)
