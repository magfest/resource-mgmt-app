"""
Admin routes for event cycle management.
"""
from __future__ import annotations

from datetime import datetime
from flask import Blueprint, redirect, url_for, request, abort, flash

from sqlalchemy import func

from app import db
from app.models import (
    EventCycle,
    Division,
    Department,
    DivisionMembership,
    DepartmentMembership,
    User,
    WorkPortfolio,
    WorkItem,
    EventCycleDivision,
    EventCycleDepartment,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
    CONFIG_AUDIT_ARCHIVE,
    CONFIG_AUDIT_RESTORE,
)
from app.routes.work.helpers import (
    is_division_enabled_for_event,
    is_department_enabled_for_event,
    get_division_enablement_record,
    get_department_enablement_record,
    set_division_enablement,
    set_department_enablement,
    copy_event_enablement,
    bulk_set_all_enabled,
    get_all_division_enablement_records,
    get_all_department_enablement_records,
    get_all_department_enabled_status,
)
from app.routes import h
from .helpers import (
    require_super_admin,
    render_admin_config_page,
    log_config_change,
    track_changes,
    validate_code_length,
    CODE_MAX_LENGTH,
    safe_int,
)

event_cycles_bp = Blueprint('event_cycles', __name__, url_prefix='/event-cycles')


def _get_event_cycle_or_404(cycle_id: int) -> EventCycle:
    """Get event cycle by ID or abort with 404."""
    cycle = db.session.get(EventCycle, cycle_id)
    if not cycle:
        abort(404, "Event cycle not found")
    return cycle


def _get_work_item_count_for_event(cycle_id: int) -> int:
    """Count non-archived work items for this event cycle."""
    return (
        db.session.query(WorkItem)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .filter(
            WorkPortfolio.event_cycle_id == cycle_id,
            WorkItem.is_archived == False,
        )
        .count()
    )


def _cycle_to_dict(cycle: EventCycle) -> dict:
    """Convert event cycle to dict for change tracking."""
    return {
        "code": cycle.code,
        "name": cycle.name,
        "is_active": cycle.is_active,
        "is_default": cycle.is_default,
        "sort_order": cycle.sort_order,
        "event_start_date": cycle.event_start_date.isoformat() if cycle.event_start_date else None,
        "event_end_date": cycle.event_end_date.isoformat() if cycle.event_end_date else None,
        "submission_deadline": cycle.submission_deadline.isoformat() if cycle.submission_deadline else None,
        "approval_target_date": cycle.approval_target_date.isoformat() if cycle.approval_target_date else None,
        "finalization_date": cycle.finalization_date.isoformat() if cycle.finalization_date else None,
    }


