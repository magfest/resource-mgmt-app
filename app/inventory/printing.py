"""The label print queue.

Heroku cannot reach the warehouse network, so the app stores jobs and a print
station collects them. A job holds short text only; the ZPL is rendered at
collection and never stored. Nothing here commits. Times are naive UTC.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app import db
from app.inventory.ledger import Actor, InventoryError, savepoint
from app.inventory.unit_fields import details_line, model_number, spec_line
from app.inventory.zpl import LabelSpec, can_print_code, render_label
from app.models import InvPrinter, InvPrintJob, InvPrintRoute
from app.models.constants import (
    INV_CONDITION_RETIRED, INV_JOB_CANCELLED, INV_JOB_CLAIMED, INV_JOB_CONTAINER,
    INV_JOB_FAILED, INV_JOB_ITEM, INV_JOB_OWNED_UNIT, INV_JOB_QUEUED,
    INV_JOB_RENTED_UNIT, INV_JOB_SENT, INV_JOB_SOURCE_SCAN, INV_JOB_TYPES,
    INV_IDENT_FESTOPS_LABEL,
    INV_IDENT_MAGFEST_TAG, INV_IDENT_SERIAL, INV_IDENT_VENDOR_TAG,
    INV_OWNERSHIP_RENTED,
)

CLAIM_TIMEOUT = timedelta(seconds=60)
COLLECT_LIMIT = 10
STATION_SEEN_WITHIN = timedelta(seconds=15)
NO_REPORT = "No report from the print station."
_ERROR_MAX = 200

FOOTER_OWNED = "Return to MAGFest FestOps"
FOOTER_RENTED = "Rented asset. Return to MAGFest FestOps"
# The printed date is the event's local date; the server clock is UTC.
INV_LOCAL_TZ = "America/New_York"


def local_today() -> date:
    return datetime.now(ZoneInfo(INV_LOCAL_TZ)).date()


def _active_value(unit, kind: str) -> str | None:
    for ident in unit.identifiers:
        if ident.kind == kind and ident.retired_at is None:
            return ident.value
    return None


def local_stamp(moment: datetime) -> str:
    """Format a stored UTC time as `Nov 7, 9:23 PM` in INV_LOCAL_TZ."""
    local = moment.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(INV_LOCAL_TZ))
    return f"{local:%b} {local.day}, {local.hour % 12 or 12}:{local:%M} {local:%p}"


def local_day(moment: datetime) -> str:
    """Format a stored UTC time as `Nov 7` in INV_LOCAL_TZ."""
    return local_stamp(moment).split(",", 1)[0]


def local_when(moment: datetime) -> str:
    """Format a stored UTC time as `Nov 7 at 5:53 PM` in INV_LOCAL_TZ."""
    return local_stamp(moment).replace(", ", " at ", 1)


def local_iso(moment: datetime) -> str:
    """Format a stored UTC time as sortable `2026-12-07 21:30` in INV_LOCAL_TZ."""
    local = moment.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(INV_LOCAL_TZ))
    return f"{local:%Y-%m-%d %H:%M}"


def local_clock(moment: datetime) -> str:
    """Format a stored UTC time as 24-hour `15:05` in INV_LOCAL_TZ."""
    local = moment.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(INV_LOCAL_TZ))
    return f"{local:%H:%M}"


def unit_number(unit) -> str | None:
    """Return the number printed on a unit: its MAGFest tag, else its FestOps label.

    Retired identifiers are skipped; a corrected tag must not print again.
    """
    return (_active_value(unit, INV_IDENT_MAGFEST_TAG)
            or _active_value(unit, INV_IDENT_FESTOPS_LABEL))


def fields_for_unit(unit) -> dict:
    """Return the label title, rows and footer for a unit. Blank rows are left out.

    An unnamed unit has no `title` or `spec` key, so its stored jobs match fields built
    before names existed. A named unit prints its type and specs in `spec`, not a row.
    """
    rows = []
    if unit.name:
        model = model_number(unit)
        if model:
            rows.append(["Model", model])
    else:
        rows.append(["Item", unit.item.item_name])
        details = details_line(unit)
        if details:
            rows.append(["Details", details])
    rented = unit.ownership == INV_OWNERSHIP_RENTED
    tag = _active_value(unit, INV_IDENT_VENDOR_TAG) if rented else None
    if tag:
        rows.append(["Vendor tag", f"{unit.vendor_name}: {tag}" if unit.vendor_name else tag])
    serial = _active_value(unit, INV_IDENT_SERIAL)
    if serial:
        rows.append(["Serial", serial])
    fields = {"rows": rows, "footer": FOOTER_RENTED if rented else FOOTER_OWNED}
    if unit.name:
        fields["title"] = unit.name
        fields["spec"] = spec_line(unit)
    return fields


def fields_for_container(container) -> dict:
    rows = [["Kind", container.kind.name]]
    if container.name:
        rows.append(["Name", container.name])
    return {"rows": rows, "footer": None}


def fields_for_item(item) -> dict:
    return {"rows": [["Item", item.item_name]], "footer": None}


def label_spec(code: str, fields: dict, printed_date: date | None) -> LabelSpec:
    """Build the label for stored fields. A job without `spec` prints a Printed row."""
    rows = [tuple(row) for row in fields.get("rows", [])]
    spec_text = fields.get("spec")
    last_date = None
    if spec_text is not None:
        # The date shares the spec line; a separate Printed row would not fit.
        last_date = f"{printed_date:%Y/%m/%d}" if printed_date else None
    elif printed_date:
        rows.append(("Printed", printed_date.isoformat()))
    return LabelSpec(code, tuple(rows), fields.get("footer"), fields.get("title"),
                     spec_text, last_date)


def render_job(job: InvPrintJob) -> str:
    """Render a queued job as ZPL for its printer."""
    printer = job.printer
    spec = label_spec(job.code, job.fields, job.printed_date)
    return render_label(spec, printer.dpi, float(printer.width_in), float(printer.height_in))


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
        return existing
    return job


def _by_request_id(client_request_id: str) -> InvPrintJob | None:
    return db.session.query(InvPrintJob).filter_by(
        client_request_id=client_request_id).first()


def collect_jobs(device, now: datetime | None = None,
                 skip_printer_ids=()) -> list[InvPrintJob]:
    """Claim up to COLLECT_LIMIT queued jobs for a station's active printers.

    Printers in skip_printer_ids still have sends in flight and claim nothing.

    A claim with no report after CLAIM_TIMEOUT becomes FAILED. It is never
    requeued: the label may have printed, and a duplicate surprises a person.
    SCAN jobs go ahead of BATCH jobs, oldest first within each.
    """
    now = now or datetime.utcnow()
    device.last_seen_at = now
    printers = db.session.query(InvPrinter).filter_by(device_id=device.id).all()
    if not printers:
        return []
    # The sweep includes inactive printers, or a claimed job on one stays claimed forever.
    stale = (db.session.query(InvPrintJob)
             .filter(InvPrintJob.printer_id.in_([p.id for p in printers]),
                     InvPrintJob.status == INV_JOB_CLAIMED,
                     InvPrintJob.claimed_at <= now - CLAIM_TIMEOUT)
             .with_for_update().all())
    for job in stale:
        job.status = INV_JOB_FAILED
        job.error = NO_REPORT
        job.finished_at = now
    # BATCH sorts before SCAN alphabetically, so the order cannot come from the column.
    skip = set(skip_printer_ids)
    claimable = [p.id for p in printers if p.is_active and p.id not in skip]
    if not claimable:
        db.session.flush()
        return []
    scan_first = db.case((InvPrintJob.source == INV_JOB_SOURCE_SCAN, 0), else_=1)
    jobs = (db.session.query(InvPrintJob)
            .filter(InvPrintJob.printer_id.in_(claimable),
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


def queue_summary(now: datetime | None = None) -> dict:
    """Sum waiting labels over active printers and say whether every station is polling.

    `station_online` is False when any active printer's station is stale, because that
    printer would leave labels sitting. With no active printers it is True.
    """
    printers = db.session.query(InvPrinter).filter(InvPrinter.is_active.is_(True)).all()
    ids = [p.id for p in printers]
    waiting = (db.session.query(InvPrintJob)
               .filter(InvPrintJob.printer_id.in_(ids),
                       InvPrintJob.status == INV_JOB_QUEUED).count()) if ids else 0
    online = all(printer_status(p, now)["station_seen"] for p in printers)
    return {"waiting": waiting, "station_online": online}


REPRINT_ORIGINAL_DATE = "ORIGINAL_DATE"
REPRINT_TODAY = "TODAY"
REPRINT_CURRENT_DETAILS = "CURRENT_DETAILS"
REPRINT_MODES = (REPRINT_ORIGINAL_DATE, REPRINT_TODAY, REPRINT_CURRENT_DETAILS)
NUMBER_CHANGED = "The number on this asset changed. Print its current details."
NO_HISTORY = "This asset has no earlier label. Print its current details."


def _unit_jobs(unit=None, container=None):
    """Return the uncancelled jobs for a unit or container as a query, or None."""
    if unit is not None:
        query = db.session.query(InvPrintJob).filter_by(unit_id=unit.id)
    elif container is not None:
        query = db.session.query(InvPrintJob).filter_by(container_id=container.id)
    else:
        return None
    return query.filter(InvPrintJob.status != INV_JOB_CANCELLED)


def last_job_for(unit=None, container=None) -> InvPrintJob | None:
    """Return the newest job for a unit or container that is not cancelled.

    A job with no rows and no title is a blank reserved label; it is not history and
    is skipped. A named unit with no model or serial has a title and no rows. The test
    runs in Python because JSON containment is not portable between SQLite and PostgreSQL.
    """
    query = _unit_jobs(unit, container)
    if query is None:
        return None
    for job in query.order_by(InvPrintJob.id.desc()):
        fields = job.fields or {}
        if fields.get("rows") or fields.get("title"):
            return job
    return None


def original_job_for(unit) -> InvPrintJob | None:
    """Return the earliest uncancelled job with a date whose code is the unit's number now."""
    number = unit_number(unit)
    if number is None:
        return None
    return (_unit_jobs(unit=unit)
            .filter(InvPrintJob.printed_date.isnot(None), InvPrintJob.code == number)
            .order_by(InvPrintJob.id).first())


