"""The address of a department's TechOps request.

This is not a landing page. A department holds one TechOps request per
event, so the URL has no list to show; it redirects to the request itself
or to the form that starts one. The route stays because it is the natural
URL to type and the one people bookmark. No template in the app links to
it; the department home links straight to the request or the new form.
"""
from flask import flash, redirect, url_for

from app.models import WorkItem
from .. import work_bp
from ..helpers import get_portfolio_context, require_portfolio_view


@work_bp.get("/<event>/<dept>/techops")
def techops_portfolio_redirect(event: str, dept: str):
    """Send the viewer to the department's TechOps request for this event."""
    ctx = get_portfolio_context(event, dept, "techops")
    perms = require_portfolio_view(ctx)

    # Newest wins. One request is the rule, enforced by can_create_primary,
    # so this orders only the rows that predate that gate.
    work_item = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).order_by(WorkItem.created_at.desc()).first()

    if work_item:
        # One destination for a draft and a submitted request both. The
        # detail page carries Edit Draft and Submit, so it is a complete
        # home for either.
        return redirect(url_for(
            "work.techops_work_item_detail",
            event=event, dept=dept, public_id=work_item.public_id,
        ))

    # can_create_primary, not can_edit: it is the gate techops_request_new
    # enforces, so matching it here keeps the redirect from landing on a 403.
    if perms.can_create_primary:
        return redirect(url_for(
            "work.techops_request_new", event=event, dept=dept,
        ))

    # A view-only member has nowhere to go here. Say why instead of
    # bouncing them into the new-request form's 403.
    flash(
        f"{ctx.department.name} has not started a TechOps request for "
        f"{ctx.event_cycle.name} yet.",
        "info",
    )
    return redirect(url_for("work.department_home", event=event, dept=dept))
