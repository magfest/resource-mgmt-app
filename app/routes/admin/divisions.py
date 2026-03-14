"""
Admin routes for division management.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    Division,
    DivisionMembership,
    DivisionMembershipWorkTypeAccess,
    Department,
    EventCycle,
    User,
    WorkType,
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
    validate_code_length,
    CODE_MAX_LENGTH,
    safe_int,
)

divisions_bp = Blueprint('divisions', __name__, url_prefix='/divisions')


def _get_division_or_404(division_id: int) -> Division:
    """Get division by ID or abort with 404."""
    division = db.session.get(Division, division_id)
    if not division:
        abort(404, "Division not found")
    return division


def _division_to_dict(division: Division) -> dict:
    """Convert division to dict for change tracking."""
    return {
        "code": division.code,
        "name": division.name,
        "description": division.description,
        "is_active": division.is_active,
        "sort_order": division.sort_order,
    }


def _get_active_work_types():
    """Get all active work types for access configuration."""
    return (
        db.session.query(WorkType)
        .filter(WorkType.is_active == True)
        .order_by(WorkType.sort_order, WorkType.name)
        .all()
    )


@divisions_bp.get("/")
@require_super_admin
def list_divisions():
    """List all divisions."""
    show_inactive = request.args.get("show_inactive") == "1"
    sort_by = request.args.get("sort_by", "sort_order")
    sort_dir = request.args.get("sort_dir", "asc")

    query = db.session.query(Division)
    if not show_inactive:
        query = query.filter(Division.is_active == True)

    # Sortable columns whitelist
    sortable = {
        "code": Division.code,
        "name": Division.name,
        "sort_order": Division.sort_order,
    }

    if sort_by in sortable:
        col = sortable[sort_by]
        order = col.desc() if sort_dir == "desc" else col.asc()
        query = query.order_by(order)
    else:
        query = query.order_by(Division.sort_order, Division.name)

    divisions = query.all()

    # Get department counts per division
    dept_counts = {}
    member_counts = {}
    for div in divisions:
        dept_counts[div.id] = (
            db.session.query(Department)
            .filter(Department.division_id == div.id)
            .count()
        )
        member_counts[div.id] = (
            db.session.query(DivisionMembership)
            .filter(DivisionMembership.division_id == div.id)
            .count()
        )

    return render_admin_config_page(
        "admin/divisions/list.html",
        divisions=divisions,
        dept_counts=dept_counts,
        member_counts=member_counts,
        show_inactive=show_inactive,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@divisions_bp.get("/new")
@require_super_admin
def new_division():
    """Show new division form."""
    return render_admin_config_page(
        "admin/divisions/form.html",
        division=None,
    )


@divisions_bp.post("/")
@require_super_admin
def create_division():
    """Create a new division."""
    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for(".new_division"))

    # Validate code length
    if not validate_code_length(code, "Code"):
        return redirect(url_for(".new_division"))

    # Check for duplicate code
    existing = db.session.query(Division).filter_by(code=code).first()
    if existing:
        flash(f"A division with code '{code}' already exists", "error")
        return redirect(url_for(".new_division"))

    division = Division(
        code=code,
        name=name,
        description=(request.form.get("description") or "").strip() or None,
        is_active=request.form.get("is_active") == "1",
        sort_order=safe_int(request.form.get("sort_order")),
    )

    db.session.add(division)
    db.session.flush()

    log_config_change("division", division.id, CONFIG_AUDIT_CREATE)

    db.session.commit()
    flash(f"Created division: {division.name}", "success")
    return redirect(url_for(".list_divisions"))


@divisions_bp.get("/<int:division_id>")
@require_super_admin
def edit_division(division_id: int):
    """Show edit form for division."""
    division = _get_division_or_404(division_id)

    # Get department count
    dept_count = (
        db.session.query(Department)
        .filter(Department.division_id == division_id)
        .count()
    )

    return render_admin_config_page(
        "admin/divisions/form.html",
        division=division,
        dept_count=dept_count,
    )


@divisions_bp.post("/<int:division_id>")
@require_super_admin
def update_division(division_id: int):
    """Update a division."""
    division = _get_division_or_404(division_id)

    old_values = _division_to_dict(division)

    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for(".edit_division", division_id=division_id))

    # Validate code length
    if not validate_code_length(code, "Code"):
        return redirect(url_for(".edit_division", division_id=division_id))

    # Check for duplicate code
    existing = db.session.query(Division).filter(
        Division.code == code,
        Division.id != division_id
    ).first()
    if existing:
        flash(f"A division with code '{code}' already exists", "error")
        return redirect(url_for(".edit_division", division_id=division_id))

    division.code = code
    division.name = name
    division.description = (request.form.get("description") or "").strip() or None
    division.is_active = request.form.get("is_active") == "1"
    division.sort_order = safe_int(request.form.get("sort_order"))

    new_values = _division_to_dict(division)
    changes = track_changes(old_values, new_values)
    if changes:
        log_config_change("division", division.id, CONFIG_AUDIT_UPDATE, changes)

    db.session.commit()
    flash(f"Updated division: {division.name}", "success")
    return redirect(url_for(".list_divisions"))


@divisions_bp.post("/<int:division_id>/archive")
@require_super_admin
def archive_division(division_id: int):
    """Archive (soft-delete) a division."""
    division = _get_division_or_404(division_id)

    if not division.is_active:
        flash("Division is already archived", "warning")
        return redirect(url_for(".list_divisions"))

    division.is_active = False

    log_config_change("division", division.id, CONFIG_AUDIT_ARCHIVE)

    db.session.commit()
    flash(f"Archived division: {division.name}", "success")
    return redirect(url_for(".list_divisions"))


@divisions_bp.post("/<int:division_id>/restore")
@require_super_admin
def restore_division(division_id: int):
    """Restore an archived division."""
    division = _get_division_or_404(division_id)

    if division.is_active:
        flash("Division is already active", "warning")
        return redirect(url_for(".list_divisions"))

    division.is_active = True

    log_config_change("division", division.id, CONFIG_AUDIT_RESTORE)

    db.session.commit()
    flash(f"Restored division: {division.name}", "success")
    return redirect(url_for(".list_divisions"))


# ============================================================
# Division Membership (Division Heads) Management
# ============================================================

@divisions_bp.get("/<int:division_id>/members")
@require_super_admin
def list_members(division_id: int):
    """List all members of a division."""
    division = _get_division_or_404(division_id)

    # Check for event filter
    event_filter_id = request.args.get('event', type=int)
    selected_event = None
    if event_filter_id:
        selected_event = db.session.get(EventCycle, event_filter_id)

    # Get all memberships for this division
    memberships_query = (
        db.session.query(DivisionMembership)
        .filter(DivisionMembership.division_id == division_id)
        .join(User)
        .join(EventCycle)
    )

    # Apply event filter if specified
    if selected_event:
        memberships_query = memberships_query.filter(
            DivisionMembership.event_cycle_id == selected_event.id
        )

    memberships = memberships_query.order_by(
        EventCycle.sort_order, User.display_name
    ).all()

    # Get available event cycles for adding new members and filtering
    event_cycles = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active == True)
        .order_by(EventCycle.sort_order)
        .all()
    )

    # Get active work types for display
    work_types = _get_active_work_types()

    return render_admin_config_page(
        "admin/divisions/members.html",
        division=division,
        memberships=memberships,
        event_cycles=event_cycles,
        work_types=work_types,
        selected_event=selected_event,
    )


@divisions_bp.get("/<int:division_id>/members/add")
@require_super_admin
def add_member_form(division_id: int):
    """Show form to add a division member."""
    division = _get_division_or_404(division_id)

    # Check for pre-selected event (e.g., coming from org page)
    preselect_event_id = request.args.get('event', type=int)
    preselect_event = None
    if preselect_event_id:
        preselect_event = db.session.get(EventCycle, preselect_event_id)

    # Get available users (all active users)
    users = (
        db.session.query(User)
        .filter(User.is_active == True)
        .order_by(User.display_name)
        .all()
    )

    # Get available event cycles
    event_cycles = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active == True)
        .order_by(EventCycle.sort_order)
        .all()
    )

    # Get active work types for access configuration
    work_types = _get_active_work_types()

    return render_admin_config_page(
        "admin/divisions/member_form.html",
        division=division,
        membership=None,
        users=users,
        event_cycles=event_cycles,
        work_types=work_types,
        preselect_event=preselect_event,
    )


@divisions_bp.post("/<int:division_id>/members")
@require_super_admin
def add_member(division_id: int):
    """Add a member to the division."""
    division = _get_division_or_404(division_id)

    user_id = request.form.get("user_id")
    event_cycle_id = request.form.get("event_cycle_id")

    if not user_id or not event_cycle_id:
        flash("User and event cycle are required", "error")
        return redirect(url_for(".add_member_form", division_id=division_id))

    # Check for existing membership
    existing = db.session.query(DivisionMembership).filter_by(
        user_id=user_id,
        division_id=division_id,
        event_cycle_id=int(event_cycle_id),
    ).first()

    if existing:
        flash("This user is already a member of this division for this event cycle", "error")
        return redirect(url_for(".add_member_form", division_id=division_id))

    membership = DivisionMembership(
        user_id=user_id,
        division_id=division_id,
        event_cycle_id=int(event_cycle_id),
        is_division_head=request.form.get("is_division_head") == "1",
    )

    db.session.add(membership)
    db.session.flush()  # Get the membership ID

    # Add work type access records
    work_types = _get_active_work_types()
    for wt in work_types:
        wt_can_view = request.form.get(f"wt_{wt.id}_view") == "1"
        wt_can_edit = request.form.get(f"wt_{wt.id}_edit") == "1"

        if wt_can_view or wt_can_edit:
            wta = DivisionMembershipWorkTypeAccess(
                division_membership_id=membership.id,
                work_type_id=wt.id,
                can_view=wt_can_view,
                can_edit=wt_can_edit,
            )
            db.session.add(wta)

    db.session.commit()

    user = db.session.get(User, user_id)
    flash(f"Added {user.display_name} to {division.name}", "success")
    return redirect(url_for(".list_members", division_id=division_id))


@divisions_bp.get("/<int:division_id>/members/<int:membership_id>")
@require_super_admin
def edit_member(division_id: int, membership_id: int):
    """Show form to edit a division membership."""
    division = _get_division_or_404(division_id)

    membership = db.session.get(DivisionMembership, membership_id)
    if not membership or membership.division_id != division_id:
        abort(404, "Membership not found")

    # Get active work types for access configuration
    work_types = _get_active_work_types()

    return render_admin_config_page(
        "admin/divisions/member_form.html",
        division=division,
        membership=membership,
        users=None,  # Can't change user on edit
        event_cycles=None,  # Can't change event cycle on edit
        work_types=work_types,
    )


@divisions_bp.post("/<int:division_id>/members/<int:membership_id>")
@require_super_admin
def update_member(division_id: int, membership_id: int):
    """Update a division membership."""
    division = _get_division_or_404(division_id)

    membership = db.session.get(DivisionMembership, membership_id)
    if not membership or membership.division_id != division_id:
        abort(404, "Membership not found")

    membership.is_division_head = request.form.get("is_division_head") == "1"

    # Update work type access records
    work_types = _get_active_work_types()
    for wt in work_types:
        wt_can_view = request.form.get(f"wt_{wt.id}_view") == "1"
        wt_can_edit = request.form.get(f"wt_{wt.id}_edit") == "1"

        # Find existing access record
        existing_wta = membership.get_work_type_access(wt.id)

        if wt_can_view or wt_can_edit:
            if existing_wta:
                existing_wta.can_view = wt_can_view
                existing_wta.can_edit = wt_can_edit
            else:
                wta = DivisionMembershipWorkTypeAccess(
                    division_membership_id=membership.id,
                    work_type_id=wt.id,
                    can_view=wt_can_view,
                    can_edit=wt_can_edit,
                )
                db.session.add(wta)
        else:
            # Remove access if both are unchecked
            if existing_wta:
                db.session.delete(existing_wta)

    db.session.commit()

    flash(f"Updated membership for {membership.user.display_name}", "success")
    return redirect(url_for(".list_members", division_id=division_id))


@divisions_bp.post("/<int:division_id>/members/<int:membership_id>/delete")
@require_super_admin
def delete_member(division_id: int, membership_id: int):
    """Remove a member from the division."""
    division = _get_division_or_404(division_id)

    membership = db.session.get(DivisionMembership, membership_id)
    if not membership or membership.division_id != division_id:
        abort(404, "Membership not found")

    user_name = membership.user.display_name
    db.session.delete(membership)
    db.session.commit()

    flash(f"Removed {user_name} from {division.name}", "success")
    return redirect(url_for(".list_members", division_id=division_id))