def reprint_options(unit) -> dict:
    """Say what a unit's label screen offers.

    `quick_reprint` is false when the last job's code is no longer the unit's number,
    because that barcode is retired. `details_changed` compares the last job with the unit now.
    """
    job = last_job_for(unit=unit)
    if job is None:
        return {"has_history": False, "details_changed": False, "original_date": None,
                "quick_reprint": False}
    same_code = job.code == unit_number(unit)
    original = original_job_for(unit)
    return {"has_history": True,
            "details_changed": not same_code or job.fields != fields_for_unit(unit),
            "original_date": original.printed_date.isoformat() if original else None,
            "quick_reprint": same_code}


def _unit_job_type(unit) -> str:
    return INV_JOB_RENTED_UNIT if unit.ownership == INV_OWNERSHIP_RENTED else INV_JOB_OWNED_UNIT


def reprint(unit, mode, actor: Actor, client_request_id=None) -> InvPrintJob:
    """Queue a unit's label again.

    ORIGINAL_DATE and TODAY copy the last job's text and code;
    ORIGINAL_DATE dates the label from the earliest job that carries the current number; they refuse when the
    unit has no history or its number changed. CURRENT_DETAILS prints the unit as it is today.
    """
    if mode not in REPRINT_MODES:
        raise InventoryError("Pick how to print the label.")
    if unit is None or unit.condition == INV_CONDITION_RETIRED:
        raise InventoryError("That asset is not in use, so it has no label to print.")
    number = unit_number(unit)
    if number is None:
        raise InventoryError("This asset has no number to print.")
    if mode == REPRINT_CURRENT_DETAILS:
        fields, code, printed = fields_for_unit(unit), number, local_today()
    else:
        job = last_job_for(unit=unit)
        if job is None:
            raise InventoryError(NO_HISTORY)
        if job.code != number:
            raise InventoryError(NUMBER_CHANGED)
        fields, code = dict(job.fields), job.code
        if mode == REPRINT_ORIGINAL_DATE:
            original = original_job_for(unit)
            printed = (original.printed_date if original
                       else job.printed_date or local_today())
        else:
            printed = local_today()
    return request_label(_unit_job_type(unit), code, fields, actor, INV_JOB_SOURCE_SCAN,
                         printed_date=printed, unit=unit, client_request_id=client_request_id)


def print_container(container, actor: Actor, client_request_id=None) -> InvPrintJob:
    """Queue a container label from its current name and kind. It carries no date."""
    if not can_print_code(container.label_code):
        raise InventoryError("This number cannot be printed as a barcode.")
    return request_label(INV_JOB_CONTAINER, container.label_code, fields_for_container(container),
                         actor, INV_JOB_SOURCE_SCAN, container=container,
                         client_request_id=client_request_id)


def print_item(item, actor: Actor, client_request_id=None) -> InvPrintJob:
    """Queue an item-type label for the item's barcode. It carries no date."""
    if not item.barcode:
        raise InventoryError("This item has no barcode to print.")
    if not can_print_code(item.barcode):
        raise InventoryError("This number cannot be printed as a barcode.")
    return request_label(INV_JOB_ITEM, item.barcode, fields_for_item(item), actor,
                         INV_JOB_SOURCE_SCAN, client_request_id=client_request_id)


def job_answer(job: InvPrintJob) -> dict:
    """The scanner's view of a queued label: its code and whether the printer is keeping up."""
    status = printer_status(job.printer)
    return {"code": job.code, "unit_id": job.unit_id, "job_id": job.id,
            "station_seen": status["station_seen"], "waiting": status["waiting"]}
