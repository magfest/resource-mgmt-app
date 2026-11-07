"""Print new label batches and reprint existing codes as ZPL.

The app renders ZPL; the print-station laptop delivers it. Browser Print, a
Zebra agent on localhost, is used when present. Otherwise the operator
downloads a .zpl file and sends it with Zebra Setup Utilities. Heroku cannot
reach the warehouse LAN, so the server never talks to a printer.
"""
from flask import flash, g, redirect, request, url_for

from app import db
from app.inventory.codes import normalize_code
from app.inventory.labels import MAX_BATCH, PREFIX_CONTAINER, PREFIX_ITEM, PREFIX_UNIT, allocate, parse
from app.inventory.printing import (
    fields_for_container, fields_for_item, fields_for_unit, local_today, unit_number,
)
from app.inventory.zpl import SUPPORTED_DPI, LabelSpec, render_batch
from app.models import InvContainer, InvUnitIdentifier, SupplyItem
from app.models.constants import INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAGFEST_TAG

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin

BROWSER_PRINT_ORIGINS = ["http://localhost:9100", "https://localhost:9101"]


def _dpi() -> int:
    dpi = request.form.get("dpi", type=int)
    return dpi if dpi in SUPPORTED_DPI else 300


def _spec(code: str, fields: dict, printed: bool = False) -> LabelSpec:
    rows = [tuple(row) for row in fields["rows"]]
    if printed:
        rows.append(("Printed", local_today().isoformat()))
    return LabelSpec(code, tuple(rows), fields["footer"])


def _spec_for(code: str) -> LabelSpec | None:
    """Return the label for an existing code, or None when nothing holds it."""
    parsed = parse(code)
    if parsed is not None and parsed[0] == PREFIX_CONTAINER:
        c = db.session.query(InvContainer).filter_by(label_code=code).first()
        return _spec(code, fields_for_container(c)) if c else None
    if parsed is not None and parsed[0] == PREFIX_ITEM:
        item = db.session.query(SupplyItem).filter_by(barcode=code).first()
        return _spec(code, fields_for_item(item)) if item else None
    ident = db.session.query(InvUnitIdentifier).filter(
        InvUnitIdentifier.kind.in_((INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAGFEST_TAG)),
        InvUnitIdentifier.normalized_value == code,
        InvUnitIdentifier.retired_at.is_(None)).first()
    if ident:
        return _spec(unit_number(ident.unit) or code, fields_for_unit(ident.unit), printed=True)
    return None


def _specs_from_codes(raw: str) -> tuple[list[LabelSpec], list[str]]:
    specs, unknown = [], []
    for line in (raw or "").splitlines():
        code = normalize_code(line)
        if not code:
            continue
        spec = _spec_for(code)
        if spec:
            specs.append(spec)
        else:
            unknown.append(code)
    return specs[:MAX_BATCH], unknown


def _batch_page(specs, dpi, unknown=()):
    g.csp_connect_src_extra = BROWSER_PRINT_ORIGINS
    return render_inventory_page("inventory/label_batch.html", specs=specs, dpi=dpi,
                                 zpl=render_batch(specs, dpi), unknown=unknown)


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
    specs, unknown = _specs_from_codes(request.form.get("codes"))
    return _batch_page(specs, _dpi(), unknown)
