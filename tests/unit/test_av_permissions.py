"""Tests for AV permission helpers."""
import pytest

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    EventCycle,
    User,
    UserRole,
    WorkType,
    WorkTypeConfig,
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
    ROUTING_STRATEGY_DIRECT,
)
from app.models.space import Space, SpaceDepartmentAssignment
from app.models.av import AVScope
from app.routes import UserContext
from app.routes.work.av.permissions import (
    can_view_av_space,
    can_create_av_request_for,
    can_edit_av_request,
    can_view_av_request,
    can_ack_av_scope,
    require_av_admin,
    require_view_av_space,
)


# ---------------------------------------------------------------------------
# Local seed fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def av_perm_seed(app):
    """Seed the minimal org + AV work type needed for permission tests.

    Creates:
    - EventCycle TST2026
    - Two Departments: DEPT_A (primary) and DEPT_B (secondary, "other")
    - AV WorkType + WorkTypeConfig
    - AV_TEAM ApprovalGroup
    - One Space (not assigned to any dept by default)
    - Four users:
        * super_admin_user  — UserRole(SUPER_ADMIN)
        * av_admin_user     — UserRole(WORKTYPE_ADMIN for AV)
        * dept_member_user  — DepartmentMembership for DEPT_A with AV can_view+can_edit
        * other_dept_user   — DepartmentMembership for DEPT_B with AV can_view+can_edit
    """
    cycle = EventCycle(
        code="TST2026", name="Test Event 2026",
        is_active=True, is_default=True, sort_order=1,
    )
    dept_a = Department(code="DEPT_A", name="Department A", is_active=True)
    dept_b = Department(code="DEPT_B", name="Department B", is_active=True)
    db.session.add_all([cycle, dept_a, dept_b])
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
        uses_dispatch=True, has_admin_final=True,
    )
    db.session.add(wtc)

    # Users
    super_admin_user = User(
        id="test:sa", email="sa@test.local",
        display_name="Super Admin", is_active=True,
    )
    av_admin_user = User(
        id="test:av_admin", email="av_admin@test.local",
        display_name="AV Admin", is_active=True,
    )
    dept_member_user = User(
        id="test:dept_a", email="dept_a@test.local",
        display_name="Dept A Member", is_active=True,
    )
    other_dept_user = User(
        id="test:dept_b", email="dept_b@test.local",
        display_name="Dept B Member", is_active=True,
    )
    db.session.add_all([super_admin_user, av_admin_user, dept_member_user, other_dept_user])
    db.session.flush()

    # Roles
    db.session.add(UserRole(user_id=super_admin_user.id, role_code=ROLE_SUPER_ADMIN))
    db.session.add(UserRole(
        user_id=av_admin_user.id,
        role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=wt.id,
    ))

    # Dept memberships with AV access
    m_a = DepartmentMembership(
        user_id=dept_member_user.id,
        department_id=dept_a.id,
        event_cycle_id=cycle.id,
    )
    db.session.add(m_a)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=m_a.id,
        work_type_id=wt.id,
        can_view=True,
        can_edit=True,
    ))

    m_b = DepartmentMembership(
        user_id=other_dept_user.id,
        department_id=dept_b.id,
        event_cycle_id=cycle.id,
    )
    db.session.add(m_b)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=m_b.id,
        work_type_id=wt.id,
        can_view=True,
        can_edit=True,
    ))

    # Space (no dept assignment yet)
    space = Space(
        event_cycle_id=cycle.id,
        code="ROOM_1",
        name="Room 1",
        location="Building A",
        is_active=True,
        created_by_user_id=super_admin_user.id,
    )
    db.session.add(space)
    db.session.commit()

    return {
        "cycle": cycle,
        "dept_a": dept_a,
        "dept_b": dept_b,
        "work_type": wt,
        "av_group": av_group,
        "super_admin_user": super_admin_user,
        "av_admin_user": av_admin_user,
        "dept_member_user": dept_member_user,
        "other_dept_user": other_dept_user,
        "space": space,
    }


# ---------------------------------------------------------------------------
# Per-test UserContext fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def super_admin_user_ctx(av_perm_seed):
    u = av_perm_seed["super_admin_user"]
    return UserContext(
        user_id=u.id,
        user=u,
        roles=(ROLE_SUPER_ADMIN,),
        is_super_admin=True,
        approval_group_ids=set(),
    )


