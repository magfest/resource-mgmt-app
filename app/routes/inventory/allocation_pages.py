"""Allocation page: department lines for one event beside the assets that can fill them."""
from flask import request
from sqlalchemy.orm import joinedload

from app.inventory.allocation_rights import can_allocate
from app.inventory.checks import unit_code
from app.inventory.demand import (
    allocation_events, asset_warning, demand_lines, find_selection, line_label, pick_event,
    selection_assets, selections, shortfall,
)
from app.inventory.unit_fields import details_line
from app import db
from app.models import InvAllocation
from app.routes import get_user_ctx

from . import inventory_bp
from .helpers import render_inventory_page, require_allocation_reader


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _requested_selection():
    """Read `item`, `service` or `pick`; an absent or stale key falls back to the first selection."""
    # The form's select posts `pick`; redirects from the action routes carry `item` or `service`.
    pick = request.args.get("pick", "")
    kind, _, raw = pick.partition(":")
    if kind in ("item", "service"):
        return find_selection(kind, _int(raw)) or find_selection(None, None)
    for kind in ("item", "service"):
        if kind in request.args:
            return find_selection(kind, _int(request.args.get(kind))) or find_selection(None, None)
    return find_selection(None, None)


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
    options = selections()
    selection = _requested_selection()
    ctx = dict(line_label=line_label, events=events, event=event, options=options, selection=selection,
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
