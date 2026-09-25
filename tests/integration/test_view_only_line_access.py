"""A view-only department member can read their department's lines.

`DepartmentMembershipWorkTypeAccess.can_edit` defaults to False, so view-only
is the default shape of a membership grant, not an exotic one. Reading a line
must not require edit; acting on one still must.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    ApprovalGroup,
    BudgetLineDetail,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    User,
    UserRole,
    WorkItem,
    WorkLine,
    WorkLineComment,
    WorkLineReview,
    REQUEST_KIND_PRIMARY,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    ROLE_APPROVER,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.models.constants import (
    COMMENT_VISIBILITY_ADMIN,
    COMMENT_VISIBILITY_PUBLIC,
)

REVIEW_URL = "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/line/1/review"

ADMIN_COMMENT = "Reviewers only: check the vendor quote before approving."
PUBLIC_COMMENT = "Please confirm the quantity on this line."
NOTICE_MARKER = "View-only access"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _member(data, user_id, display, *, can_edit):
    """A department member with work-type access at the given edit level."""
    user = User(id=user_id, email=f"{user_id.split(':')[1]}@test.local",
                display_name=display, is_active=True)
    db.session.add(user)
    db.session.flush()

    membership = DepartmentMembership(
        user_id=user.id, department_id=data["department"].id,
        event_cycle_id=data["cycle"].id,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id,
        work_type_id=data["work_type"].id,
        can_view=True, can_edit=can_edit,
    ))
    return user


@pytest.fixture
def submitted_line(seed_workflow_data):
    """A SUBMITTED budget line routed to TECH, plus the four users who matter.

    The work item is created by the admin, not by any of the members below.
    can_respond_to_work_item returns True for a creator, so a viewer who also
    created the item would pass the guard today and prove nothing.
    """
    data = seed_workflow_data
    ag = data["approval_group"]

    viewer = _member(data, "test:viewonly", "Test Viewer", can_edit=False)
    editor = _member(data, "test:editor", "Test Editor", can_edit=True)

    # An approver on a group this line is NOT routed to, with no department
    # membership. The approval-group narrowing must still refuse them.
    other_group = ApprovalGroup(
        work_type_id=data["work_type"].id,
        code="HOTEL", name="Hotel Team", is_active=True,
    )
    db.session.add(other_group)
    db.session.flush()
    outsider = User(id="test:outsider", email="outsider@test.local",
                    display_name="Test Outsider", is_active=True)
    db.session.add(outsider)
    db.session.flush()
    db.session.add(UserRole(user_id=outsider.id, role_code=ROLE_APPROVER,
                            approval_group_id=other_group.id))

    item = WorkItem(
        portfolio_id=data["portfolio"].id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(item)
    db.session.flush()

    line = WorkLine(work_item_id=item.id, line_number=1,
                    status=WORK_LINE_STATUS_PENDING,
                    current_review_stage=REVIEW_STAGE_APPROVAL_GROUP)
    db.session.add(line)
    db.session.flush()

    # is_reviewer_for_line resolves routing off this snapshot, not off the
    # WorkLineReview row.
    db.session.add(BudgetLineDetail(
        work_line_id=line.id,
        expense_account_id=data["expense_account"].id,
        spend_type_id=data["spend_type"].id,
        routed_approval_group_id=ag.id,
        unit_price_cents=1000, quantity=1,
    ))
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=ag.id, status=REVIEW_STATUS_PENDING,
        created_by_user_id=data["admin"].id,
    ))
    db.session.add(WorkLineComment(
        work_line_id=line.id, body=ADMIN_COMMENT,
        visibility=COMMENT_VISIBILITY_ADMIN,
        created_by_user_id=data["admin"].id,
    ))
    db.session.add(WorkLineComment(
        work_line_id=line.id, body=PUBLIC_COMMENT,
        visibility=COMMENT_VISIBILITY_PUBLIC,
        created_by_user_id=data["admin"].id,
    ))
    db.session.commit()

    return {**data, "work_item": item, "line": line,
            "viewer": viewer, "editor": editor, "outsider": outsider,
            "other_group": other_group}


def test_view_only_member_can_open_the_line_review_page(client, submitted_line):
    """The regression. Before the read predicate this was a 403."""
    _login(client, "test:viewonly")

    resp = client.get(REVIEW_URL)

    assert resp.status_code == 200


def test_view_only_member_gets_no_review_or_respond_actions(client, submitted_line):
    """Reading widened; acting did not. Asserting on the action URLs rather
    than on button text, because the text varies by work type."""
    _login(client, "test:viewonly")

    resp = client.get(REVIEW_URL)

    # Assert the page rendered before asserting what is missing from it. A
    # 403 error page also contains none of these URLs, so without this the
    # test passes while the bug is still present.
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/line/1/approve" not in body
    assert "/line/1/reject" not in body
    assert "/line/1/respond" not in body


def test_an_approver_from_another_group_is_still_refused(client, submitted_line):
    """The approval-group narrowing is the reason this guard exists. Granting
    read on `perms.can_view` would have widened it, because that flag is True
    for an approver on ANY line of the item."""
    _login(client, "test:outsider")

    resp = client.get(REVIEW_URL)

    assert resp.status_code == 403


def test_view_only_member_does_not_see_admin_only_comments(client, submitted_line):
    _login(client, "test:viewonly")

    body = client.get(REVIEW_URL).get_data(as_text=True)

    assert ADMIN_COMMENT not in body
    assert PUBLIC_COMMENT in body


def test_view_only_member_sees_the_view_only_notice(client, submitted_line):
    """A page with no action buttons and no explanation reads as broken."""
    _login(client, "test:viewonly")

    body = client.get(REVIEW_URL).get_data(as_text=True)

    assert NOTICE_MARKER in body


def test_an_editing_member_does_not_see_the_view_only_notice(client, submitted_line):
    """The notice keys off the grant, not off whether buttons happen to show.
    This line is not awaiting a response, so an editor sees no respond form
    either; telling them they are view-only would be false."""
    _login(client, "test:editor")

    body = client.get(REVIEW_URL).get_data(as_text=True)

    assert NOTICE_MARKER not in body
