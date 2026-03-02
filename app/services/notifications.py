"""
High-level notification functions for budget workflow events.
"""
from __future__ import annotations

from flask import render_template, current_app
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


def get_base_url() -> str:
    """Get base URL for email links."""
    return current_app.config.get('BASE_URL', 'https://budget.magfest.org')


def notify_budget_submitted(work_item: WorkItem):
    """
    Notify budget admins that a new budget was submitted and is awaiting dispatch.

    Called after: work_item.status set to AWAITING_DISPATCH
    """
    recipients = _get_budget_admin_emails()

    subject = f'[MAGFest Budget] New Submission - {work_item.public_id}'
    body = render_template(
        'email/submitted.txt',
        work_item=work_item,
        base_url=get_base_url(),
    )

    for email in recipients:
        send_email(
            to=email,
            subject=subject,
            body_text=body,
            template_key='submitted',
            work_item_id=work_item.id,
        )


def notify_budget_dispatched(work_item: WorkItem, approval_group_ids: List[int]):
    """
    Notify approval group members that a budget is ready for their review.

    Called after: work_item dispatched to approval groups
    """
    recipients = _get_approval_group_emails(approval_group_ids)

    subject = f'[MAGFest Budget] Ready for Review - {work_item.public_id}'
    body = render_template(
        'email/dispatched.txt',
        work_item=work_item,
        base_url=get_base_url(),
    )

    for email in recipients:
        send_email(
            to=email,
            subject=subject,
            body_text=body,
            template_key='dispatched',
            work_item_id=work_item.id,
        )


def notify_needs_attention(work_item: WorkItem):
    """
    Notify department members that their budget request needs attention.

    Called after: reviewer marks a line as NEEDS_INFO or NEEDS_ADJUSTMENT
    """
    recipients = _get_department_member_emails(
        department_id=work_item.portfolio.department_id,
        event_cycle_id=work_item.portfolio.event_cycle_id,
    )

    subject = f'[MAGFest Budget] Action Required - {work_item.public_id}'
    body = render_template(
        'email/needs_attention.txt',
        work_item=work_item,
        base_url=get_base_url(),
    )

    for email in recipients:
        send_email(
            to=email,
            subject=subject,
            body_text=body,
            template_key='needs_attention',
            work_item_id=work_item.id,
        )


def notify_response_received(work_item: WorkItem, reviewer_user_id: str):
    """
    Notify the reviewer that the requester has responded to their feedback.

    Called after: requester responds to NEEDS_INFO or NEEDS_ADJUSTMENT
    """
    user = db.session.query(User).filter_by(id=reviewer_user_id).first()
    if not user or not user.email:
        return

    subject = f'[MAGFest Budget] Response Received - {work_item.public_id}'
    body = render_template(
        'email/response_received.txt',
        work_item=work_item,
        base_url=get_base_url(),
    )

    send_email(
        to=user.email,
        subject=subject,
        body_text=body,
        template_key='response_received',
        work_item_id=work_item.id,
        recipient_user_id=user.id,
    )


def notify_budget_finalized(work_item: WorkItem):
    """
    Notify department members that their budget has been finalized.

    Called after: admin finalizes the work item
    """
    recipients = _get_department_member_emails(
        department_id=work_item.portfolio.department_id,
        event_cycle_id=work_item.portfolio.event_cycle_id,
    )

    subject = f'[MAGFest Budget] Finalized - {work_item.public_id}'
    body = render_template(
        'email/finalized.txt',
        work_item=work_item,
        base_url=get_base_url(),
    )

    for email in recipients:
        send_email(
            to=email,
            subject=subject,
            body_text=body,
            template_key='finalized',
            work_item_id=work_item.id,
        )


# ============================================================
# Recipient Helpers
# ============================================================

def _get_budget_admin_emails() -> List[str]:
    """
    Get emails of users who should receive budget submission notifications.

    Includes: SUPER_ADMIN and WORKTYPE_ADMIN (for budget work type)
    """
    emails: Set[str] = set()

    # Get the budget work type ID
    from app.models import WorkType
    budget_wt = db.session.query(WorkType).filter_by(code="BUDGET").first()

    # Find all admin users
    admin_roles = db.session.query(UserRole).filter(
        UserRole.role_code.in_([ROLE_SUPER_ADMIN, ROLE_WORKTYPE_ADMIN])
    ).all()

    for role in admin_roles:
        # SUPER_ADMIN always gets notifications
        # WORKTYPE_ADMIN only if it's for budget work type or unscoped
        # if role.role_code == ROLE_SUPER_ADMIN:
        #     user = db.session.query(User).filter_by(id=role.user_id).first()
        #     if user and user.is_active and user.email:
        #         emails.add(user.email)
        # el
        if role.role_code == ROLE_WORKTYPE_ADMIN:
            # Only include if this is the budget work type admin
            if budget_wt and (role.work_type_id == budget_wt.id or role.work_type_id is None):
                user = db.session.query(User).filter_by(id=role.user_id).first()
                if user and user.is_active and user.email:
                    emails.add(user.email)

    return list(emails)


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

    for role in approver_roles:
        user = db.session.query(User).filter_by(id=role.user_id).first()
        if user and user.is_active and user.email:
            emails.add(user.email)

    return list(emails)


def _get_department_member_emails(department_id: int, event_cycle_id: int) -> List[str]:
    """
    Get emails of department members (direct or via division membership).
    """
    emails: Set[str] = set()

    # Direct department memberships
    dept_memberships = db.session.query(DepartmentMembership).filter_by(
        department_id=department_id,
        event_cycle_id=event_cycle_id,
    ).all()

    for m in dept_memberships:
        user = db.session.query(User).filter_by(id=m.user_id).first()
        if user and user.is_active and user.email:
            emails.add(user.email)

    # Division memberships (for departments within that division)
    from app.models import Department
    dept = db.session.query(Department).get(department_id)
    if dept and dept.division_id:
        div_memberships = db.session.query(DivisionMembership).filter_by(
            division_id=dept.division_id,
            event_cycle_id=event_cycle_id,
        ).all()

        for m in div_memberships:
            user = db.session.query(User).filter_by(id=m.user_id).first()
            if user and user.is_active and user.email:
                emails.add(user.email)

    return list(emails)