@pytest.fixture(scope="function")
def av_admin_user_ctx(av_perm_seed):
    u = av_perm_seed["av_admin_user"]
    return UserContext(
        user_id=u.id,
        user=u,
        roles=(ROLE_WORKTYPE_ADMIN,),
        is_super_admin=False,
        approval_group_ids=set(),
    )


@pytest.fixture(scope="function")
def dept_member_user_ctx(av_perm_seed):
    u = av_perm_seed["dept_member_user"]
    return UserContext(
        user_id=u.id,
        user=u,
        roles=(),
        is_super_admin=False,
        approval_group_ids=set(),
    )


@pytest.fixture(scope="function")
def other_dept_member_user_ctx(av_perm_seed):
    u = av_perm_seed["other_dept_user"]
    return UserContext(
        user_id=u.id,
        user=u,
        roles=(),
        is_super_admin=False,
        approval_group_ids=set(),
    )


@pytest.fixture(scope="function")
def space(av_perm_seed):
    return av_perm_seed["space"]


@pytest.fixture(scope="function")
def department(av_perm_seed):
    return av_perm_seed["dept_a"]


@pytest.fixture(scope="function")
def space_with_dept(av_perm_seed):
    """Returns (space, assignment) with DEPT_A assigned to the space."""
    sp = av_perm_seed["space"]
    dept = av_perm_seed["dept_a"]
    assignment = SpaceDepartmentAssignment(
        space_id=sp.id,
        department_id=dept.id,
        assigned_by_user_id=av_perm_seed["super_admin_user"].id,
    )
    db.session.add(assignment)
    db.session.commit()
    return sp, assignment


# ---------------------------------------------------------------------------
# Tests: can_view_av_space
# ---------------------------------------------------------------------------

def test_can_view_av_space_super_admin(super_admin_user_ctx, space):
    assert can_view_av_space(super_admin_user_ctx, space) is True


def test_can_view_av_space_av_admin(av_admin_user_ctx, space):
    assert can_view_av_space(av_admin_user_ctx, space) is True


def test_can_view_av_space_assigned_dept_member(dept_member_user_ctx, space_with_dept):
    space, _assignment = space_with_dept
    assert can_view_av_space(dept_member_user_ctx, space) is True


def test_cannot_view_av_space_unassigned_dept_member(other_dept_member_user_ctx, space):
    # other_dept_user's dept (DEPT_B) is NOT assigned to this space
    assert can_view_av_space(other_dept_member_user_ctx, space) is False


# ---------------------------------------------------------------------------
# Tests: can_create_av_request_for
# ---------------------------------------------------------------------------

def test_can_create_av_request_for_assigned_dept(dept_member_user_ctx, space_with_dept, department):
    space, _ = space_with_dept
    assert can_create_av_request_for(dept_member_user_ctx, space, department) is True


def test_cannot_create_av_request_for_unassigned_dept(dept_member_user_ctx, space, department):
    # space has no assignment to any dept
    assert can_create_av_request_for(dept_member_user_ctx, space, department) is False


def test_super_admin_bypasses_can_create(super_admin_user_ctx, space_with_dept, department):
    """Super admins can create AV requests even without dept membership."""
    space, _ = space_with_dept
    assert can_create_av_request_for(super_admin_user_ctx, space, department) is True


def test_av_admin_does_NOT_bypass_can_create(av_admin_user_ctx, space_with_dept, department):
    """AV admins (WORKTYPE_ADMIN(AV)) do NOT bypass create — they manage spaces, not requests."""
    space, _ = space_with_dept
    # av_admin_user_ctx has no DepartmentMembership for `department`
    assert can_create_av_request_for(av_admin_user_ctx, space, department) is False