def _parse_date(value: str | None):
    """Parse a date string (YYYY-MM-DD) into a date object."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@event_cycles_bp.get("/")
@require_super_admin
def list_event_cycles():
    """List all event cycles."""
    show_inactive = request.args.get("show_inactive") == "1"
    sort_by = request.args.get("sort_by", "sort_order")
    sort_dir = request.args.get("sort_dir", "asc")

    query = db.session.query(EventCycle)
    if not show_inactive:
        query = query.filter(EventCycle.is_active == True)

    # Sortable columns whitelist
    sortable = {
        "code": EventCycle.code,
        "name": EventCycle.name,
        "sort_order": EventCycle.sort_order,
    }

    if sort_by in sortable:
        col = sortable[sort_by]
        order = col.desc() if sort_dir == "desc" else col.asc()
        query = query.order_by(order)
    else:
        query = query.order_by(EventCycle.sort_order, EventCycle.name)

    cycles = query.all()

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
        sort_by=sort_by,
        sort_dir=sort_dir,
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

    # Validate code length
    if not validate_code_length(code, "Code"):
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
        sort_order=safe_int(request.form.get("sort_order")),
        event_start_date=_parse_date(request.form.get("event_start_date")),
        event_end_date=_parse_date(request.form.get("event_end_date")),
        submission_deadline=_parse_date(request.form.get("submission_deadline")),
        approval_target_date=_parse_date(request.form.get("approval_target_date")),
        finalization_date=_parse_date(request.form.get("finalization_date")),
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

    # Check if code is locked (work items exist)
    work_item_count = _get_work_item_count_for_event(cycle_id)
    code_locked = work_item_count > 0

    return render_admin_config_page(
        "admin/event_cycles/form.html",
        cycle=cycle,
        portfolio_count=portfolio_count,
        code_locked=code_locked,
        work_item_count=work_item_count,
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

    # Check if code is being changed and work items exist
    if code != cycle.code:
        work_item_count = _get_work_item_count_for_event(cycle_id)
        if work_item_count > 0:
            flash(
                f"Cannot change event code: {work_item_count} budget request(s) exist. "
                "The code is used in request IDs and URLs.",
                "error"
            )
            return redirect(url_for(".edit_event_cycle", cycle_id=cycle_id))

    # Validate code length
    if not validate_code_length(code, "Code"):
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
    cycle.sort_order = safe_int(request.form.get("sort_order"))
    cycle.event_start_date = _parse_date(request.form.get("event_start_date"))
    cycle.event_end_date = _parse_date(request.form.get("event_end_date"))
    cycle.submission_deadline = _parse_date(request.form.get("submission_deadline"))
    cycle.approval_target_date = _parse_date(request.form.get("approval_target_date"))
    cycle.finalization_date = _parse_date(request.form.get("finalization_date"))
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


# ============================================================
# Organization Enablement (Division/Department per Event)
# ============================================================

@event_cycles_bp.get("/<int:cycle_id>/organization")
@require_super_admin
def organization_enablement(cycle_id: int):
    """
    Event Organization dashboard - manage divisions, departments, and members.

    Shows:
    - Which divisions/departments participate in this event
    - Member counts and heads for each
    - Direct links to member management
    """
    cycle = _get_event_cycle_or_404(cycle_id)

    # Get all active divisions with their departments
    divisions = Division.query.filter_by(is_active=True).order_by(
        Division.sort_order, Division.name
    ).all()

    # Get all departments (including those without a division)
    departments = Department.query.filter_by(is_active=True).order_by(
        Department.sort_order, Department.name
    ).all()

    # ========================================
    # Fetch member data efficiently (batch queries)
    # ========================================

    # Division member counts for this event
    div_member_counts = dict(
        db.session.query(
            DivisionMembership.division_id,
            func.count(DivisionMembership.id)
        ).filter(
            DivisionMembership.event_cycle_id == cycle_id
        ).group_by(DivisionMembership.division_id).all()
    )

    # Division heads for this event (with user info)
    div_heads_query = db.session.query(
        DivisionMembership.division_id,
        User.display_name,
        User.id.label('user_id'),
    ).join(
        User, DivisionMembership.user_id == User.id
    ).filter(
        DivisionMembership.event_cycle_id == cycle_id,
        DivisionMembership.is_division_head == True,
    ).all()

    div_heads = {}
    for row in div_heads_query:
        if row.division_id not in div_heads:
            div_heads[row.division_id] = []
        div_heads[row.division_id].append({
            "name": row.display_name,
            "user_id": row.user_id,
        })

    # Department member counts for this event
    dept_member_counts = dict(
        db.session.query(
            DepartmentMembership.department_id,
            func.count(DepartmentMembership.id)
        ).filter(
            DepartmentMembership.event_cycle_id == cycle_id
        ).group_by(DepartmentMembership.department_id).all()
    )

    # Department heads for this event (with user info)
    dept_heads_query = db.session.query(
        DepartmentMembership.department_id,
        User.display_name,
        User.id.label('user_id'),
    ).join(
        User, DepartmentMembership.user_id == User.id
    ).filter(
        DepartmentMembership.event_cycle_id == cycle_id,
        DepartmentMembership.is_department_head == True,
    ).all()

    dept_heads = {}
    for row in dept_heads_query:
        if row.department_id not in dept_heads:
            dept_heads[row.department_id] = []
        dept_heads[row.department_id].append({
            "name": row.display_name,
            "user_id": row.user_id,
        })

    # ========================================
    # Batch fetch enablement records (2 queries instead of N+N)
    # ========================================
    div_enablement_map = get_all_division_enablement_records(cycle_id)
    dept_enablement_map = get_all_department_enablement_records(cycle_id)

    # Compute all department enabled status in memory (no additional queries)
    dept_enabled_status = get_all_department_enabled_status(
        cycle_id, departments, div_enablement_map, dept_enablement_map
    )

    # ========================================
    # Build enablement status for each division
    # ========================================
    division_status = {}
    for div in divisions:
        record = div_enablement_map.get(div.id)
        division_status[div.id] = {
            "is_enabled": record.is_enabled if record else True,
            "note": record.note if record else None,
            "has_record": record is not None,
            "user_count": div_member_counts.get(div.id, 0),
            "heads": div_heads.get(div.id, []),
        }

    # ========================================
    # Build enablement status for each department
    # ========================================
    department_status = {}
    for dept in departments:
        record = dept_enablement_map.get(dept.id)
        actual_enabled = dept_enabled_status.get(dept.id, True)
        own_enabled = record.is_enabled if record else True

        department_status[dept.id] = {
            "is_enabled": actual_enabled,
            "own_enabled": own_enabled,
            "note": record.note if record else None,
            "has_record": record is not None,
            "inherited_disabled": not actual_enabled and own_enabled,
            "user_count": dept_member_counts.get(dept.id, 0),
            "heads": dept_heads.get(dept.id, []),
        }

    # Group departments by division
    depts_by_division = {}
    depts_no_division = []
    for dept in departments:
        if dept.division_id:
            if dept.division_id not in depts_by_division:
                depts_by_division[dept.division_id] = []
            depts_by_division[dept.division_id].append(dept)
        else:
            depts_no_division.append(dept)

    # Get other event cycles for copy dropdown
    other_cycles = EventCycle.query.filter(
        EventCycle.id != cycle_id,
        EventCycle.is_active == True,
    ).order_by(EventCycle.sort_order, EventCycle.name).all()

    # Count enabled/disabled
    enabled_div_count = sum(1 for s in division_status.values() if s["is_enabled"])
    enabled_dept_count = sum(1 for s in department_status.values() if s["is_enabled"])

    # Total users for summary
    total_div_users = sum(div_member_counts.values())
    total_dept_users = sum(dept_member_counts.values())

    return render_admin_config_page(
        "admin/event_cycles/org_enablement.html",
        cycle=cycle,
        divisions=divisions,
        departments=departments,
        division_status=division_status,
        department_status=department_status,
        depts_by_division=depts_by_division,
        depts_no_division=depts_no_division,
        other_cycles=other_cycles,
        enabled_div_count=enabled_div_count,
        enabled_dept_count=enabled_dept_count,
        total_div_count=len(divisions),
        total_dept_count=len(departments),
        total_div_users=total_div_users,
        total_dept_users=total_dept_users,
    )


@event_cycles_bp.post("/<int:cycle_id>/organization/division/<int:division_id>")
@require_super_admin
def toggle_division_enablement(cycle_id: int, division_id: int):
    """Toggle a division's enablement for this event."""
    cycle = _get_event_cycle_or_404(cycle_id)
    division = db.session.get(Division, division_id)
    if not division:
        abort(404, "Division not found")

    # Get current status
    current_enabled = is_division_enabled_for_event(division_id, cycle_id)
    new_enabled = not current_enabled

    set_division_enablement(
        division_id=division_id,
        event_cycle_id=cycle_id,
        is_enabled=new_enabled,
        user_id=h.get_active_user_id(),
    )

    action = "enabled" if new_enabled else "disabled"
    log_config_change(
        "event_cycle_division",
        cycle_id,
        CONFIG_AUDIT_UPDATE,
        {"division_id": division_id, "is_enabled": {"old": current_enabled, "new": new_enabled}},
    )

    db.session.commit()
    flash(f"Division '{division.name}' {action} for {cycle.name}", "success")
    return redirect(url_for(".organization_enablement", cycle_id=cycle_id))


