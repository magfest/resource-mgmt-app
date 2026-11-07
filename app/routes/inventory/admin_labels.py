"""Print new label batches and reprint existing codes as ZPL.

The app renders ZPL; the print-station laptop delivers it. Browser Print, a
Zebra agent on localhost, is used when present. Otherwise the operator
downloads a .zpl file and sends it with Zebra Setup Utilities. Heroku cannot
reach the warehouse LAN, so the server never talks to a printer.
"""
from dataclasses import dataclass
import secrets

from flask import flash, g, redirect, request, url_for

from app import db
from app.inventory.codes import normalize_code
from app.inventory.labels import MAX_BATCH, PREFIX_CONTAINER, PREFIX_ITEM, PREFIX_UNIT, allocate, parse, was_issued
from app.inventory.printing import (
    fields_for_container, fields_for_item, fields_for_unit, local_today, request_label,
    unit_number,
)
from app.inventory.zpl import SUPPORTED_DPI, LabelSpec, can_print_code, render_batch
from app.models import InvContainer, InvUnitIdentifier, SupplyItem
from app.inventory.ledger import InventoryError
from app.models.constants import (
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAGFEST_TAG, INV_JOB_CONTAINER, INV_JOB_ITEM,
    INV_JOB_OWNED_UNIT, INV_JOB_RENTED_UNIT, INV_JOB_SOURCE_BATCH, INV_OWNERSHIP_RENTED,
)

from . import inventory_bp
from .helpers import (
    admin_actor, render_inventory_page, require_inventory_admin, require_inventory_operator,
)

BROWSER_PRINT_ORIGINS = ["http://localhost:9100", "https://localhost:9101"]


def _dpi() -> int:
    dpi = request.form.get("dpi", type=int)
    return dpi if dpi in SUPPORTED_DPI else 300


@dataclass
class _Found:
    """One code resolved to what a label needs, with the record behind it if any."""
    code: str
    job_type: str
    fields: dict
    printed: bool = False
    unit: object = None
    container: object = None

    def spec(self) -> LabelSpec:
        rows = [tuple(row) for row in self.fields["rows"]]
        if self.printed:
            rows.append(("Printed", local_today().isoformat()))
        return LabelSpec(self.code, tuple(rows), self.fields["footer"])


def _find(code: str) -> _Found | None:
    """Return the label for an existing code, or None when nothing holds it."""
    parsed = parse(code)
    if parsed is not None and parsed[0] == PREFIX_CONTAINER:
        c = db.session.query(InvContainer).filter_by(label_code=code).first()
        return _Found(code, INV_JOB_CONTAINER, fields_for_container(c), container=c) if c else None
    if parsed is not None and parsed[0] == PREFIX_ITEM:
        item = db.session.query(SupplyItem).filter_by(barcode=code).first()
        return _Found(code, INV_JOB_ITEM, fields_for_item(item)) if item else None
    ident = db.session.query(InvUnitIdentifier).filter(
        InvUnitIdentifier.kind.in_((INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAGFEST_TAG)),
        InvUnitIdentifier.normalized_value == code,
        InvUnitIdentifier.retired_at.is_(None)).first()
    if ident:
        unit = ident.unit
        job_type = (INV_JOB_RENTED_UNIT if unit.ownership == INV_OWNERSHIP_RENTED
                    else INV_JOB_OWNED_UNIT)
        return _Found(unit_number(unit) or code, job_type, fields_for_unit(unit),
                      printed=True, unit=unit)
    return None


_BLANK = {"rows": [], "footer": None}
_JOB_TYPE_BY_PREFIX = {PREFIX_UNIT: INV_JOB_OWNED_UNIT, PREFIX_CONTAINER: INV_JOB_CONTAINER,
                       PREFIX_ITEM: INV_JOB_ITEM}


def _find_or_reserved(code: str) -> _Found | None:
    """Like _find, but a reserved code with no record yet gets a blank label."""
    found = _find(code)
    if found is not None or not was_issued(code):
        return found
    return _Found(code, _JOB_TYPE_BY_PREFIX[parse(code)[0]], dict(_BLANK))


