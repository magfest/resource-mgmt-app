"""Allocation page: department lines for one event beside the assets that can fill them."""
from flask import abort, flash, redirect, request, url_for
from sqlalchemy.orm import joinedload

from app.inventory.allocation_rights import can_allocate
from app.inventory.allocation_view import (
    LINES_ALL, LINES_NOTES, LINES_SHORT, asset_choices, cut_words, departments, filter_lines,
    group_by_department, option_text, short_count, short_order,
)
from app.inventory.allocation import RowChange, save_changes
from app.inventory.checks import unit_code, unit_phrase
from app.inventory.demand import (
    allocation_events, asset_warning, demand_lines, find_selection, line_label, pick_event,
    selection_assets, selections, shortfall,
)
from app.inventory.unit_fields import label_line
from app import db
from app.inventory.ledger import InventoryError
from app.models import InvAllocation, InvUnit, WorkLine
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
    pick = request.values.get("pick", "")
    kind, _, raw = pick.partition(":")
    if kind in ("item", "service"):
        return find_selection(kind, _int(raw), event_id) or find_selection(None, None, event_id)
    for kind in ("item", "service"):
        if kind in request.values:
            return find_selection(kind, _int(request.values.get(kind)), event_id) or find_selection(
                None, None, event_id)
    return find_selection(None, None, event_id)


def _held(assets) -> dict[int, InvAllocation]:
    """Map each listed asset to its allocation in any event, in one query."""
    if not assets:
        return {}
    return {a.unit_id: a for a in db.session.query(InvAllocation)
            .options(joinedload(InvAllocation.department), joinedload(InvAllocation.event_cycle),
                     joinedload(InvAllocation.work_line).joinedload(WorkLine.work_item))
            .filter(InvAllocation.unit_id.in_([u.id for u in assets]))}


def _held_option(row: InvAllocation, event) -> str:
    """Name an allocation whose line is not among this event's lines."""
    if row.work_line is None:
        text = f"Line changed: removed · {row.line_label}"
    else:
        text = (f"{row.department.name} · {short_order(row.work_line.work_item.public_id)}"
                f" line {row.work_line.line_number} · {cut_words(row.line_label)}")
    return text if row.event_cycle_id == event.id else f"{text} ({row.event_cycle.code})"


def _asset_rows(assets, held, lines, event, choices, picked=None, was=None):
    """Build each asset row: reader text, and the dropdown's options, choice and rendered value.

    After a refused save, `picked` and `was` hold the posted values so no pick is lost.
    """
    live = {line.work_line_id: line for line in lines if line.work_line_id}
    offered = {line_id for group in choices for line_id, _ in group.options}
    rows = []
    for unit in assets:
        row = held.get(unit.id)
        current = row.work_line_id if row else None
        if row is None:
            text = "Not allocated"
        else:
            label = (line_label(live[current]) if current in live
                     else f"{row.department.name} · {row.line_label}")
            text = f"Allocated to {label}"
            if row.event_cycle_id != event.id:
                text += f" ({row.event_cycle.code})"
        warning = asset_warning(unit)
        selected = picked[unit.id] if picked and unit.id in picked else current
        rendered = _int(was[unit.id]) if was and unit.id in was else current
        # The rules refuse a new allocation for an asset out of service, so it gets no choices.
        extra, seen = [], set() if warning else set(offered)
        for line_id in (current, rendered, selected):
            if line_id is None or line_id in seen:
                continue
            seen.add(line_id)
            if line_id in live:
                extra.append((line_id, option_text(live[line_id])))
            elif line_id == current:
                extra.append((line_id, _held_option(row, event)))
        # A removed line has no id, so its option shares the "" value of Not allocated.
        removed = row is not None and current is None
        if removed:
            extra.append(("", _held_option(row, event)))
        rows.append({"unit": unit, "number": unit_code(unit), "details": label_line(unit),
                     "text": text, "warning": warning, "allocation": row, "removed": removed,
                     "choices": [] if warning else choices, "extra": extra,
                     "selected": selected, "was": rendered})
    return rows