@event_cycles_bp.post("/<int:cycle_id>/organization/department/<int:department_id>")
@require_super_admin
def toggle_department_enablement(cycle_id: int, department_id: int):
    """Toggle a department's enablement for this event."""
    cycle = _get_event_cycle_or_404(cycle_id)
    department = db.session.get(Department, department_id)
    if not department:
        abort(404, "Department not found")

    # Check if division is disabled (department can't be enabled)
    if department.division_id:
        div_enabled = is_division_enabled_for_event(department.division_id, cycle_id)
        if not div_enabled:
            flash(f"Cannot enable department - its division is disabled", "error")
            return redirect(url_for(".organization_enablement", cycle_id=cycle_id))

    # Get current status (own status, not inherited)
    record = get_department_enablement_record(department_id, cycle_id)
    current_enabled = record.is_enabled if record else True
    new_enabled = not current_enabled

    set_department_enablement(
        department_id=department_id,
        event_cycle_id=cycle_id,
        is_enabled=new_enabled,
        user_id=h.get_active_user_id(),
    )

    action = "enabled" if new_enabled else "disabled"
    log_config_change(
        "event_cycle_department",
        cycle_id,
        CONFIG_AUDIT_UPDATE,
        {"department_id": department_id, "is_enabled": {"old": current_enabled, "new": new_enabled}},
    )

    db.session.commit()
    flash(f"Department '{department.name}' {action} for {cycle.name}", "success")
    return redirect(url_for(".organization_enablement", cycle_id=cycle_id))


