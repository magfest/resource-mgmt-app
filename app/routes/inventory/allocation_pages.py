"""Allocation page: department lines for one event beside the assets that can fill them."""
from flask import abort, flash, redirect, request, url_for
from sqlalchemy.orm import joinedload

from app.inventory.allocation_rights import can_allocate
from app.inventory.allocation import allocate, move, release
from app.inventory.checks import unit_code, unit_phrase
from app.inventory.demand import (
    LINE_OPEN, allocation_events, asset_warning, demand_lines, find_selection, line_label, pick_event,
    selection_assets, selections, shortfall,
)
from app.inventory.unit_fields import details_line
from app import db
from app.inventory.ledger import InventoryError
from app.models import InvAllocation, InvUnit
from app.routes import get_user_ctx

from . import inventory_bp
from .helpers import render_inventory_page, require_allocation_reader, require_allocator


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _requested_selection(event):
    """Read `item`, `service` or `pick`; an absent or stale key falls back to the first selection."""
    # The form's select posts `pick`; redirects from the action routes carry `item` or `service`.
    event_id = event.id if event else None
    pick = request.args.get("pick", "")
    kind, _, raw = pick.partition(":")
    if kind in ("item", "service"):
        return find_selection(kind, _int(raw), event_id) or find_selection(None, None, event_id)
    for kind in ("item", "service"):
        if kind in request.args:
            return find_selection(kind, _int(request.args.get(kind)), event_id) or find_selection(
                None, None, event_id)
    return find_selection(None, None, event_id)


def _asset_rows(assets, lines, event):
    """Pair each asset with its allocation text; a line seen on this page uses its live words."""
    live = {line.work_line_id: line_label(line) for line in lines if line.work_line_id}
    held = {}
    if assets:
        held = {a.unit_id: a for a in db.session.query(InvAllocation)
                .options(joinedload(InvAllocation.department), joinedload(InvAllocation.event_cycle))
                .filter(InvAllocation.unit_id.in_([u.id for u in assets]))}
    rows = []
    for unit in assets:
        row = held.get(unit.id)
        if row is None:
            text = "Not allocated"
        else:
            label = live.get(row.work_line_id) or f"{row.department.name} · {row.line_label}"
            text = f"Allocated to {label}"
            if row.event_cycle_id != event.id:
                text += f" ({row.event_cycle.code})"
        rows.append({"unit": unit, "number": unit_code(unit), "details": details_line(unit),
                     "text": text, "warning": asset_warning(unit), "allocation": row})
    return rows


@inventory_bp.get("/allocation")
@require_allocation_reader
def allocation():
    events = allocation_events()
    event = pick_event(events, _int(request.args.get("event")))
    options = selections(event.id if event else None)
    selection = _requested_selection(event)
    ctx = dict(line_label=line_label, unit_code=unit_code, events=events, event=event, options=options, selection=selection,
               can_allocate=can_allocate(get_user_ctx()),
               unallocated_only=request.args.get("show") == "unallocated")
    if event is None or selection is None:
        return render_inventory_page("inventory/allocation.html", lines=[], rows=[],
                                     totals=None, **ctx)
    lines = demand_lines(event.id, selection)
    assets = selection_assets(selection, ctx["unallocated_only"])
    return render_inventory_page(
        "inventory/allocation.html", lines=lines, totals=shortfall(selection, lines),
        rows=_asset_rows(assets, lines, event), **ctx)


def _carried() -> dict:
    """Event, selection and filter that every write route hands back to the page."""
    return {key: request.values[key] for key in ("event", "item", "service", "show")
            if request.values.get(key)}


def _units_from(values) -> list[InvUnit]:
    units = []
    for raw in values:
        unit = db.session.get(InvUnit, _int(raw)) if _int(raw) is not None else None
        if unit is None:
            abort(404)
        units.append(unit)
    return units


@inventory_bp.get("/allocation/pick")
@require_allocator
def allocation_pick():
    carried = _carried()
    units = _units_from(request.args.getlist("unit"))
    if not units:
        flash("Tick at least one asset.", "error")
        return redirect(url_for("inventory.allocation", **carried))
    event = pick_event(allocation_events(), _int(request.args.get("event")))
    selection = _requested_selection(event)
    if event is None or selection is None:
        return redirect(url_for("inventory.allocation", **carried))
    is_move = request.args.get("move") == "1" and len(units) == 1
    current = None
    if is_move:
        held = db.session.query(InvAllocation).filter_by(unit_id=units[0].id).one_or_none()
        current = held.work_line_id if held else None
    options = [line for line in demand_lines(event.id, selection)
               if line.state == LINE_OPEN and line.left > 0
               and not (is_move and line.work_line_id == current)]
    if is_move:
        heading = f"Move {unit_phrase(units[0])} to…"
    elif len(units) == 1:
        heading = f"Allocate {unit_phrase(units[0])} to…"
    else:
        heading = f"Allocate {len(units)} assets to…"
    return render_inventory_page(
        "inventory/allocation_pick.html", heading=heading, options=options, units=units,
        line_label=line_label, is_move=is_move, carried=carried,
        back=url_for("inventory.allocation", **carried))


def _finish(work):
    """Run one write; commit and flash its message, or roll back and flash the refusal."""
    try:
        message = work()
    except InventoryError as error:
        db.session.rollback()
        flash(str(error), "error")
    else:
        db.session.commit()
        flash(message, "success")
    return redirect(url_for("inventory.allocation", **_carried()))


@inventory_bp.post("/allocation/allocate")
@require_allocator
def allocation_allocate():
    units = _units_from(request.form.getlist("unit"))
    line_id = _int(request.form.get("line_id"))
    user_id = get_user_ctx().user_id

    def work():
        line = allocate(units, line_id, user_id)
        who = unit_phrase(units[0]) if len(units) == 1 else f"{len(units)} assets"
        return f"Allocated {who} to {line_label(line)}."
    return _finish(work)


@inventory_bp.post("/allocation/move")
@require_allocator
def allocation_move():
    unit = _units_from(request.form.getlist("unit")[:1] or [None])[0]
    line_id = _int(request.form.get("line_id"))
    user_id = get_user_ctx().user_id
    return _finish(lambda: f"Moved {unit_phrase(unit)} to "
                           f"{line_label(move(unit, line_id, user_id))}.")


@inventory_bp.post("/allocation/release")
@require_allocator
def allocation_release():
    unit = _units_from(request.form.getlist("unit")[:1] or [None])[0]
    user_id = get_user_ctx().user_id

    def work():
        release(unit, user_id)
        return f"Released {unit_phrase(unit)}."
    return _finish(work)
