"""
Admin routes for AV Space management.

Spaces are per-event physical areas managed by WORKTYPE_ADMIN(AV) holders.
Requires WORKTYPE_ADMIN(AV) or SUPER_ADMIN for all routes.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash, render_template

from datetime import datetime

from app import db
from app.models import (
    Department,
    EventCycle,
    WorkItem,
    ActivityEvent,
)
from app.models.constants import (
    ACTIVITY_AV_SPACE_CREATED,
    ACTIVITY_AV_SPACE_DEPT_ASSIGNED,
    ACTIVITY_AV_SPACE_DEPT_UNASSIGNED,
)
from app.models.space import Space, SpaceDepartmentAssignment
from app.models.av import AVRequestDetail
from app.routes import h, get_user_ctx
from app.routes.work.av.permissions import require_av_admin

av_spaces_bp = Blueprint("av_spaces", __name__, url_prefix="/admin/av/spaces")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_space_or_404(space_id: int) -> Space:
    """Get a Space by ID or abort with 404."""
    space = db.session.get(Space, space_id)
    if not space:
        abort(404, "Space not found")
    return space


def _get_av_request_count_for_space(space_id: int) -> int:
    """Count of AV requests (non-DRAFT) referencing this space.

    Used to gate code edits — once non-draft requests exist, code is frozen.
    """
    return (
        db.session.query(WorkItem)
        .join(AVRequestDetail, AVRequestDetail.work_item_id == WorkItem.id)
        .filter(AVRequestDetail.space_id == space_id)
        .filter(WorkItem.status != "DRAFT")
        .count()
    )


def _get_default_event_cycle() -> EventCycle | None:
    """Return the current default active EventCycle, or None if none set."""
    return EventCycle.query.filter_by(is_default=True, is_active=True).first()


def _parse_datetime(value: str | None):
    """Parse a datetime-local string (YYYY-MM-DDTHH:MM) into a datetime object."""
    from datetime import datetime
    if not value or not value.strip():
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _render_av_admin_page(template: str, **ctx):
    """Render an AV admin page. Caller must have already verified permission."""
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx, **ctx)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@av_spaces_bp.get("/")
def list_spaces():
    """List all Spaces for the active (default) EventCycle."""
    user_ctx = get_user_ctx()
    require_av_admin(user_ctx)

    event_cycle = _get_default_event_cycle()
    spaces = []
    if event_cycle:
        spaces = (
            Space.query
            .filter_by(event_cycle_id=event_cycle.id)
            .order_by(Space.name.asc())
            .all()
        )

    # Optionally include inactive when requested
    show_inactive = request.args.get("show_inactive") == "1"
    if not show_inactive:
        spaces = [s for s in spaces if s.is_active]

    return _render_av_admin_page(
        "admin/av_spaces/list.html",
        spaces=spaces,
        event_cycle=event_cycle,
        show_inactive=show_inactive,
    )


@av_spaces_bp.get("/new")
def new_space():
    """Show the new Space form."""
    user_ctx = get_user_ctx()
    require_av_admin(user_ctx)

    event_cycle = _get_default_event_cycle()
    return _render_av_admin_page(
        "admin/av_spaces/form.html",
        space=None,
        event_cycle=event_cycle,
        code_locked=False,
        av_request_count=0,
    )


@av_spaces_bp.post("/new")
def create_space():
    """Create a new Space."""
    user_ctx = get_user_ctx()
    require_av_admin(user_ctx)

    event_cycle = _get_default_event_cycle()
    if not event_cycle:
        flash("No active default event cycle found. Cannot create a Space.", "error")
        return redirect(url_for(".new_space"))

    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required.", "error")
        return redirect(url_for(".new_space"))

    if len(code) > 64:
        flash("Code must be 64 characters or less.", "error")
        return redirect(url_for(".new_space"))

    # Duplicate code check (per event cycle)
    existing = Space.query.filter_by(
        event_cycle_id=event_cycle.id, code=code,
    ).first()
    if existing:
        flash(f"A Space with code '{code}' already exists for this event.", "error")
        return redirect(url_for(".new_space"))

    space = Space(
        event_cycle_id=event_cycle.id,
        code=code,
        name=name,
        location=(request.form.get("location") or "").strip() or None,
        square_feet=_safe_int_or_none(request.form.get("square_feet")),
        push_in_at=_parse_datetime(request.form.get("push_in_at")),
        push_out_at=_parse_datetime(request.form.get("push_out_at")),
        notes=(request.form.get("notes") or "").strip() or None,
        is_active=True,
        created_by_user_id=h.get_active_user_id(),
        updated_by_user_id=h.get_active_user_id(),
    )
    db.session.add(space)
    db.session.flush()

    db.session.add(ActivityEvent(
        event_type=ACTIVITY_AV_SPACE_CREATED,
        space_id=space.id,
        actor_user_id=h.get_active_user_id(),
    ))

    db.session.commit()
    flash(f"Created space: {space.name}", "success")
    return redirect(url_for(".list_spaces"))


@av_spaces_bp.get("/<int:space_id>/edit")
def edit_space(space_id: int):
    """Show the edit form for a Space."""
    user_ctx = get_user_ctx()
    require_av_admin(user_ctx)

    space = _get_space_or_404(space_id)
    av_request_count = _get_av_request_count_for_space(space_id)
    code_locked = av_request_count > 0

    return _render_av_admin_page(
        "admin/av_spaces/form.html",
        space=space,
        event_cycle=space.event_cycle,
        code_locked=code_locked,
        av_request_count=av_request_count,
    )


@av_spaces_bp.post("/<int:space_id>/edit")
def update_space(space_id: int):
    """Update a Space."""
    user_ctx = get_user_ctx()
    require_av_admin(user_ctx)

    space = _get_space_or_404(space_id)

    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Name is required.", "error")
        return redirect(url_for(".edit_space", space_id=space_id))

    # Code handling: respect lock
    av_request_count = _get_av_request_count_for_space(space_id)
    code_locked = av_request_count > 0

    if code_locked:
        # Ignore whatever code was submitted — keep existing
        new_code = space.code
    else:
        new_code = (request.form.get("code") or "").strip().upper()
        if not new_code:
            flash("Code is required.", "error")
            return redirect(url_for(".edit_space", space_id=space_id))
        if len(new_code) > 64:
            flash("Code must be 64 characters or less.", "error")
            return redirect(url_for(".edit_space", space_id=space_id))
        # Check for duplicate code (excluding self)
        if new_code != space.code:
            existing = Space.query.filter(
                Space.event_cycle_id == space.event_cycle_id,
                Space.code == new_code,
                Space.id != space_id,
            ).first()
            if existing:
                flash(f"A Space with code '{new_code}' already exists for this event.", "error")
                return redirect(url_for(".edit_space", space_id=space_id))

    space.code = new_code
    space.name = name
    space.location = (request.form.get("location") or "").strip() or None
    space.square_feet = _safe_int_or_none(request.form.get("square_feet"))
    space.push_in_at = _parse_datetime(request.form.get("push_in_at"))
    space.push_out_at = _parse_datetime(request.form.get("push_out_at"))
    space.notes = (request.form.get("notes") or "").strip() or None
    space.updated_by_user_id = h.get_active_user_id()

    db.session.commit()
    flash(f"Updated space: {space.name}", "success")
    return redirect(url_for(".list_spaces"))


@av_spaces_bp.post("/<int:space_id>/archive")
def archive_space(space_id: int):
    """Soft-archive a Space (set is_active=False)."""
    user_ctx = get_user_ctx()
    require_av_admin(user_ctx)

    space = _get_space_or_404(space_id)

    if not space.is_active:
        flash("Space is already archived.", "warning")
        return redirect(url_for(".list_spaces"))

    space.is_active = False
    space.updated_by_user_id = h.get_active_user_id()

    db.session.commit()
    flash(f"Archived space: {space.name}", "success")
    return redirect(url_for(".list_spaces"))


@av_spaces_bp.route("/<int:space_id>/assignments", methods=["GET", "POST"])
def manage_assignments(space_id: int):
    """GET: list all departments with assigned/unassigned status for a Space.
    POST: assign or unassign one department (action + department_id form fields).
    """
    user_ctx = get_user_ctx()
    require_av_admin(user_ctx)

    space = _get_space_or_404(space_id)

    if request.method == "POST":
        action = request.form.get("action")
        dept_id_raw = request.form.get("department_id")
        if not dept_id_raw:
            flash("department_id is required.", "error")
            return redirect(url_for(".manage_assignments", space_id=space_id))

        dept_id = int(dept_id_raw)
        dept = db.session.get(Department, dept_id)
        if not dept:
            abort(404, "Department not found")

        if action == "assign":
            # Check if active assignment already exists; if so, no-op
            existing = SpaceDepartmentAssignment.query.filter_by(
                space_id=space_id, department_id=dept_id, unassigned_at=None,
            ).first()
            if existing is None:
                assignment = SpaceDepartmentAssignment(
                    space_id=space_id,
                    department_id=dept_id,
                    assigned_at=datetime.utcnow(),
                    assigned_by_user_id=h.get_active_user_id(),
                )
                db.session.add(assignment)
                db.session.add(ActivityEvent(
                    event_type=ACTIVITY_AV_SPACE_DEPT_ASSIGNED,
                    space_id=space_id,
                    actor_user_id=h.get_active_user_id(),
                ))
                db.session.commit()
                flash(f"Assigned {dept.name} to {space.name}.", "success")

        elif action == "unassign":
            existing = SpaceDepartmentAssignment.query.filter_by(
                space_id=space_id, department_id=dept_id, unassigned_at=None,
            ).first()
            if existing:
                existing.unassigned_at = datetime.utcnow()
                existing.unassigned_by_user_id = h.get_active_user_id()
                db.session.add(ActivityEvent(
                    event_type=ACTIVITY_AV_SPACE_DEPT_UNASSIGNED,
                    space_id=space_id,
                    actor_user_id=h.get_active_user_id(),
                ))
                db.session.commit()
                flash(f"Unassigned {dept.name} from {space.name}.", "success")

        else:
            flash(f"Unknown action: {action!r}", "error")

        return redirect(url_for(".manage_assignments", space_id=space_id))

    # GET: list all departments with assigned status
    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()
    active_assignments = SpaceDepartmentAssignment.query.filter_by(
        space_id=space_id, unassigned_at=None,
    ).all()
    assigned_ids = {a.department_id for a in active_assignments}

    return _render_av_admin_page(
        "admin/av_spaces/assignments.html",
        space=space,
        departments=departments,
        assigned_ids=assigned_ids,
    )


@av_spaces_bp.post("/clone-from-previous")
def clone_from_previous():
    """Bulk-clone Spaces (and optionally their assignments) from the most recent
    prior EventCycle into a target EventCycle.

    Idempotent: spaces whose ``code`` already exists in the target cycle are
    skipped without modification.
    """
    user_ctx = get_user_ctx()
    require_av_admin(user_ctx)

    target_event_id = int(request.form.get("event_cycle_id"))
    target_event = EventCycle.query.get_or_404(target_event_id)

    skip_assignments = bool(request.form.get("skip_assignments"))

    # Most recent prior event by sort_order (descending), excluding target.
    # Falls back to id ordering if sort_order is NULL.
    prior_event = (
        EventCycle.query
        .filter(EventCycle.id != target_event_id)
        .order_by(
            EventCycle.sort_order.desc().nullslast(),
            EventCycle.id.desc(),
        )
        .first()
    )

    if prior_event is None:
        flash("No prior event found to clone from.", "warning")
        return redirect(url_for(".list_spaces"))

    # Collect codes already in target so we can skip duplicates.
    existing_codes = {
        s.code for s in Space.query.filter_by(event_cycle_id=target_event_id).all()
    }

    prior_spaces = Space.query.filter_by(
        event_cycle_id=prior_event.id, is_active=True,
    ).all()

    cloned_count = 0
    skipped_count = 0
    actor_id = h.get_active_user_id()

    for prior_space in prior_spaces:
        if prior_space.code in existing_codes:
            skipped_count += 1
            continue

        new_space = Space(
            event_cycle_id=target_event_id,
            code=prior_space.code,
            name=prior_space.name,
            location=prior_space.location,
            square_feet=prior_space.square_feet,
            push_in_at=None,   # event-specific; intentionally not carried over
            push_out_at=None,
            notes=prior_space.notes,
            is_active=True,
            created_by_user_id=actor_id,
            updated_by_user_id=actor_id,
        )
        db.session.add(new_space)
        db.session.flush()  # populate new_space.id before referencing it

        if not skip_assignments:
            for prior_assignment in prior_space.assignments:
                if prior_assignment.unassigned_at is not None:
                    continue  # skip historical (already-unassigned) rows
                new_assignment = SpaceDepartmentAssignment(
                    space_id=new_space.id,
                    department_id=prior_assignment.department_id,
                    assigned_at=datetime.utcnow(),
                    assigned_by_user_id=actor_id,
                )
                db.session.add(new_assignment)

        db.session.add(ActivityEvent(
            event_type=ACTIVITY_AV_SPACE_CREATED,
            space_id=new_space.id,
            actor_user_id=actor_id,
        ))

        cloned_count += 1

    db.session.commit()

    flash(
        f"Cloned {cloned_count} space(s) from {prior_event.name}. "
        f"Skipped {skipped_count} (codes already existed).",
        "success",
    )
    return redirect(url_for(".list_spaces"))


# ---------------------------------------------------------------------------
# Module-level utility (kept here, not imported from helpers to avoid circular)
# ---------------------------------------------------------------------------

def _safe_int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
