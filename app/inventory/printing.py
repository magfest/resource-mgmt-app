"""The label print queue.

Heroku cannot reach the warehouse network, so the app stores jobs and a print
station collects them. A job holds short text only; the ZPL is rendered at
collection and never stored. Nothing here commits. Times are naive UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app import db
from app.inventory.ledger import Actor, InventoryError, savepoint
from app.models import InvPrinter, InvPrintJob, InvPrintRoute
from app.models.constants import (
    INV_JOB_CANCELLED, INV_JOB_CLAIMED, INV_JOB_FAILED, INV_JOB_QUEUED,
    INV_JOB_SENT, INV_JOB_SOURCE_SCAN, INV_JOB_TYPES,
)

CLAIM_TIMEOUT = timedelta(seconds=60)
COLLECT_LIMIT = 10
STATION_SEEN_WITHIN = timedelta(seconds=15)
NO_REPORT = "No report from the print station."
_ERROR_MAX = 200


def printer_for(job_type: str) -> InvPrinter:
    """Return the active printer routed for a job type."""
    route = db.session.query(InvPrintRoute).filter_by(job_type=job_type).first()
    if route is None or not route.printer.is_active:
        raise InventoryError("No printer is set for this label. Ask an inventory admin.")
    return route.printer


def request_label(job_type, code, fields, actor: Actor, source, printed_date=None,
                  unit=None, container=None, batch_id=None,
                  client_request_id=None) -> InvPrintJob:
    """Queue one label. A repeated client_request_id returns the first job."""
    if job_type not in INV_JOB_TYPES:
        raise InventoryError(f"{job_type or 'That'} is not a label type.")
    if client_request_id:
        existing = _by_request_id(client_request_id)
        if existing is not None:
            return existing
    printer = printer_for(job_type)
    job = InvPrintJob(
        printer_id=printer.id, job_type=job_type, code=code, fields=fields,
        printed_date=printed_date, source=source, batch_id=batch_id,
        status=INV_JOB_QUEUED, client_request_id=client_request_id,
        unit_id=unit.id if unit else None,
        container_id=container.id if container else None,
        performed_by_user_id=actor.user_id, shift_id=actor.shift_id,
        device_id=actor.device_id,
    )
    try:
        with savepoint():
            db.session.add(job)
            db.session.flush()
    except IntegrityError:
        # A concurrent retry won the unique constraint; return its job.
        existing = _by_request_id(client_request_id) if client_request_id else None
        if existing is None:
            raise
        db.session.expunge(job)
        return existing
    return job


def _by_request_id(client_request_id: str) -> InvPrintJob | None:
    return db.session.query(InvPrintJob).filter_by(
        client_request_id=client_request_id).first()


def collect_jobs(device, now: datetime | None = None) -> list[InvPrintJob]:
    """Claim up to COLLECT_LIMIT queued jobs for a station's active printers.

    A claim with no report after CLAIM_TIMEOUT becomes FAILED. It is never
    requeued: the label may have printed, and a duplicate surprises a person.
    SCAN jobs go ahead of BATCH jobs, oldest first within each.
    """
    now = now or datetime.utcnow()
    device.last_seen_at = now
    printer_ids = [p.id for p in db.session.query(InvPrinter).filter_by(
        device_id=device.id, is_active=True)]
    if not printer_ids:
        return []
    stale = (db.session.query(InvPrintJob)
             .filter(InvPrintJob.printer_id.in_(printer_ids),
                     InvPrintJob.status == INV_JOB_CLAIMED,
                     InvPrintJob.claimed_at <= now - CLAIM_TIMEOUT)
             .with_for_update().all())
    for job in stale:
        job.status = INV_JOB_FAILED
        job.error = NO_REPORT
        job.finished_at = now
    # BATCH sorts before SCAN alphabetically, so the order cannot come from the column.
    scan_first = db.case((InvPrintJob.source == INV_JOB_SOURCE_SCAN, 0), else_=1)
    jobs = (db.session.query(InvPrintJob)
            .filter(InvPrintJob.printer_id.in_(printer_ids),
                    InvPrintJob.status == INV_JOB_QUEUED)
            .order_by(scan_first, InvPrintJob.created_at, InvPrintJob.id)
            .limit(COLLECT_LIMIT).with_for_update().populate_existing().all())
    for job in jobs:
        job.status = INV_JOB_CLAIMED
        job.claimed_at = now
    db.session.flush()
    return jobs


def report_job(job: InvPrintJob, ok: bool, error: str | None = None,
               now: datetime | None = None) -> None:
    """Record the station's result. A job that is not CLAIMED is left alone."""
    if job.status != INV_JOB_CLAIMED:
        return
    job.status = INV_JOB_SENT if ok else INV_JOB_FAILED
    job.error = None if ok else (error or "The printer refused the label.")[:_ERROR_MAX]
    job.finished_at = now or datetime.utcnow()
    db.session.flush()


def cancel_job(job: InvPrintJob) -> None:
    """Cancel a queued job. Anything already collected cannot be recalled."""
    if job.status != INV_JOB_QUEUED:
        raise InventoryError("Only a waiting label can be cancelled.")
    job.status = INV_JOB_CANCELLED
    job.finished_at = datetime.utcnow()
    db.session.flush()


def cancel_batch(batch_id: str) -> int:
    """Cancel the queued jobs of a batch and return how many."""
    jobs = (db.session.query(InvPrintJob)
            .filter_by(batch_id=batch_id, status=INV_JOB_QUEUED)
            .with_for_update().all())
    now = datetime.utcnow()
    for job in jobs:
        job.status = INV_JOB_CANCELLED
        job.finished_at = now
    db.session.flush()
    return len(jobs)


def printer_status(printer: InvPrinter, now: datetime | None = None) -> dict:
    """Report whether the printer's station is polling and how many jobs wait."""
    now = now or datetime.utcnow()
    seen_at = printer.device.last_seen_at if printer.device else None
    waiting = db.session.query(InvPrintJob).filter_by(
        printer_id=printer.id, status=INV_JOB_QUEUED).count()
    return {"station_seen": seen_at is not None and now - seen_at <= STATION_SEEN_WITHIN,
            "waiting": waiting}
