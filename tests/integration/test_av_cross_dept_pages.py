"""Integration tests for the cross-dept AV Space pages (Phase 8 Tasks 46 + 47).

Routes:
  GET /<event_code>/av/spaces/            (Task 46 — space list)
  GET /<event_code>/av/spaces/<code>      (Task 47 — space detail)

Fixture strategy: mirrors test_av_request_flow.py — inline AV seed, no shared
conftest beyond `app` and `client`.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    EventCycle,
    Department,
    User,
    UserRole,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    WorkType,
    WorkTypeConfig,
    WorkPortfolio,
    WorkItem,
    WorkLine,
    ApprovalGroup,
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
    ROUTING_STRATEGY_DIRECT,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.models.av import AVRequestDetail, AVLineDetail
from app.models.space import Space, SpaceDepartmentAssignment


# ---------------------------------------------------------------------------
# Base seed
# ---------------------------------------------------------------------------

@pytest.fixture
def av_base(app):
    """Minimum AV org + work type for cross-dept page tests.

    Creates:
    - EventCycle TST2026
    - AV WorkType + WorkTypeConfig + AV_TEAM ApprovalGroup
    - super_admin (SUPER_ADMIN)
    - av_admin (WORKTYPE_ADMIN scoped to AV)
    - dept_member user with AV view access to dept_a
    - dept_a and other_dept departments
    - unrelated_user with no memberships
    """
    cycle = EventCycle(
        code="TST2026", name="Test Event 2026",
        is_active=True, is_default=True, sort_order=1,
    )
    db.session.add(cycle)
    db.session.flush()

    wt = WorkType(code="AV", name="AV Requests", is_active=True)
    db.session.add(wt)
    db.session.flush()

    av_group = ApprovalGroup(
        work_type_id=wt.id, code="AV_TEAM",
        name="AV Team", is_active=True,
    )
    db.session.add(av_group)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="av",
        public_id_prefix="AV", line_detail_type="av",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
        default_approval_group_id=av_group.id,
        uses_dispatch=False, has_admin_final=False,
    )
    db.session.add(wtc)

    super_admin = User(
        id="test:super_admin", email="super_admin@test.local",
        auth_subject="test:super_admin", display_name="Super Admin",
        is_active=True,
    )
    av_admin = User(
        id="test:av_admin", email="av_admin@test.local",
        auth_subject="test:av_admin", display_name="AV Admin",
        is_active=True,
    )
    dept_member = User(
        id="test:dept_member", email="dept_member@test.local",
        auth_subject="test:dept_member", display_name="Dept Member",
        is_active=True,
    )
    unrelated_user = User(
        id="test:unrelated", email="unrelated@test.local",
        auth_subject="test:unrelated", display_name="Unrelated User",
        is_active=True,
    )
    db.session.add_all([super_admin, av_admin, dept_member, unrelated_user])
    db.session.flush()

    db.session.add(UserRole(user_id=super_admin.id, role_code=ROLE_SUPER_ADMIN))
    db.session.add(UserRole(
        user_id=av_admin.id, role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=wt.id,
    ))

    dept_a = Department(code="ATEST", name="AV Test Dept", is_active=True)
    other_dept = Department(code="OTHERTEST", name="Other Test Dept", is_active=True)
    db.session.add_all([dept_a, other_dept])
    db.session.flush()

    membership = DepartmentMembership(
        user_id=dept_member.id,
        department_id=dept_a.id,
        event_cycle_id=cycle.id,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id,
        work_type_id=wt.id,
        can_view=True,
        can_edit=True,
    ))

    db.session.commit()

    return {
        "cycle": cycle,
        "work_type": wt,
        "approval_group": av_group,
        "super_admin": super_admin,
        "av_admin": av_admin,
        "dept_member": dept_member,
        "unrelated_user": unrelated_user,
        "dept_a": dept_a,
        "other_dept": other_dept,
    }


# ---------------------------------------------------------------------------
# Client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def super_admin_client(client, av_base):
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_base["super_admin"].id
    return client


@pytest.fixture
def dept_member_client(client, av_base):
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_base["dept_member"].id
    return client


@pytest.fixture
def unrelated_user_client(client, av_base):
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_base["unrelated_user"].id
    return client


# ---------------------------------------------------------------------------
# Space fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def two_spaces_one_assigned(av_base):
    """Two Spaces for TST2026: one assigned to dept_a, one assigned to other_dept.

    Returns (event_cycle, space_for_dept_a, space_for_other_dept).
    """
    seed = av_base

    space_a = Space(
        event_cycle_id=seed["cycle"].id,
        code="SPACE_A",
        name="Space A",
        location="Hall A",
        is_active=True,
        created_by_user_id=seed["av_admin"].id,
    )
    space_b = Space(
        event_cycle_id=seed["cycle"].id,
        code="SPACE_B",
        name="Space B",
        is_active=True,
        created_by_user_id=seed["av_admin"].id,
    )
    db.session.add_all([space_a, space_b])
    db.session.flush()

    # Assign space_a to dept_a; space_b to other_dept
    db.session.add(SpaceDepartmentAssignment(
        space_id=space_a.id,
        department_id=seed["dept_a"].id,
        assigned_by_user_id=seed["av_admin"].id,
    ))
    db.session.add(SpaceDepartmentAssignment(
        space_id=space_b.id,
        department_id=seed["other_dept"].id,
        assigned_by_user_id=seed["av_admin"].id,
    ))
    db.session.commit()

    return seed["cycle"], space_a, space_b


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSpaceList:
    def test_super_admin_sees_all_spaces(self, super_admin_client, two_spaces_one_assigned):
        event, space_a, space_b = two_spaces_one_assigned
        response = super_admin_client.get(f"/{event.code}/av/spaces/")
        assert response.status_code == 200
        assert space_a.code.encode() in response.data
        assert space_b.code.encode() in response.data

    def test_dept_member_sees_only_assigned_space(self, dept_member_client, two_spaces_one_assigned):
        """dept_member is only in dept_a, so only space_a (assigned to dept_a) is visible."""
        event, space_a, space_b = two_spaces_one_assigned
        response = dept_member_client.get(f"/{event.code}/av/spaces/")
        assert response.status_code == 200
        assert space_a.code.encode() in response.data
        assert space_b.code.encode() not in response.data

    def test_empty_state_for_user_with_no_visible_spaces(self, unrelated_user_client, av_base):
        """User with no memberships sees empty state, not a crash or 403."""
        event = av_base["cycle"]
        # Add a space so there's at least one in the event, but unrelated_user can't see it
        space = Space(
            event_cycle_id=event.id,
            code="SOME_SPACE",
            name="Some Space",
            is_active=True,
            created_by_user_id=av_base["av_admin"].id,
        )
        db.session.add(space)
        db.session.commit()

        response = unrelated_user_client.get(f"/{event.code}/av/spaces/")
        assert response.status_code == 200
        assert b"No spaces" in response.data or b"no spaces" in response.data.lower()


# ---------------------------------------------------------------------------
# Space detail fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def space_assigned_to_my_dept(av_base):
    """A Space with dept_a (the dept_member's dept) as the sole active assignment.

    Returns the Space object; the av_base fixture provides the event cycle and
    user objects.
    """
    seed = av_base
    space = Space(
        event_cycle_id=seed["cycle"].id,
        code="PANELS_4",
        name="Panels 4",
        location="West Hall",
        is_active=True,
        created_by_user_id=seed["av_admin"].id,
    )
    db.session.add(space)
    db.session.flush()

    db.session.add(SpaceDepartmentAssignment(
        space_id=space.id,
        department_id=seed["dept_a"].id,
        assigned_by_user_id=seed["av_admin"].id,
    ))
    db.session.commit()
    return space


@pytest.fixture
def space_with_request(av_base):
    """A Space with one SUBMITTED AV WorkItem filed for it.

    Returns (space, work_item).
    """
    seed = av_base
    space = Space(
        event_cycle_id=seed["cycle"].id,
        code="MAINSTAGE",
        name="Main Stage",
        is_active=True,
        created_by_user_id=seed["av_admin"].id,
    )
    db.session.add(space)
    db.session.flush()

    db.session.add(SpaceDepartmentAssignment(
        space_id=space.id,
        department_id=seed["dept_a"].id,
        assigned_by_user_id=seed["av_admin"].id,
    ))
    db.session.flush()

    portfolio = WorkPortfolio(
        work_type_id=seed["work_type"].id,
        event_cycle_id=seed["cycle"].id,
        department_id=seed["dept_a"].id,
        created_by_user_id=seed["dept_member"].id,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED,
        public_id="TST2026-ATEST-AV-1",
        created_by_user_id=seed["dept_member"].id,
    )
    db.session.add(work_item)
    db.session.flush()

    av_detail = AVRequestDetail(
        work_item_id=work_item.id,
        space_id=space.id,
        priority="MUST_HAVE",
        duration_model="FULL_EVENT",
        dept_sourced_gear_mode="NONE",
        primary_contact_name="Test Member",
        primary_contact_email="dept_member@test.local",
        created_by_user_id=seed["dept_member"].id,
    )
    db.session.add(av_detail)

    line = WorkLine(
        work_item_id=work_item.id,
        line_number=1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(line)
    db.session.flush()

    av_line = AVLineDetail(
        work_line_id=line.id,
        description="Wireless microphone for panel moderator",
        gear_specificity="USAGE_ONLY",
    )
    db.session.add(av_line)
    db.session.commit()

    return space, work_item


# ---------------------------------------------------------------------------
# Task 47: Space detail tests
# ---------------------------------------------------------------------------

class TestSpaceDetail:
    def test_assigned_dept_member_sees_detail_with_cta(
        self, dept_member_client, av_base, space_assigned_to_my_dept,
    ):
        """Dept member sees the space detail page and a 'File AV request' CTA."""
        event = av_base["cycle"]
        space = space_assigned_to_my_dept
        response = dept_member_client.get(
            f"/{event.code}/av/spaces/{space.code}"
        )
        assert response.status_code == 200
        assert space.name.encode() in response.data
        # CTA should be present for the dept member
        assert b"File AV request" in response.data

    def test_unassigned_user_blocked(
        self, unrelated_user_client, av_base, space_assigned_to_my_dept,
    ):
        """User with no relationship to this space gets 403."""
        event = av_base["cycle"]
        space = space_assigned_to_my_dept
        response = unrelated_user_client.get(
            f"/{event.code}/av/spaces/{space.code}"
        )
        assert response.status_code == 403

    def test_contributing_requests_listed(
        self, super_admin_client, av_base, space_with_request,
    ):
        """When AV requests exist for the space, they appear in the contributing-requests table."""
        space, work_item = space_with_request
        event = av_base["cycle"]
        response = super_admin_client.get(
            f"/{event.code}/av/spaces/{space.code}"
        )
        assert response.status_code == 200
        assert work_item.public_id.encode() in response.data
