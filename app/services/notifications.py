"""
High-level notification functions for work-item lifecycle events.

Each function:
- Gets recipient emails via helper functions
- Renders email template from database
- Sends via send_email() which handles rate limits, debounce, and logging
- Logs warnings for edge cases (no recipients, user not found, etc.)

Names are worktype-neutral (notify_work_item_*). The submit notification
branches on WorkTypeConfig.uses_dispatch to pick between worktype admins
(dispatch flow) and routed approval groups (no-dispatch flow).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from flask import current_app
from typing import List, Set

from app import db
from app.models import (
    WorkItem,
    User,
    UserRole,
    DepartmentMembership,
    DivisionMembership,
    WorkLineReview,
    ROLE_WORKTYPE_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_APPROVER,
)
from .email import send_email
from .email_templates import render_email_template
from .slack import send_slack_message, is_slack_enabled
from .slack_messages import (
    format_submitted, format_dispatched, format_needs_attention,
    format_response_received, format_finalized,
)

logger = logging.getLogger(__name__)


def get_base_url() -> str:
    """Get base URL for email links."""
    return current_app.config.get('BASE_URL', 'https://budget.magfest.org')


def notify_work_item_submitted(work_item: WorkItem) -> int:
    """
    Notify the right people that a new work item was submitted.

    Routing depends on the work type's uses_dispatch flag:
    - uses_dispatch=True: notify worktype admins (so they can dispatch)
    - uses_dispatch=False: notify routed approval groups directly

    Called after: work_item.status transitions out of DRAFT.
    Returns: Number of emails sent.
    """
    sent_count = _send_emails(
        recipients=_get_submit_recipients(work_item),
        template_key='submitted',
        work_item=work_item,
        empty_recipients_msg="No recipients found for submission notification",
    )

    _send_slack(work_item, 'submitted', format_submitted)

    return sent_count


def notify_submission_confirmation(work_item: WorkItem) -> int:
    """
    Send the submitting department a confirmation that their BUDGET
    request was received.

    Audience: same dept-member set used for needs_attention / finalized
    (direct department memberships + members of the department's
    division), so dept leadership gets a paper trail even when a
    deputy clicked Submit.

    BUDGET-only. The 'submitted' template (which targets budget admins
    so they can dispatch) intentionally does NOT cover this audience.
    For non-BUDGET worktypes this function is a silent no-op so
    submit-route callers can stay worktype-neutral.

    The email body shows the requester-set line count and total — the
    template wording explicitly frames these as "requested" so they
    cannot be misread as an approval.

    Returns: Number of emails sent.
    """
    portfolio = work_item.portfolio
    work_type = portfolio.work_type if portfolio else None
    if work_type is None or work_type.code != 'BUDGET':
        return 0

    line_count = 0
    total_requested_cents = 0
    for line in work_item.lines:
        detail = line.budget_detail
        if not detail:
            continue
        line_count += 1
        total_requested_cents += int(detail.unit_price_cents * detail.quantity)

    recipients = _get_department_member_emails(
        department_id=portfolio.department_id,
        event_cycle_id=portfolio.event_cycle_id,
    )

    return _send_emails(
        recipients=recipients,
        template_key='submission_confirmation',
        work_item=work_item,
        empty_recipients_msg=(
            "No department member recipients found for submission_confirmation"
        ),
        extra_context={
            'line_count': line_count,
            'total_requested_dollars': total_requested_cents / 100,
        },
    )


def notify_work_item_dispatched(work_item: WorkItem, approval_group_ids: List[int]) -> int:
    """
    Notify approval group members that a work item is ready for their review.

    Called after: work_item dispatched to approval groups.
    Returns: Number of emails sent.

    The Slack channel announcement fires whenever the dispatch action
    succeeded (approval_group_ids is non-empty), even if no individual
    approvers have email — channel-level visibility is independent of
    whether approvers are configured yet.
    """
    if not approval_group_ids:
        logger.warning(f"No approval groups provided for dispatch notification: {work_item.public_id}")
        return 0

    recipients = _get_approval_group_emails(approval_group_ids)
    sent_count = _send_emails(
        recipients=recipients,
        template_key='dispatched',
        work_item=work_item,
        empty_recipients_msg=(
            f"No approver recipients found for groups {approval_group_ids}"
        ),
    )

    _send_slack(work_item, 'dispatched', format_dispatched)

    return sent_count


def notify_needs_attention(work_item: WorkItem) -> int:
    """
    Notify department members that their work item needs attention.

    Called after: reviewer marks a line as NEEDS_INFO or NEEDS_ADJUSTMENT.
    Returns: Number of emails sent.
    """
    recipients = _get_department_member_emails(
        department_id=work_item.portfolio.department_id,
        event_cycle_id=work_item.portfolio.event_cycle_id,
    )
    sent_count = _send_emails(
        recipients=recipients,
        template_key='needs_attention',
        work_item=work_item,
        empty_recipients_msg="No department member recipients found for needs_attention",
    )

    _send_slack(work_item, 'needs_attention', format_needs_attention)

    return sent_count


def notify_response_received(work_item: WorkItem, reviewer_user_id: str) -> bool:
    """
    Notify the reviewer that the requester has responded to their feedback.

    Called after: requester responds to NEEDS_INFO or NEEDS_ADJUSTMENT
    Returns: True if email sent, False otherwise
    """
    user = db.session.query(User).filter_by(id=reviewer_user_id).first()
    if not user:
        logger.warning(f"Reviewer user not found for response notification: user_id={reviewer_user_id}, work_item={work_item.public_id}")
        return False

    if not user.email:
        logger.warning(f"Reviewer has no email for response notification: user_id={reviewer_user_id}, work_item={work_item.public_id}")
        return False

    # Render template from database
    rendered = render_email_template('response_received', {
        'work_item': work_item,
        'base_url': get_base_url(),
    })

    if not rendered:
        logger.error(f"Failed to render 'response_received' template for {work_item.public_id}")
        return False

    success = send_email(
        to=user.email,
        subject=rendered.subject,
        body_text=rendered.body_text,
        template_key='response_received',
        work_item_id=work_item.id,
        recipient_user_id=user.id,
    )

    if success:
        logger.info(f"Sent response_received notification to {user.email} for {work_item.public_id}")

    # Slack channel notification
    if is_slack_enabled():
        text, blocks = format_response_received(work_item)
        send_slack_message(text=text, blocks=blocks, template_key='response_received', work_item_id=work_item.id)

    return success


def notify_work_item_finalized(work_item: WorkItem) -> int:
    """
    Notify department members that their work item has been finalized.

    Called after: admin finalizes the work item.
    Returns: Number of emails sent.
    """
    recipients = _get_department_member_emails(
        department_id=work_item.portfolio.department_id,
        event_cycle_id=work_item.portfolio.event_cycle_id,
    )
    sent_count = _send_emails(
        recipients=recipients,
        template_key='finalized',
        work_item=work_item,
        empty_recipients_msg="No department member recipients found for finalized notification",
    )

    _send_slack(work_item, 'finalized', format_finalized)

    return sent_count


# ============================================================
# Email + Slack send helpers
# ============================================================

def _send_emails(
    recipients: List[str],
    template_key: str,
    work_item: WorkItem,
    empty_recipients_msg: str,
    extra_context: dict | None = None,
) -> int:
    """
    Render a DB-backed email template and send to each recipient.

    Returns the number of emails actually sent. Logs warnings for empty
    recipient lists and errors for template-render failures, but does
    not raise — callers depend on this being non-blocking.

    `extra_context` lets a caller pass template variables beyond the
    default `work_item` / `base_url` (e.g. precomputed line totals).
    Keys in `extra_context` win on collision.
    """
    if not recipients:
        logger.warning(f"{empty_recipients_msg}: {work_item.public_id}")
        return 0

    context = {
        'work_item': work_item,
        'base_url': get_base_url(),
    }
    if extra_context:
        context.update(extra_context)

    rendered = render_email_template(template_key, context)

    if not rendered:
        logger.error(f"Failed to render {template_key!r} template for {work_item.public_id}")
        return 0

    sent_count = 0
    for email in recipients:
        if send_email(
            to=email,
            subject=rendered.subject,
            body_text=rendered.body_text,
            template_key=template_key,
            work_item_id=work_item.id,
        ):
            sent_count += 1

    logger.info(
        f"Sent {sent_count}/{len(recipients)} {template_key} notifications "
        f"for {work_item.public_id}"
    )
    return sent_count


def _send_slack(work_item: WorkItem, template_key: str, formatter) -> None:
    """
    Send the channel-level Slack announcement for a work-item event.

    Fires whenever Slack is enabled — independent of whether email
    recipients exist. Channel announcements are about visibility for the
    whole team, not personal notifications.
    """
    if not is_slack_enabled():
        return
    text, blocks = formatter(work_item)
    send_slack_message(
        text=text,
        blocks=blocks,
        template_key=template_key,
        work_item_id=work_item.id,
    )


# ============================================================
# Recipient Helpers
# ============================================================

def _get_worktype_admin_emails(work_type_id: int) -> List[str]:
    """
    Get emails of users who should receive submit notifications for a work type.

    Includes: SUPER_ADMIN (always) and WORKTYPE_ADMIN scoped to this work type
    or unscoped (legacy WORKTYPE_ADMIN rows without a work_type_id).
    """
    emails: Set[str] = set()

    admin_roles = db.session.query(UserRole).filter(
        UserRole.role_code.in_([ROLE_SUPER_ADMIN, ROLE_WORKTYPE_ADMIN])
    ).all()

    relevant_user_ids = []
    for role in admin_roles:
        if role.role_code == ROLE_SUPER_ADMIN:
            relevant_user_ids.append(role.user_id)
        elif role.role_code == ROLE_WORKTYPE_ADMIN:
            if role.work_type_id == work_type_id or role.work_type_id is None:
                relevant_user_ids.append(role.user_id)

    if relevant_user_ids:
        users = db.session.query(User).filter(User.id.in_(relevant_user_ids)).all()
        for user in users:
            if user.is_active and user.email:
                emails.add(user.email)

    return list(emails)


def _get_submit_recipients(work_item: WorkItem) -> List[str]:
    """
    Pick submit-notification recipients based on the work type's uses_dispatch flag.

    - uses_dispatch=True: notify worktype admins (they'll dispatch)
    - uses_dispatch=False: notify routed approval groups directly

    For the no-dispatch path, approval groups are computed by running the
    routing strategy on each line. If the strategy isn't ready (e.g. a
    not-yet-implemented worktype), we log and return [] rather than raise.
    """
    portfolio = work_item.portfolio
    work_type = portfolio.work_type if portfolio else None
    config = work_type.config if work_type else None

    if config is None:
        logger.warning(
            f"No WorkTypeConfig for {work_item.public_id}; cannot pick submit recipients"
        )
        return []

    if config.uses_dispatch:
        return _get_worktype_admin_emails(work_type.id)

    # No-dispatch worktype — recipients are the routed approval groups.
    from app.routing.registry import get_approval_group_for_line

    group_ids: Set[int] = set()
    for line in work_item.lines:
        try:
            group = get_approval_group_for_line(line)
        except ValueError:
            logger.exception(
                f"Routing failed for line {line.id} on {work_item.public_id} "
                f"during submit notification — skipping line"
            )
            continue
        if group:
            group_ids.add(group.id)

    if not group_ids:
        logger.warning(
            f"No routed approval groups found for {work_item.public_id} "
            f"(work type {work_type.code}) — no submit notification recipients"
        )
        return []

    return _get_approval_group_emails(list(group_ids))


def _get_approval_group_emails(group_ids: List[int]) -> List[str]:
    """
    Get emails of users who are approvers for the given approval groups.
    """
    if not group_ids:
        return []

    emails: Set[str] = set()

    # Find users with APPROVER role for these groups
    approver_roles = db.session.query(UserRole).filter(
        UserRole.role_code == ROLE_APPROVER,
        UserRole.approval_group_id.in_(group_ids),
    ).all()

    # Batch load all users in one query
    user_ids = [role.user_id for role in approver_roles]
    if user_ids:
        users = db.session.query(User).filter(User.id.in_(user_ids)).all()
        for user in users:
            if user.is_active and user.email:
                emails.add(user.email)

    return list(emails)


def _get_department_member_emails(
    department_id: int,
    event_cycle_id: int,
    include_division_members: bool = True,
) -> List[str]:
    """
    Get emails of department members for an event.

    By default returns the union of direct department members
    (DepartmentMembership) and members of the department's parent division
    (DivisionMembership) — the latter inherit access to every department in
    their division. Existing notification callers (submitted, needs_attention,
    finalized, submission_confirmation) want both because divisional heads
    follow individual requests.

    Set include_division_members=False to skip the division-membership
    expansion. The submission_reminder flow does this because a division
    head with 15 departments would otherwise receive 15 copies of an
    identical reminder; direct dept members are sufficient to drive the
    submit action.
    """
    from app.models import Department

    emails: Set[str] = set()
    user_ids: Set[str] = set()

    # Direct department memberships
    dept_memberships = db.session.query(DepartmentMembership).filter_by(
        department_id=department_id,
        event_cycle_id=event_cycle_id,
    ).all()

    for m in dept_memberships:
        user_ids.add(m.user_id)

    # Division memberships (for departments within that division) — optional
    if include_division_members:
        dept = db.session.query(Department).get(department_id)
        if dept and dept.division_id:
            div_memberships = db.session.query(DivisionMembership).filter_by(
                division_id=dept.division_id,
                event_cycle_id=event_cycle_id,
            ).all()

            for m in div_memberships:
                user_ids.add(m.user_id)

    # Batch load all users in one query
    if user_ids:
        users = db.session.query(User).filter(User.id.in_(user_ids)).all()
        for user in users:
            if user.is_active and user.email:
                emails.add(user.email)

    return list(emails)


# ============================================================
# Submission-reminder audience query (manual-trigger stopgap)
# ============================================================


@dataclass(frozen=True)
class ReminderTarget:
    """One department to be reminded, plus its pre-computed recipient list."""
    department_id: int
    department_code: str
    department_name: str
    recipient_emails: list[str]


def get_departments_needing_submission_reminder(
    event_cycle: 'EventCycle',
) -> list['ReminderTarget']:
    """
    Find every department participating in `event_cycle` that has NOT yet
    started a PRIMARY BUDGET submission (no work item out of DRAFT).

    Scoped end-to-end to the single `event_cycle` argument. Work items,
    memberships, and enablement rows from other events are never consulted.

    Returns a list sorted by department code for deterministic dry-run
    output. Departments with no human recipients are still returned (with
    empty recipient_emails) so the caller can warn instead of silently
    dropping them.
    """
    # Function-level imports to avoid module-load-time coupling between
    # the service layer and route helpers (see feedback_circular_imports).
    from app.routes.work.helpers.event_enablement import (
        get_enabled_departments_for_event,
    )
    from app.models import (
        WorkType,
        WorkPortfolio,
        WorkItem,
        REQUEST_KIND_PRIMARY,
        WORK_ITEM_STATUS_DRAFT,
    )

    # 1. Get departments enabled for this event (already handles the
    #    EventCycleDepartment.is_enabled flag AND the division-cascade
    #    where a disabled division excludes all its departments).
    enabled_depts = get_enabled_departments_for_event(event_cycle.id)
    if not enabled_depts:
        return []

    # 2. Identify departments that ALREADY have a PRIMARY BUDGET work item
    #    out of DRAFT for this event. NOT EXISTS via a subquery — cleaner
    #    than LEFT JOIN IS NULL when a dept has multiple work items.
    budget_wt = db.session.query(WorkType).filter_by(code="BUDGET").first()
    if budget_wt is None:
        logger.warning(
            "BUDGET WorkType not seeded; no departments to remind. "
            "(Run `flask seed bootstrap` if this is a fresh DB.)"
        )
        return []

    submitted_dept_rows = db.session.query(WorkPortfolio.department_id).join(
        WorkItem, WorkItem.portfolio_id == WorkPortfolio.id,
    ).filter(
        WorkPortfolio.event_cycle_id == event_cycle.id,
        WorkPortfolio.work_type_id == budget_wt.id,
        WorkItem.request_kind == REQUEST_KIND_PRIMARY,
        WorkItem.status != WORK_ITEM_STATUS_DRAFT,
    ).distinct().all()
    submitted_dept_ids = {row.department_id for row in submitted_dept_rows}

    # 3. Build the target list for departments still needing a reminder.
    targets: list[ReminderTarget] = []
    for dept in enabled_depts:
        if dept.id in submitted_dept_ids:
            continue
        # Direct department members only — DivisionMembership members would
        # otherwise receive one reminder per department in their division
        # (a div head with 15 depts gets 15 near-identical emails). Per-target
        # scoping is sufficient to drive the submit action.
        recipients = _get_department_member_emails(
            department_id=dept.id,
            event_cycle_id=event_cycle.id,
            include_division_members=False,
        )
        targets.append(ReminderTarget(
            department_id=dept.id,
            department_code=dept.code,
            department_name=dept.name,
            recipient_emails=recipients,
        ))

    # 4. Sort by department code for deterministic dry-run output.
    targets.sort(key=lambda t: t.department_code)
    return targets


# --- Orchestrator ---


@dataclass
class ReminderRunSummary:
    """Result of a send_submission_reminders run (live or dry)."""
    event_code: str
    targets_total: int
    targets_with_recipients: int
    targets_without_recipients: list[str]   # dept codes
    emails_sent: int
    emails_attempted: int
    dry_run: bool


def send_submission_reminders(
    event_cycle: 'EventCycle',
    dry_run: bool,
) -> 'ReminderRunSummary':
    """
    Render the 'submission_reminder' template once per department and send
    one copy per recipient via the existing send_email().

    Template context per render:
        department  — the Department being reminded
        event_cycle — the EventCycle
        base_url    — from get_base_url()

    When dry_run=True, no send_email() calls are made; the summary still
    reports targets and counts. The orchestrator does no DB writes of its
    own; send_email() owns NotificationLog. send_email() exceptions are
    contained per-recipient so one bad row does not abort the run.
    """
    # Function-level import follows existing pattern in this file
    # (_get_department_member_emails does the same for Department).
    from app.models import Department

    logger.info(
        f"Starting submission reminders for {event_cycle.code} "
        f"(dry_run={dry_run})"
    )

    targets = get_departments_needing_submission_reminder(event_cycle)

    summary = ReminderRunSummary(
        event_code=event_cycle.code,
        targets_total=len(targets),
        targets_with_recipients=sum(1 for t in targets if t.recipient_emails),
        targets_without_recipients=[
            t.department_code for t in targets if not t.recipient_emails
        ],
        emails_sent=0,
        emails_attempted=0,
        dry_run=dry_run,
    )

    for code in summary.targets_without_recipients:
        logger.warning(
            f"Submission-reminder target {code} has no recipients; skipping send."
        )

    if dry_run:
        logger.info(
            f"Completed (dry-run): would send to "
            f"{sum(len(t.recipient_emails) for t in targets)} recipients across "
            f"{summary.targets_with_recipients} departments"
        )
        return summary

    base_url = get_base_url()

    # Per-target render failures (`continue`) and per-recipient send failures
    # (`try/except`) are handled asymmetrically by design: a render failure is
    # almost always a programming error (missing template, Jinja syntax, gone
    # department) that affects every recipient of that target the same way,
    # while a send failure may be a transient per-recipient SES issue.
    for target in targets:
        if not target.recipient_emails:
            continue

        dept = db.session.get(Department, target.department_id)
        if dept is None:
            # Department row vanished between the audience query and now (race
            # with admin delete). Skip rather than letting Jinja AttributeError
            # propagate past render_email_template's UndefinedError handler.
            logger.error(
                f"Department id={target.department_id} disappeared mid-run; "
                f"skipping {len(target.recipient_emails)} recipients for "
                f"{target.department_code}."
            )
            continue

        # Render once per department (department + event_cycle vary across targets,
        # but are identical for all recipients within one target).
        rendered = render_email_template('submission_reminder', {
            'department': dept,
            'event_cycle': event_cycle,
            'base_url': base_url,
        })
        if not rendered:
            logger.error(
                f"Failed to render submission_reminder template for "
                f"{target.department_code}; skipping {len(target.recipient_emails)} "
                f"recipients."
            )
            continue

        for email in target.recipient_emails:
            summary.emails_attempted += 1
            try:
                ok = send_email(
                    to=email,
                    subject=rendered.subject,
                    body_text=rendered.body_text,
                    template_key='submission_reminder',
                    work_item_id=None,
                )
            except Exception:
                logger.exception(
                    f"send_email raised for {email} "
                    f"(dept={target.department_code}); continuing run."
                )
                ok = False

            # send_email() adds the NotificationLog row but does not commit
            # (per its docstring contract: "Caller handles commit"). There is
            # NO implicit commit anywhere — Flask-SQLAlchemy rolls back at
            # request teardown. HTTP routes commit explicitly after notify_*
            # calls; CLI commands must too. Commit per-recipient (not
            # end-of-run) so a mid-run crash still leaves a clear audit trail
            # of which sends already went out.
            try:
                db.session.commit()
            except Exception:
                logger.exception(
                    f"Failed to commit NotificationLog for {email} "
                    f"(dept={target.department_code}); rolling back this row."
                )
                db.session.rollback()

            if ok:
                summary.emails_sent += 1

    logger.info(
        f"Completed: {summary.emails_sent}/{summary.emails_attempted} sent across "
        f"{summary.targets_with_recipients} departments"
    )
    return summary