@event_cycles_bp.post("/<int:cycle_id>/organization/copy-from/<int:source_cycle_id>")
@require_super_admin
def copy_enablement_from(cycle_id: int, source_cycle_id: int):
    """Copy enablement settings from another event cycle."""
    cycle = _get_event_cycle_or_404(cycle_id)
    source_cycle = _get_event_cycle_or_404(source_cycle_id)

    result = copy_event_enablement(
        source_event_id=source_cycle_id,
        target_event_id=cycle_id,
        user_id=h.get_active_user_id(),
    )

    log_config_change(
        "event_cycle_enablement",
        cycle_id,
        CONFIG_AUDIT_UPDATE,
        {"copied_from": source_cycle_id, **result},
    )

    db.session.commit()
    flash(
        f"Copied settings from {source_cycle.name}: "
        f"{result['divisions_copied']} divisions, {result['departments_copied']} departments",
        "success"
    )
    return redirect(url_for(".organization_enablement", cycle_id=cycle_id))


@event_cycles_bp.post("/<int:cycle_id>/organization/enable-all")
@require_super_admin
def enable_all_orgs(cycle_id: int):
    """Enable all divisions and departments for this event."""
    cycle = _get_event_cycle_or_404(cycle_id)

    result = bulk_set_all_enabled(
        event_cycle_id=cycle_id,
        is_enabled=True,
        user_id=h.get_active_user_id(),
    )

    log_config_change(
        "event_cycle_enablement",
        cycle_id,
        CONFIG_AUDIT_UPDATE,
        {"action": "enable_all", **result},
    )

    db.session.commit()
    flash(f"Enabled all divisions and departments for {cycle.name}", "success")
    return redirect(url_for(".organization_enablement", cycle_id=cycle_id))


@event_cycles_bp.post("/<int:cycle_id>/organization/disable-all")
@require_super_admin
def disable_all_orgs(cycle_id: int):
    """Disable all divisions and departments for this event."""
    cycle = _get_event_cycle_or_404(cycle_id)

    result = bulk_set_all_enabled(
        event_cycle_id=cycle_id,
        is_enabled=False,
        user_id=h.get_active_user_id(),
    )

    log_config_change(
        "event_cycle_enablement",
        cycle_id,
        CONFIG_AUDIT_UPDATE,
        {"action": "disable_all", **result},
    )

    db.session.commit()
    flash(f"Disabled all divisions and departments for {cycle.name}", "success")
    return redirect(url_for(".organization_enablement", cycle_id=cycle_id))