def _render_allocation(error=None, picked=None, was=None):
    """Build the page from `request.values`; `error` shows above the lists."""
    events = allocation_events()
    event = pick_event(events, _int(request.values.get("event")))
    options = selections(event.id if event else None)
    selection = _requested_selection(event)
    ctx = dict(line_label=line_label, unit_code=unit_code, short_count=short_count, events=events, event=event, options=options, selection=selection,
               can_allocate=can_allocate(get_user_ctx()),
               unallocated_only=request.values.get("show") == "unallocated",
               error=error, picked=picked, was=was)
    if event is None or selection is None:
        return render_inventory_page("inventory/allocation.html", lines=[], rows=[],
                                     totals=None, tab="assets", dept_id=None, dept_options=[],
                                     lines_show=LINES_SHORT, filters={}, groups=[], lines_count=0,
                                     assets_count=0, **ctx)
    lines = demand_lines(event.id, selection)
    assets = selection_assets(selection, ctx["unallocated_only"])
    tab = "lines" if request.values.get("tab") == "lines" else "assets"
    options_by_id = dict(departments(lines))
    dept_id = _int(request.values.get("dept"))
    dept_id = dept_id if dept_id in options_by_id else None
    held = _held(assets)
    if dept_id:
        # One department's view: its allocations for this event, and every free asset.
        assets = [u for u in assets if u.id not in held or (
            held[u.id].event_cycle_id == event.id and held[u.id].department_id == dept_id)]
    lines_show = request.values.get("lines")
    lines_show = lines_show if lines_show in (LINES_ALL, LINES_NOTES) else LINES_SHORT
    filters = {"tab": tab, "lines": lines_show}
    if dept_id:
        filters["dept"] = dept_id
    if ctx["unallocated_only"]:
        filters["show"] = "unallocated"
    shown = filter_lines(lines, dept_id, lines_show)
    # The Lines tab needs no asset rows, so they are built only when the Assets tab shows.
    rows = (_asset_rows(assets, held, lines, event, asset_choices(lines, dept_id), picked, was)
            if tab == "assets" else [])
    return render_inventory_page(
        "inventory/allocation.html", lines=lines, totals=shortfall(selection, lines), rows=rows,
        tab=tab, dept_id=dept_id, dept_options=list(options_by_id.items()), lines_show=lines_show,
        filters=filters, groups=group_by_department(shown), lines_count=len(shown), assets_count=len(assets), **ctx)


@inventory_bp.get("/allocation")
@require_allocation_reader
def allocation():
    return _render_allocation()


def _carried() -> dict:
    """Event, selection and filter that every write route hands back to the page."""
    return {key: request.values[key] for key in ("event", "item", "service", "tab", "dept", "show", "lines")
            if request.values.get(key)}


def _units_from(values) -> list[InvUnit]:
    units = []
    for raw in values:
        unit = db.session.get(InvUnit, _int(raw)) if _int(raw) is not None else None
        if unit is None:
            abort(404)
        units.append(unit)
    return units


@inventory_bp.post("/allocation/save")
@require_allocator
def allocation_save():
    """Apply every changed row in one transaction; a refusal re-renders the page with nothing saved."""
    ids = sorted({key.partition("_")[2] for key in request.form if key.startswith(("was_", "to_"))})
    units = {unit.id: unit for unit in _units_from(ids)}
    was = {uid: request.form.get(f"was_{uid}", "") for uid in units}
    picked = {uid: _int(request.form.get(f"to_{uid}")) for uid in units}
    changes = [RowChange(units[uid], _int(was[uid]), picked[uid]) for uid in units]
    try:
        result = save_changes(changes, get_user_ctx().user_id)
    except InventoryError as error:
        db.session.rollback()
        return _render_allocation(error=str(error), picked=picked, was=was)
    db.session.commit()
    if result.saved:
        flash(f"Saved {result.saved} change{'' if result.saved == 1 else 's'}.", "success")
    elif not result.stale:
        flash("Nothing to save.", "success")
    for unit in result.stale:
        flash(f"{unit_phrase(unit)} was changed by someone else; it was not saved.", "warning")
    return redirect(url_for("inventory.allocation", **_carried()))
