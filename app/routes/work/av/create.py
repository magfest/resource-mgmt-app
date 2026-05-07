"""
AV request creation — the "New AV Request" form.

GET renders an empty form (optionally pre-selecting a space via ?space_id=X).
POST with action=save_draft creates WorkPortfolio (if needed), WorkItem (DRAFT),
AVRequestDetail, WorkLine, and AVLineDetail in one transaction.

POST with action=submit does the same, then immediately calls _do_submit from
submit.py to transition DRAFT → SUBMITTED and create the WorkLineReview.
"""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

from app import db
from app.models import (
    AVLineDetail,
    AVRequestDetail,
    Department,
    EventCycle,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_PENDING,
)
from app.models.space import Space, SpaceDepartmentAssignment
from app.routes import get_user_ctx
from app.routes.work.helpers.formatting import generate_public_id_for_portfolio
from app.routes.work.av.permissions import can_create_av_request_for, _user_dept_ids_with_av_access
from .form_utils import ACTION_SAVE_DRAFT, ACTION_SUBMIT, AVRequestForm
from .. import work_bp


def _assignable_spaces(event_cycle: EventCycle, dept: Department) -> list[Space]:
    """Return spaces currently assigned to this dept in this event, active only."""
    return (
        Space.query
        .join(SpaceDepartmentAssignment, SpaceDepartmentAssignment.space_id == Space.id)
        .filter(
            SpaceDepartmentAssignment.department_id == dept.id,
            SpaceDepartmentAssignment.unassigned_at.is_(None),
            Space.event_cycle_id == event_cycle.id,
            Space.is_active.is_(True),
        )
        .order_by(Space.name)
        .all()
    )


def _populate_space_choices(form: AVRequestForm, spaces: list[Space]) -> None:
    """Set form.space_id choices from the provided space list."""
    form.space_id.choices = [(s.id, f"{s.code} — {s.name}") for s in spaces]


@work_bp.get("/<event>/<dept>/av/new")
def av_request_new(event: str, dept: str):
    """Render the empty (or space-pre-filled) New AV Request form."""
    user_ctx = get_user_ctx()

    event_cycle = EventCycle.query.filter_by(code=event.upper()).first_or_404()
    department = Department.query.filter_by(code=dept.upper()).first_or_404()

    # Permission: user must have AV edit access to this dept
    user_dept_ids = _user_dept_ids_with_av_access(user_ctx, edit=True)
    if not user_ctx.is_super_admin and department.id not in user_dept_ids:
        abort(403)

    spaces = _assignable_spaces(event_cycle, department)

    form = AVRequestForm()
    _populate_space_choices(form, spaces)

    # Pre-select space if provided via query string
    preselect_space_id = request.args.get("space_id", type=int)
    if preselect_space_id:
        valid_ids = {s.id for s in spaces}
        if preselect_space_id in valid_ids:
            form.space_id.data = preselect_space_id

    # Pre-fill contact info from the logged-in user
    if user_ctx.user:
        form.primary_contact_name.data = form.primary_contact_name.data or user_ctx.user.display_name or ""
        form.primary_contact_email.data = form.primary_contact_email.data or user_ctx.user.email or ""

    return render_template(
        "av/request_form.html",
        form=form,
        event=event_cycle,
        dept=department,
        assignable_spaces=spaces,
    )


@work_bp.post("/<event>/<dept>/av/new")
def av_request_create(event: str, dept: str):
    """Process the New AV Request form — creates a DRAFT request."""
    user_ctx = get_user_ctx()

    event_cycle = EventCycle.query.filter_by(code=event.upper()).first_or_404()
    department = Department.query.filter_by(code=dept.upper()).first_or_404()
    av_wt = WorkType.query.filter_by(code="AV").first_or_404()

    spaces = _assignable_spaces(event_cycle, department)

    form = AVRequestForm()
    _populate_space_choices(form, spaces)

    if not form.validate_on_submit():
        return render_template(
            "av/request_form.html",
            form=form,
            event=event_cycle,
            dept=department,
            assignable_spaces=spaces,
        ), 400

    # Authorise: space must be assigned to dept AND user must have edit access
    space = db.session.get(Space, form.space_id.data)
    if space is None or not can_create_av_request_for(user_ctx, space, department):
        abort(403)

    # ---- Transaction: get-or-create portfolio, create item + detail + line ----

    portfolio = WorkPortfolio.query.filter_by(
        event_cycle_id=event_cycle.id,
        department_id=department.id,
        work_type_id=av_wt.id,
    ).first()

    if portfolio is None:
        portfolio = WorkPortfolio(
            event_cycle_id=event_cycle.id,
            department_id=department.id,
            work_type_id=av_wt.id,
            created_by_user_id=user_ctx.user_id,
            next_public_id_seq=1,
        )
        db.session.add(portfolio)
        db.session.flush()

    public_id = generate_public_id_for_portfolio(portfolio)

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id=public_id,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(work_item)
    db.session.flush()

    detail = AVRequestDetail(
        work_item_id=work_item.id,
        space_id=space.id,
        priority=form.priority.data,
        duration_model=form.duration_model.data,
        duration_hours=(
            form.duration_hours.data
            if form.duration_model.data == "HOURS_OF_CONTENT"
            else None
        ),
        duration_slots=(
            form.duration_slots.data
            if form.duration_model.data == "MULTIPLE_SLOTS"
            else None
        ),
        duration_notes=form.duration_notes.data or None,
        dept_sourced_gear_mode=form.dept_sourced_gear_mode.data,
        dept_sourced_gear_text=form.dept_sourced_gear_text.data or None,
        primary_contact_name=form.primary_contact_name.data,
        primary_contact_email=form.primary_contact_email.data,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(detail)

    work_line = WorkLine(
        work_item_id=work_item.id,
        line_number=1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(work_line)
    db.session.flush()

    line_detail = AVLineDetail(
        work_line_id=work_line.id,
        description=form.description.data,
        gear_specificity=form.gear_specificity.data,
        suggested_gear_text=form.suggested_gear_text.data or None,
        # routed_approval_group_id NOT set here — happens at submit (Task 25)
    )
    db.session.add(line_detail)

    db.session.commit()

    if form.action.data == ACTION_SUBMIT:
        from .submit import _do_submit
        _do_submit(work_item, user_ctx)
        flash(
            "AV request submitted! The AV team will review it and may reach out with questions.",
            "success",
        )
    else:
        flash(f"Draft saved as {public_id}.", "success")

    return redirect(url_for(
        "work.av_portfolio_landing",
        event=event_cycle.code,
        dept=department.code,
    ))