def _spec_for(code: str) -> LabelSpec | None:
    found = _find(code)
    return found.spec() if found else None


def _sort_codes(raw: str, find) -> tuple[list[_Found], list[str], list[str]]:
    """Sort typed codes into labels, codes with no record, and codes no barcode can hold.

    Tags have no fixed format, so one long tag must not stop the rest of a batch.
    """
    found, unknown, unprintable = [], [], []
    for line in (raw or "").splitlines():
        code = normalize_code(line)
        if not code:
            continue
        hit = find(code)
        if hit is None:
            unknown.append(code)
        elif can_print_code(hit.code):
            found.append(hit)
        else:
            unprintable.append(hit.code)
    return found[:MAX_BATCH], unknown, unprintable


def _specs_from_codes(raw: str) -> tuple[list[LabelSpec], list[str], list[str]]:
    found, unknown, unprintable = _sort_codes(raw, _find)
    return [f.spec() for f in found], unknown, unprintable


def _batch_page(specs, dpi, unknown=(), unprintable=()):
    g.csp_connect_src_extra = BROWSER_PRINT_ORIGINS
    return render_inventory_page("inventory/label_batch.html", specs=specs, dpi=dpi,
                                 zpl=render_batch(specs, dpi), unknown=unknown,
                                 unprintable=unprintable)


@inventory_bp.get("/admin/labels")
@require_inventory_admin
def labels():
    g.csp_connect_src_extra = BROWSER_PRINT_ORIGINS
    return render_inventory_page("inventory/labels.html", max_batch=MAX_BATCH,
                                 prefill=request.args.get("codes", ""))


@inventory_bp.post("/admin/labels/new")
@require_inventory_admin
def new_labels():
    prefix = request.form.get("prefix")
    count = request.form.get("count", type=int) or 0
    if prefix not in (PREFIX_CONTAINER, PREFIX_UNIT, PREFIX_ITEM) or not 1 <= count <= MAX_BATCH:
        flash(f"Choose a label type and 1 to {MAX_BATCH} labels.", "error")
        return redirect(url_for("inventory.labels"))
    codes = allocate(prefix, count)
    db.session.commit()
    return _batch_page([LabelSpec(c, (), None) for c in codes], _dpi())


@inventory_bp.post("/admin/labels/reprint")
@require_inventory_admin
def reprint_labels():
    specs, unknown, unprintable = _specs_from_codes(request.form.get("codes"))
    return _batch_page(specs, _dpi(), unknown, unprintable)


@inventory_bp.post("/admin/labels/queue")
@require_inventory_operator
def queue_labels():
    """Queue one print job per code. A missing route queues nothing."""
    found, unknown, unprintable = _sort_codes(request.form.get("codes"), _find_or_reserved)
    if unknown:
        flash(f"Not queued, these codes were never issued or no record holds them: "
              f"{', '.join(unknown)}", "warning")
    if unprintable:
        flash(f"Not queued, a barcode cannot hold these codes: {', '.join(unprintable)}",
              "warning")
    if not found:
        flash("Nothing to queue.", "error")
        return redirect(url_for("inventory.print_queue"))
    batch_id = secrets.token_hex(8)
    actor = admin_actor()
    try:
        for hit in found:
            request_label(hit.job_type, hit.code, hit.fields, actor, INV_JOB_SOURCE_BATCH,
                          printed_date=local_today() if hit.unit else None,
                          unit=hit.unit, container=hit.container, batch_id=batch_id)
    except InventoryError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        # Show the batch again; a reserved code has no record to reprint it from.
        return _batch_page([f.spec() for f in found], _dpi(), unknown, unprintable)
    db.session.commit()
    flash(f"Sent {len(found)} label{'' if len(found) == 1 else 's'} to the print queue.",
          "success")
    return redirect(url_for("inventory.print_queue"))
