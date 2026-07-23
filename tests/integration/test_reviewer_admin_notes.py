"""Feature 1: reviewers can see and add ADMIN-visibility comments."""
from datetime import datetime

from app import db
from app.models import (
    User, UserRole, WorkItem, WorkLine, WorkLineReview, WorkLineComment,
    BudgetLineDetail, DepartmentMembership, DepartmentMembershipWorkTypeAccess,
    ROLE_APPROVER, REQUEST_KIND_PRIMARY, REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING, WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_PENDING,
)
from app.models.constants import COMMENT_VISIBILITY_ADMIN, COMMENT_VISIBILITY_PUBLIC


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_review_for_reviewer(data):
    """Submitted work item + line + AG review routed to the reviewer's group."""
    reviewer = data["reviewer"]
    ag = data["approval_group"]
    # Grant reviewer APPROVER on the group
    db.session.add(UserRole(user_id=reviewer.id, role_code=ROLE_APPROVER,
                            approval_group_id=ag.id))
    item = WorkItem(
        portfolio_id=data["portfolio"].id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(item); db.session.flush()
    line = WorkLine(work_item_id=item.id, line_number=1,
                    status=WORK_LINE_STATUS_PENDING,
                    current_review_stage=REVIEW_STAGE_APPROVAL_GROUP)
    db.session.add(line); db.session.flush()
    # is_reviewer_for_line() resolves routing off the line's BudgetLineDetail
    # snapshot (routed_approval_group_id), not the WorkLineReview row, so the
    # line needs a detail row routed to the reviewer's group.
    detail = BudgetLineDetail(
        work_line_id=line.id,
        expense_account_id=data["expense_account"].id,
        spend_type_id=data["spend_type"].id,
        routed_approval_group_id=ag.id,
        unit_price_cents=1000,
        quantity=1,
    )
    db.session.add(detail); db.session.flush()
    review = WorkLineReview(work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
                            approval_group_id=ag.id, status=REVIEW_STATUS_PENDING,
                            created_by_user_id=data["admin"].id)
    db.session.add(review); db.session.commit()
    return item, line


def _seed_review_for_requester(data):
    """
    Submitted work item + line + AG review, but created by a plain
    requester (no SUPER_ADMIN, no APPROVER/approval-group membership) who
    is granted only department view access — the non-reviewer trust
    boundary this feature must respect.

    is_reviewer_for_line() is False for this user because they hold no
    UserRole at all; they can load the line-review page only via
    portfolio can_view (DepartmentMembership work-type access) or via
    being the work item's creator (can_respond_to_work_item).
    """
    ag = data["approval_group"]
    dept = data["department"]
    cycle = data["cycle"]
    wt = data["work_type"]

    requester = User(
        id="test:requester", email="requester@test.local",
        display_name="Test Requester", is_active=True,
    )
    db.session.add(requester); db.session.flush()

    membership = DepartmentMembership(
        user_id=requester.id, department_id=dept.id, event_cycle_id=cycle.id,
    )
    db.session.add(membership); db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id, work_type_id=wt.id,
        can_view=True, can_edit=True,
    ))

    item = WorkItem(
        portfolio_id=data["portfolio"].id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
        created_by_user_id=requester.id,
    )
    db.session.add(item); db.session.flush()
    line = WorkLine(work_item_id=item.id, line_number=1,
                    status=WORK_LINE_STATUS_PENDING,
                    current_review_stage=REVIEW_STAGE_APPROVAL_GROUP)
    db.session.add(line); db.session.flush()
    detail = BudgetLineDetail(
        work_line_id=line.id,
        expense_account_id=data["expense_account"].id,
        spend_type_id=data["spend_type"].id,
        routed_approval_group_id=ag.id,
        unit_price_cents=1000,
        quantity=1,
    )
    db.session.add(detail); db.session.flush()
    review = WorkLineReview(work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
                            approval_group_id=ag.id, status=REVIEW_STATUS_PENDING,
                            created_by_user_id=data["admin"].id)
    db.session.add(review); db.session.commit()
    return requester, item, line


def test_reviewer_can_post_admin_only_comment(client, seed_workflow_data):
    item, line = _seed_review_for_reviewer(seed_workflow_data)
    _login(client, "test:reviewer")
    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/comment",
        data={"comment": "internal concern", "admin_only": "1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    c = WorkLineComment.query.filter_by(work_line_id=line.id).one()
    assert c.visibility == COMMENT_VISIBILITY_ADMIN


def test_reviewer_can_see_admin_comment(client, seed_workflow_data):
    item, line = _seed_review_for_reviewer(seed_workflow_data)
    db.session.add(WorkLineComment(
        work_line_id=line.id, visibility=COMMENT_VISIBILITY_ADMIN,
        body="secret", created_by_user_id="test:admin"))
    db.session.commit()
    _login(client, "test:reviewer")
    resp = client.get(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/review")
    assert resp.status_code == 200
    assert b"secret" in resp.data           # reviewer sees it


def test_requester_cannot_see_admin_comment(client, seed_workflow_data):
    """The actual guarantee: a non-reviewer requester must NOT see ADMIN
    comments, even when they can otherwise legitimately load the page."""
    requester, item, line = _seed_review_for_requester(seed_workflow_data)
    db.session.add(WorkLineComment(
        work_line_id=line.id, visibility=COMMENT_VISIBILITY_ADMIN,
        body="secret", created_by_user_id="test:admin"))
    db.session.commit()
    _login(client, requester.id)
    resp = client.get(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/review")
    assert resp.status_code == 200          # requester can load the page...
    assert b"secret" not in resp.data       # ...but must not see the ADMIN note


def test_decision_note_is_public_even_if_admin_only_posted(client, seed_workflow_data):
    """Task 13: a reviewer decision note must always be PUBLIC, even if the
    (now-removed) admin_only field is forged/stale on the submitted form.
    Non-public notes only exist via the standalone comment form."""
    requester, item, line = _seed_review_for_requester(seed_workflow_data)

    # test:admin is SUPER_ADMIN, which is_reviewer_for_line() treats as a
    # valid reviewer for every line regardless of approval-group routing.
    _login(client, "test:admin")
    checkout_resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{item.public_id}/checkout")
    assert checkout_resp.status_code in (200, 302)

    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/approve",
        data={
            "note": "approved because the vendor quote was confirmed",
            "admin_only": "1",  # simulates a stale/forged form value
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    comment = (
        WorkLineComment.query.filter_by(work_line_id=line.id)
        .order_by(WorkLineComment.id.desc())
        .first()
    )
    assert comment is not None
    assert "approved because the vendor quote was confirmed" in comment.body
    assert comment.visibility == COMMENT_VISIBILITY_PUBLIC  # forced public despite admin_only=1

    # The requester (who created the item, no reviewer/admin role) must see
    # the decision note in the decision trail.
    _login(client, requester.id)
    view_resp = client.get(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/review")
    assert view_resp.status_code == 200
    assert b"approved because the vendor quote was confirmed" in view_resp.data
