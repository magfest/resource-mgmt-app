"""
AV request edit endpoint.

GET  /<event>/<dept>/av/item/<public_id>/edit — renders the form pre-filled from the
     existing record.
POST /<event>/<dept>/av/item/<public_id>/edit — saves changes in place.

Only DRAFT requests may be edited.  POST action=save_draft stays DRAFT;
action=submit saves then immediately calls _do_submit from submit.py.
"""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

from app import db
from app.models import (
    Department,
    EventCycle,
    WorkItem,
    WorkLine,
    WorkLineAuditEvent,
    WorkPortfolio,
    WorkType,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_PENDING,
    AUDIT_EVENT_REQUESTER_RESPONSE,
)
from app.models.space import Space, SpaceDepartmentAssignment
from app.routes import get_user_ctx
from app.routes.work.av.permissions import can_create_av_request_for, can_edit_av_request
from .form_utils import ACTION_SUBMIT, AVRequestForm
from .create import _assignable_spaces, _populate_space_choices
from .. import work_bp


@work_bp.route("/<event>/<dept>/av/item/<public_id>/edit", methods=["GET", "POST"])
def av_request_edit(event: str, dept: str, public_id: str):
    """Edit a DRAFT AV request."""
    user_ctx = get_user_ctx()

    event_cycle = EventCycle.query.filter_by(code=event.upper()).first_or_404()
    department = Department.query.filter_by(code=dept.upper()).first_or_404()
    av_wt = WorkType.query.filter_by(code="AV").first_or_404()

    portfolio = WorkPortfolio.query.filter_by(
        event_cycle_id=event_cycle.id,
        department_id=department.id,
        work_type_id=av_wt.id,
    ).first_or_404()

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=portfolio.id,
            is_archived=False,
        )
        .options(
            db.joinedload(WorkItem.lines)
                .joinedload(WorkLine.av_line_detail),
        )
        .first_or_404()
    )

    if not user_ctx.is_super_admin and not can_edit_av_request(user_ctx, work_item):
        abort(403)

    # Editing is allowed for DRAFT requests, and also for SUBMITTED requests
    # whose latest WorkLineReview is NEEDS_ADJUSTMENT (the AV team has asked for
    # changes; the dept responds by editing and re-saving).
    _line = work_item.lines[0] if work_item.lines else None
    _latest_review = (
        max(_line.reviews, key=lambda r: r.id) if _line and _line.reviews else None
    )
    is_needs_adjustment = (
        work_item.status == WORK_ITEM_STATUS_SUBMITTED
        and _latest_review is not None
        and _latest_review.status == REVIEW_STATUS_NEEDS_ADJUSTMENT
    )

    if work_item.status not in (WORK_ITEM_STATUS_DRAFT,) and not is_needs_adjustment:
        flash("Only DRAFT requests (or requests with a pending adjustment) can be edited.", "error")
        return redirect(url_for(
            "work.av_portfolio_landing",
            event=event_cycle.code,
            dept=department.code,
        ))

    detail = work_item.av_request_detail
    line = work_item.lines[0]
    line_detail = line.av_line_detail

    spaces = _assignable_spaces(event_cycle, department)

    if request.method == "GET":
        form = AVRequestForm(data={
            "space_id": detail.space_id,
            "description": line_detail.description,
            "priority": detail.priority,
            "duration_model": detail.duration_model,
            "duration_hours": detail.duration_hours,
            "duration_slots": detail.duration_slots,
            "duration_notes": detail.duration_notes,
            "gear_specificity": line_detail.gear_specificity,
            "suggested_gear_text": line_detail.suggested_gear_text,
            "dept_sourced_gear_mode": detail.dept_sourced_gear_mode,
            "dept_sourced_gear_text": detail.dept_sourced_gear_text,
            "primary_contact_name": detail.primary_contact_name,
            "primary_contact_email": detail.primary_contact_email,
        })
        _populate_space_choices(form, spaces)
        return render_template(
            "av/request_form.html",
            form=form,
            event=event_cycle,
            dept=department,
            assignable_spaces=spaces,
            editing=True,
            work_item=work_item,
        )

    # POST
    form = AVRequestForm(request.form)
    _populate_space_choices(form, spaces)

    if not form.validate_on_submit():
        return render_template(
            "av/request_form.html",
            form=form,
            event=event_cycle,
            dept=department,
            assignable_spaces=spaces,
            editing=True,
            work_item=work_item,
        ), 400

    # Authorise: selected space must still be assigned to this dept
    space = db.session.get(Space, form.space_id.data)
    if space is None or not can_create_av_request_for(user_ctx, space, department):
        abort(403)

    # Update AVRequestDetail
    detail.space_id = space.id
    detail.priority = form.priority.data
    detail.duration_model = form.duration_model.data
    detail.duration_hours = (
        form.duration_hours.data
        if form.duration_model.data == "HOURS_OF_CONTENT"
        else None
    )
    detail.duration_slots = (
        form.duration_slots.data
        if form.duration_model.data == "MULTIPLE_SLOTS"
        else None
    )
    detail.duration_notes = form.duration_notes.data or None
    detail.dept_sourced_gear_mode = form.dept_sourced_gear_mode.data
    detail.dept_sourced_gear_text = form.dept_sourced_gear_text.data or None
    detail.primary_contact_name = form.primary_contact_name.data
    detail.primary_contact_email = form.primary_contact_email.data
    detail.updated_by_user_id = user_ctx.user_id

    # Update AVLineDetail
    line_detail.description = form.description.data
    line_detail.gear_specificity = form.gear_specificity.data
    line_detail.suggested_gear_text = form.suggested_gear_text.data or None

    # If the request was in NEEDS_ADJUSTMENT kickback state, reset the review to
    # PENDING now that the dept has made the requested adjustments.
    if is_needs_adjustment and _latest_review is not None:
        _latest_review.status = REVIEW_STATUS_PENDING
        audit = WorkLineAuditEvent(
            work_line_id=_line.id,
            event_type=AUDIT_EVENT_REQUESTER_RESPONSE,
            note="Dept resubmitted after NEEDS_ADJUSTMENT edit.",
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(audit)

    db.session.commit()

    if form.action.data == ACTION_SUBMIT:
        from .submit import _do_submit
        _do_submit(work_item, user_ctx)
        flash(
            "AV request submitted! The AV team will review it and may reach out with questions.",
            "success",
        )
    else:
        flash(f"{public_id} draft saved.", "success")

    return redirect(url_for(
        "work.av_portfolio_landing",
        event=event_cycle.code,
        dept=department.code,
    ))
