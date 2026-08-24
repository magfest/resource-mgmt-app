"""
TechOps request creation — the "New Request" sectioned form.

GET renders the empty (or default-populated) form. POST validates and
creates the WorkItem + TechOpsRequestDetail + per-service WorkLines.
Save Draft leaves the request in DRAFT; Submit calls the engine
submit_work_item helper to transition to SUBMITTED, synthesizing one
OTHER-service line first when the no-services-needed affirmation is set
so the affirmation goes through normal review.
"""
from flask import abort, flash, redirect, render_template, request, url_for

from app import db
from app.models import (
    WorkItem,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    generate_public_id_for_portfolio,
    get_portfolio_context,
    require_portfolio_edit,
    require_portfolio_view,
)
from .form_utils import (
    ACTION_SUBMIT,
    active_service_types,
    form_render_kwargs,
    parse_form,
    replace_lines,
    synthesize_no_services_line,
    upsert_request_detail,
    validate,
)


def _do_submit(work_item, user_ctx):
    """Run the submit lifecycle: synthesize the no-services line if needed,
    call submit_work_item, queue the submit notification, commit once, then
    announce on Slack.

    The notification is queued inside the submit transaction; the outbox rows
    and the SUBMITTED status land together or not at all. The announcement is
    a webhook call and runs after the commit.
    """
    from app.routes.work.helpers.lifecycle import submit_work_item

    if work_item.techops_detail and work_item.techops_detail.no_services_needed:
        if not synthesize_no_services_line(work_item, user_ctx):
            db.session.rollback()
            flash(
                "Could not submit: the OTHER service type is missing from the catalog. "
                "Contact a TechOps admin.",
                "error",
            )
            return False
        db.session.flush()

    submit_work_item(work_item, user_ctx)

    # Queue before the commit. notify only INSERTs into email_outbox, so one
    # commit covers the status change and its emails.
    from app.services.notifications import (
        announce_work_item_event,
        notify_work_item_submitted,
    )
    notify_work_item_submitted(work_item)

    db.session.commit()

    # Slack after the commit; it is a webhook call, not a local INSERT.
    announce_work_item_event(work_item, 'submitted')

    return True


@work_bp.get("/<event>/<dept>/techops/new")
def techops_request_new(event: str, dept: str):
    """Render the New TechOps Request form."""
    ctx = get_portfolio_context(event, dept, "techops")
    perms = require_portfolio_view(ctx)

    if not perms.can_create_primary:
        abort(403, "You do not have permission to create a TechOps request for this department.")

    user_ctx = get_user_ctx()
    user = user_ctx.user

    return render_template(
        "techops/work_item_form.html",
        ctx=ctx,
        perms=perms,
        work_item=None,
        request_detail=None,
        existing_lines_by_code={},
        service_types=active_service_types(),
        # Defaults: account display name + email. Help text in the template
        # explains why someone might change these for a specific request.
        default_contact_name=user.display_name if user else "",
        default_contact_email=user.email if user else "",
    )


@work_bp.post("/<event>/<dept>/techops/new")
def techops_request_create(event: str, dept: str):
    """Process the New TechOps Request form."""
    ctx = get_portfolio_context(event, dept, "techops")
    perms = require_portfolio_edit(ctx)

    if not perms.can_create_primary:
        abort(403, "You do not have permission to create a TechOps request for this department.")

    user_ctx = get_user_ctx()

    service_types = active_service_types()
    data = parse_form(request.form, service_types)

    errors = validate(data)
    if errors:
        for err in errors:
            flash(err, "error")
        # Re-render with submitted values preserved (no redirect) so the
        # user sees their input + the errors inline. PRG only applies to
        # successful mutations.
        return render_template(
            "techops/work_item_form.html",
            **form_render_kwargs(data, ctx, perms, service_types, work_item=None),
        )

    work_item = WorkItem(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id=generate_public_id_for_portfolio(ctx.portfolio),
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(work_item)
    db.session.flush()

    upsert_request_detail(work_item, data, user_ctx)
    replace_lines(work_item, data)
    db.session.commit()

    if data.action == ACTION_SUBMIT:
        if not _do_submit(work_item, user_ctx):
            return redirect(url_for(
                "work.techops_work_item_detail",
                event=event, dept=dept, public_id=work_item.public_id,
            ))
        flash(
            "TechOps request submitted! TechOps will reach out if any clarifications are needed.",
            "success",
        )
    else:
        flash("Draft saved.", "success")

    return redirect(url_for(
        "work.techops_work_item_detail",
        event=event, dept=dept, public_id=work_item.public_id,
    ))