# ---------------------------------------------------------------------------
# Fixture: av_request_in_assigned_space
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def av_request_in_assigned_space(av_perm_seed):
    """A WorkItem filed for DEPT_A in Room 1, with DEPT_A assigned to that space.

    The request was created by dept_member_user so that super_admin and av_admin
    have no implicit ownership.  Returns the WorkItem.
    """
    from app.models import (
        WorkPortfolio,
        WorkItem,
        WorkLine,
        REQUEST_KIND_PRIMARY,
        WORK_ITEM_STATUS_DRAFT,
        WORK_LINE_STATUS_PENDING,
    )
    from app.models.av import AVRequestDetail

    seed = av_perm_seed
    sp = seed["space"]
    dept = seed["dept_a"]
    cycle = seed["cycle"]
    wt = seed["work_type"]
    filer = seed["dept_member_user"]
    admin = seed["super_admin_user"]

    # Assign DEPT_A to the space
    assignment = SpaceDepartmentAssignment(
        space_id=sp.id,
        department_id=dept.id,
        assigned_by_user_id=admin.id,
    )
    db.session.add(assignment)
    db.session.flush()

    portfolio = WorkPortfolio(
        work_type_id=wt.id,
        event_cycle_id=cycle.id,
        department_id=dept.id,
        created_by_user_id=filer.id,
        next_public_id_seq=1,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-DEPT_A-AV-1",
        created_by_user_id=filer.id,
    )
    db.session.add(work_item)
    db.session.flush()

    av_detail = AVRequestDetail(
        work_item_id=work_item.id,
        space_id=sp.id,
        priority="MUST_HAVE",
        duration_model="FULL_EVENT",
        dept_sourced_gear_mode="NONE",
        primary_contact_name="Dept A Member",
        primary_contact_email="dept_a@test.local",
        created_by_user_id=filer.id,
    )
    db.session.add(av_detail)

    work_line = WorkLine(
        work_item_id=work_item.id,
        line_number=1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(work_line)
    db.session.commit()

    return work_item


# ---------------------------------------------------------------------------
# Tests: can_edit_av_request
# ---------------------------------------------------------------------------

def test_super_admin_bypasses_can_edit(super_admin_user_ctx, av_request_in_assigned_space):
    """Super admins can edit AV requests even without dept membership."""
    assert can_edit_av_request(super_admin_user_ctx, av_request_in_assigned_space) is True


def test_av_admin_does_NOT_bypass_can_edit(av_admin_user_ctx, av_request_in_assigned_space):
    """AV admins do NOT bypass edit — they manage spaces, not requests."""
    assert can_edit_av_request(av_admin_user_ctx, av_request_in_assigned_space) is False


# ---------------------------------------------------------------------------
# Tests: can_ack_av_scope
# ---------------------------------------------------------------------------

def test_can_ack_only_when_open_for_input(dept_member_user_ctx, space_with_dept, department):
    space, _ = space_with_dept

    draft_scope = AVScope(
        space_id=space.id, version=1, state="DRAFT",
        scope_text="x", authored_by_user_id="u",
    )
    open_scope = AVScope(
        space_id=space.id, version=2, state="OPEN_FOR_INPUT",
        scope_text="x", authored_by_user_id="u",
    )
    locked_scope = AVScope(
        space_id=space.id, version=3, state="LOCKED",
        scope_text="x", authored_by_user_id="u",
    )
    db.session.add_all([draft_scope, open_scope, locked_scope])
    db.session.commit()

    assert can_ack_av_scope(dept_member_user_ctx, draft_scope, department) is False
    assert can_ack_av_scope(dept_member_user_ctx, open_scope, department) is True
    assert can_ack_av_scope(dept_member_user_ctx, locked_scope, department) is False


# ---------------------------------------------------------------------------
# Tests: require_* gates
# ---------------------------------------------------------------------------

def test_require_av_admin_allows_av_admin(av_admin_user_ctx):
    require_av_admin(av_admin_user_ctx)  # should not raise


def test_require_av_admin_denies_dept_member(dept_member_user_ctx):
    import werkzeug.exceptions
    with pytest.raises(werkzeug.exceptions.Forbidden):
        require_av_admin(dept_member_user_ctx)


def test_require_view_av_space_allows_av_admin(av_admin_user_ctx, space):
    require_view_av_space(av_admin_user_ctx, space)  # should not raise


def test_require_view_av_space_denies_unassigned_dept_member(other_dept_member_user_ctx, space):
    import werkzeug.exceptions
    with pytest.raises(werkzeug.exceptions.Forbidden):
        require_view_av_space(other_dept_member_user_ctx, space)
