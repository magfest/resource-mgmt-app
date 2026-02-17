"""
Admin routes for event cycle management.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    EventCycle,
    WorkPortfolio,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
    CONFIG_AUDIT_ARCHIVE,
    CONFIG_AUDIT_RESTORE,
)
from app.routes import h
from .helpers import (
    require_super_admin,
    render_admin_config_page,
    log_config_change,
    track_changes,
)

event_cycles_bp = Blueprint('event_cycles', __name__, url_prefix='/event-cycles')


def _get_event_cycle_or_404(cycle_id: int) -> EventCycle:
    """Get event cycle by ID or abort with 404."""
    cycle = db.session.get(EventCycle, cycle_id)
    if not cycle:
        abort(404, "Event cycle not found")
    return cycle


def _cycle_to_dict(cycle: EventCycle) -> dict:
    """Convert event cycle to dict for change tracking."""
    return {
        "code": cycle.code,
        "name": cycle.name,
        "is_active": cycle.is_active,
        "is_default": cycle.is_default,
        "sort_order": cycle.sort_order,
    }


@event_cycles_bp.get("/")
@require_super_admin
def list_event_cycles():
    """List all event cycles."""
    show_inactive = request.args.get("show_inactive") == "1"

    query = db.session.query(EventCycle)
    if not show_inactive:
        query = query.filter(EventCycle.is_active == True)

    cycles = query.order_by(EventCycle.sort_order, EventCycle.name).all()

    # Get portfolio counts per cycle
    portfolio_counts = {}
    for cycle in cycles:
        count = (
            db.session.query(WorkPortfolio)
            .filter(WorkPortfolio.event_cycle_id == cycle.id)
            .count()
        )
        portfolio_counts[cycle.id] = count

    return render_admin_config_page(
        "admin/event_cycles/list.html",
        cycles=cycles,
        portfolio_counts=portfolio_counts,
        show_inactive=show_inactive,
    )


@event_cycles_bp.get("/new")
@require_super_admin
def new_event_cycle():
    """Show new event cycle form."""
    return render_admin_config_page(
        "admin/event_cycles/form.html",
        cycle=None,
    )


@event_cycles_bp.post("/")
@require_super_admin
def create_event_cycle():
    """Create a new event cycle."""
    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for(".new_event_cycle"))

    # Check for duplicate code
    existing = db.session.query(EventCycle).filter_by(code=code).first()
    if existing:
        flash(f"An event cycle with code '{code}' already exists", "error")
        return redirect(url_for(".new_event_cycle"))

    is_default = request.form.get("is_default") == "1"

    # If setting as default, clear other defaults
    if is_default:
        db.session.query(EventCycle).filter(EventCycle.is_default == True).update({"is_default": False})

    cycle = EventCycle(
        code=code,
        name=name,
        is_active=request.form.get("is_active") == "1",
        is_default=is_default,
        sort_order=int(request.form.get("sort_order") or 0),
        created_by_user_id=h.get_active_user_id(),
        updated_by_user_id=h.get_active_user_id(),
    )

    db.session.add(cycle)
    db.session.flush()

    log_config_change("event_cycle", cycle.id, CONFIG_AUDIT_CREATE)

    db.session.commit()
    flash(f"Created event cycle: {cycle.name}", "success")
    return redirect(url_for(".list_event_cycles"))


@event_cycles_bp.get("/<int:cycle_id>")
@require_super_admin
def edit_event_cycle(cycle_id: int):
    """Show edit form for event cycle."""
    cycle = _get_event_cycle_or_404(cycle_id)

    # Get portfolio count
    portfolio_count = (
        db.session.query(WorkPortfolio)
        .filter(WorkPortfolio.event_cycle_id == cycle_id)
        .count()
    )

    return render_admin_config_page(
        "admin/event_cycles/form.html",
        cycle=cycle,
        portfolio_count=portfolio_count,
    )


@event_cycles_bp.post("/<int:cycle_id>")
@require_super_admin
def update_event_cycle(cycle_id: int):
    """Update an event cycle."""
    cycle = _get_event_cycle_or_404(cycle_id)

    old_values = _cycle_to_dict(cycle)

    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for(".edit_event_cycle", cycle_id=cycle_id))

    # Check for duplicate code
    existing = db.session.query(EventCycle).filter(
        EventCycle.code == code,
        EventCycle.id != cycle_id
    ).first()
    if existing:
        flash(f"An event cycle with code '{code}' already exists", "error")
        return redirect(url_for(".edit_event_cycle", cycle_id=cycle_id))

    is_default = request.form.get("is_default") == "1"

    # If setting as default, clear other defaults
    if is_default and not cycle.is_default:
        db.session.query(EventCycle).filter(
            EventCycle.is_default == True,
            EventCycle.id != cycle_id
        ).update({"is_default": False})

    cycle.code = code
    cycle.name = name
    cycle.is_active = request.form.get("is_active") == "1"
    cycle.is_default = is_default
    cycle.sort_order = int(request.form.get("sort_order") or 0)
    cycle.updated_by_user_id = h.get_active_user_id()

    new_values = _cycle_to_dict(cycle)
    changes = track_changes(old_values, new_values)
    if changes:
        log_config_change("event_cycle", cycle.id, CONFIG_AUDIT_UPDATE, changes)

    db.session.commit()
    flash(f"Updated event cycle: {cycle.name}", "success")
    return redirect(url_for(".list_event_cycles"))


@event_cycles_bp.post("/<int:cycle_id>/archive")
@require_super_admin
def archive_event_cycle(cycle_id: int):
    """Archive (soft-delete) an event cycle."""
    cycle = _get_event_cycle_or_404(cycle_id)

    if not cycle.is_active:
        flash("Event cycle is already archived", "warning")
        return redirect(url_for(".list_event_cycles"))

    cycle.is_active = False
    cycle.updated_by_user_id = h.get_active_user_id()

    # Clear default flag if archiving default
    if cycle.is_default:
        cycle.is_default = False

    log_config_change("event_cycle", cycle.id, CONFIG_AUDIT_ARCHIVE)

    db.session.commit()
    flash(f"Archived event cycle: {cycle.name}", "success")
    return redirect(url_for(".list_event_cycles"))


@event_cycles_bp.post("/<int:cycle_id>/restore")
@require_super_admin
def restore_event_cycle(cycle_id: int):
    """Restore an archived event cycle."""
    cycle = _get_event_cycle_or_404(cycle_id)

    if cycle.is_active:
        flash("Event cycle is already active", "warning")
        return redirect(url_for(".list_event_cycles"))

    cycle.is_active = True
    cycle.updated_by_user_id = h.get_active_user_id()

    log_config_change("event_cycle", cycle.id, CONFIG_AUDIT_RESTORE)

    db.session.commit()
    flash(f"Restored event cycle: {cycle.name}", "success")
    return redirect(url_for(".list_event_cycles"))


@event_cycles_bp.post("/<int:cycle_id>/set-default")
@require_super_admin
def set_default_event_cycle(cycle_id: int):
    """Set an event cycle as the default."""
    cycle = _get_event_cycle_or_404(cycle_id)

    if not cycle.is_active:
        flash("Cannot set inactive cycle as default", "error")
        return redirect(url_for(".list_event_cycles"))

    # Clear other defaults
    db.session.query(EventCycle).filter(
        EventCycle.is_default == True,
        EventCycle.id != cycle_id
    ).update({"is_default": False})

    cycle.is_default = True
    cycle.updated_by_user_id = h.get_active_user_id()

    log_config_change("event_cycle", cycle.id, CONFIG_AUDIT_UPDATE, {"is_default": {"old": False, "new": True}})

    db.session.commit()
    flash(f"Set {cycle.name} as the default event cycle", "success")
    return redirect(url_for(".list_event_cycles"))
