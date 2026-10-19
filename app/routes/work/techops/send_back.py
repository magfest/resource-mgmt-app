"""Return a submitted TechOps order to draft.

Two actions share one reset. A department editor pulls back an order nobody
at TechOps has decided yet. A TechOps admin sends back any submitted or
finalized order, with a reason the department sees. The event's TechOps
deadline also ends the pull-back; the send-back has no date limit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from flask import flash, redirect, request, url_for

from app import db
from app.models import (
    EventCycle,
    WorkItem,
    WorkItemComment,
    WorkLineReview,
    COMMENT_VISIBILITY_PUBLIC,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes import get_user_ctx
from app.services.email_windows import eastern_date_to_utc_end
from app.services.notifications import notify_sent_back
from .. import work_bp
from ..helpers import (
    build_portfolio_perms,
    build_work_item_perms,
    get_portfolio_context,
)
from ..helpers.checkout import is_checked_out
from ..helpers.context import WorkItemPerms
from ..helpers.lifecycle import (
    RETURN_ACTION_PULL_BACK,
    RETURN_ACTION_SEND_BACK,
    reset_submitted_to_draft,
)

NOT_ALLOWED = (
    "This request can no longer be returned to draft. "
    "TechOps may have started reviewing it."
)
DEADLINE_PASSED = (
    "The TechOps deadline has passed. Ask for changes in "
    "#super-techops-requests."
)


def pull_back_deadline_passed(event_cycle: EventCycle, now: datetime | None = None) -> bool:
    """Return True once the deadline day has ended in US Eastern time.

    The server clock is UTC, so comparing against the UTC date would close the
    window four or five hours early, depending on daylight saving.
    """
    deadline = event_cycle.techops_submission_deadline
    if deadline is None:
        return False
    return (now or datetime.utcnow()) > eastern_date_to_utc_end(deadline)


def _short_date(d) -> str:
    # Built by hand: strftime's %-d is not portable to Windows.
    return f"{d:%b} {d.day}"


def submitted_message(event_cycle: EventCycle, now: datetime | None = None) -> str:
    """Return the flash shown after a department submits."""
    deadline = event_cycle.techops_submission_deadline
    if deadline is None:
        return ("TechOps request submitted. You can return it to draft until "
                "TechOps starts reviewing it.")
    if pull_back_deadline_passed(event_cycle, now):
        return "TechOps request submitted. " + DEADLINE_PASSED
    return (f"TechOps request submitted. You can return it to draft until "
            f"{_short_date(deadline)}, as long as TechOps has not started "
            f"reviewing it.")


@dataclass(frozen=True)
class ReturnOptions:
    can_pull_back: bool
    can_send_back: bool
    # The sentence the department reads under the action bar; None shows nothing.
    pull_back_note: str | None = None


def _all_reviews_pending(work_item: WorkItem) -> bool:
    line_ids = [line.id for line in work_item.lines]
    if not line_ids:
        return True
    decided = (
        WorkLineReview.query
        .filter(WorkLineReview.work_line_id.in_(line_ids))
        .filter(WorkLineReview.stage == REVIEW_STAGE_APPROVAL_GROUP)
        .filter(WorkLineReview.status != REVIEW_STATUS_PENDING)
        .first()
    )
    return decided is None


def techops_return_options(
    work_item: WorkItem,
    perms: WorkItemPerms,
    portfolio_can_edit: bool,
    user_id: str,
) -> ReturnOptions:
    """Return which of the two return-to-draft actions this user may take.

    TechOps never grants a checkout, so the lock checks only matter for a
    stale lock left on the row. An admin is offered the send-back alone, even
    on an unreviewed order, so the department always gets a reason.
    """
    event_cycle = work_item.portfolio.event_cycle
    deadline_passed = pull_back_deadline_passed(event_cycle)
    locked = is_checked_out(work_item)
    locked_by_other = locked and work_item.checked_out_by_user_id != user_id

    can_send_back = (
        perms.is_worktype_admin
        and work_item.status in (WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED)
        and not locked_by_other
    )
    is_department_editor = (
        not perms.is_worktype_admin
        and portfolio_can_edit
        and work_item.status == WORK_ITEM_STATUS_SUBMITTED
    )
    can_pull_back = (
        is_department_editor
        and not deadline_passed
        and not locked
        and _all_reviews_pending(work_item)
    )

    note = None
    deadline = event_cycle.techops_submission_deadline
    if can_pull_back and deadline is not None:
        note = (f"You can return this to draft until {_short_date(deadline)}, "
                f"as long as TechOps has not started reviewing it.")
    elif is_department_editor and deadline_passed:
        note = DEADLINE_PASSED
    return ReturnOptions(
        can_pull_back=can_pull_back, can_send_back=can_send_back, pull_back_note=note)


def _load_locked(event: str, dept: str, public_id: str):
    """Load the item row-locked, then decide what this user may do.

    The options are computed after the lock, so a review decision that lands
    between page load and click refuses the action instead of being erased.
    Returns (work_item or None, ReturnOptions, detail_url).
    """
    ctx = get_portfolio_context(event, dept, "techops")
    detail_url = url_for(
        "work.techops_work_item_detail", event=event, dept=dept, public_id=public_id)
    work_item = WorkItem.query.filter_by(
        public_id=public_id, portfolio_id=ctx.portfolio.id, is_archived=False,
    ).first()
    if work_item is None:
        return None, ReturnOptions(False, False), detail_url

    # OF limits the lock to the request row; PostgreSQL refuses a bare FOR
    # UPDATE across the portfolio outer join.
    db.session.refresh(work_item, with_for_update={"of": WorkItem})
    perms = build_work_item_perms(work_item, ctx)
    options = techops_return_options(
        work_item, perms, build_portfolio_perms(ctx).can_edit, ctx.user_ctx.user_id)
    return work_item, options, detail_url


@work_bp.post("/<event>/<dept>/techops/item/<public_id>/return-to-draft")
def techops_request_pull_back(event: str, dept: str, public_id: str):
    """Let a department editor take back an order nobody has decided yet."""
    work_item, options, detail_url = _load_locked(event, dept, public_id)
    if work_item is None or not options.can_pull_back:
        db.session.rollback()
        flash(DEADLINE_PASSED if options.pull_back_note == DEADLINE_PASSED else NOT_ALLOWED,
              "error")
        return redirect(detail_url)

    reset_submitted_to_draft(work_item, get_user_ctx(), action=RETURN_ACTION_PULL_BACK)
    db.session.commit()
    flash("Request returned to draft. Submit it again when it is ready.", "success")
    return redirect(detail_url)


@work_bp.post("/<event>/<dept>/techops/item/<public_id>/send-back")
def techops_request_send_back(event: str, dept: str, public_id: str):
    """Let a TechOps admin return an order to the department with a reason.

    The reason is stored as a PUBLIC comment, so the department reads it on the
    request page as well as in the email. The status change, comment and
    outbox rows commit together.
    """
    reason = (request.form.get("reason") or "").replace("\r\n", "\n").strip()

    work_item, options, detail_url = _load_locked(event, dept, public_id)
    if work_item is None or not options.can_send_back:
        db.session.rollback()
        flash(NOT_ALLOWED, "error")
        return redirect(detail_url)
    if not reason:
        db.session.rollback()
        flash("Say why you are sending this back. The department sees your reason.", "error")
        return redirect(detail_url)

    user_ctx = get_user_ctx()
    event = reset_submitted_to_draft(
        work_item, user_ctx, action=RETURN_ACTION_SEND_BACK, reason=reason)
    db.session.add(WorkItemComment(
        work_item_id=work_item.id,
        visibility=COMMENT_VISIBILITY_PUBLIC,
        body=reason,
        created_by_user_id=user_ctx.user_id,
    ))
    db.session.flush()  # sets event.created_at, which keys the email dedup
    queued = notify_sent_back(work_item, reason, event.created_at)
    db.session.commit()
    if queued:
        flash("Request sent back to the department. They have been emailed your reason.", "success")
    else:
        flash(
            "Request sent back to the department. No email address was found for "
            "them, so tell them in #super-techops-requests.",
            "warning",
        )
    return redirect(detail_url)
