"""Non-admin landing pages must use the STATUS_LABELS map, not raw title-casing.

home.html, department_home.html, and division_home.html each carried their own
if/elif chain over effective_status with a generic
`{{ status | replace('_', ' ') | title }}` fallthrough. PENDING_BOARD_APPROVAL
fell into that branch and rendered "Pending Board Approval" instead of the
"Pending FY Budget Approval" label the item detail page shows via
friendly_status(). These tests log in as a department member, not
test:admin, since these are department-facing pages.
"""
from app import db
from app.models import (
    User,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    DivisionMembership,
    DivisionMembershipWorkTypeAccess,
    WORK_ITEM_STATUS_FINALIZED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _hold_for_board_approval(data):
    """Put the fixture item in the PENDING_BOARD_APPROVAL state."""
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = None
    db.session.commit()
    return item


def _add_department_member(data):
    member = User(id="test:deptmember", email="member@test.local",
                  display_name="Dept Member", is_active=True)
    db.session.add(member)
    db.session.flush()
    membership = DepartmentMembership(
        user_id=member.id, department_id=data["department"].id,
        event_cycle_id=data["cycle"].id,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id,
        work_type_id=data["work_type"].id, can_view=True, can_edit=False,
    ))
    db.session.commit()
    return member


def _add_division_member(data):
    data["department"].division_id = data["division"].id
    member = User(id="test:divmember", email="divmember@test.local",
                  display_name="Div Member", is_active=True)
    db.session.add(member)
    db.session.flush()
    membership = DivisionMembership(
        user_id=member.id, division_id=data["division"].id,
        event_cycle_id=data["cycle"].id,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(DivisionMembershipWorkTypeAccess(
        division_membership_id=membership.id,
        work_type_id=data["work_type"].id, can_view=True, can_edit=False,
    ))
    db.session.commit()
    return member


def test_home_page_shows_pending_fy_budget_approval(client, seed_draft_work_item):
    data = seed_draft_work_item
    _hold_for_board_approval(data)
    _add_department_member(data)

    _login(client, "test:deptmember")
    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Pending FY Budget Approval" in body
    assert "Pending Board Approval" not in body


def test_department_home_shows_pending_fy_budget_approval(client, seed_draft_work_item):
    data = seed_draft_work_item
    _hold_for_board_approval(data)
    _add_department_member(data)

    _login(client, "test:deptmember")
    resp = client.get(f"/{data['cycle'].code}/{data['department'].code}/")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Pending FY Budget Approval" in body
    assert "Pending Board Approval" not in body


def test_division_home_shows_pending_fy_budget_approval(client, seed_draft_work_item):
    data = seed_draft_work_item
    _hold_for_board_approval(data)
    _add_division_member(data)

    _login(client, "test:divmember")
    resp = client.get(f"/{data['cycle'].code}/division/{data['division'].code}/")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Pending FY Budget Approval" in body
    assert "Pending Board Approval" not in body
