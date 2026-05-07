"""Integration tests for AV admin Space CRUD pages.

Pattern: mirrors tests/integration/test_work_types_admin.py.
All DB setup is done inline within per-file fixtures; no shared conftest
fixtures are assumed beyond `app` and `client` (from tests/conftest.py).
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    EventCycle,
    User,
    UserRole,
    WorkType,
    WorkTypeConfig,
    ApprovalGroup,
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
    ROUTING_STRATEGY_DIRECT,
)
from app.models import Department
from app.models.space import Space, SpaceDepartmentAssignment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def av_seed(app):
    """Seed the minimum org + AV work type required for all AV admin tests.

    Creates:
    - EventCycle TST2026 (default)
    - AV WorkType + WorkTypeConfig
    - AV_TEAM ApprovalGroup
    - super_admin user (SUPER_ADMIN role)
    - av_admin user (WORKTYPE_ADMIN scoped to AV)
    - regular user (no admin roles)
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
        uses_dispatch=True, has_admin_final=True,
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
    regular = User(
        id="test:regular", email="regular@test.local",
        auth_subject="test:regular", display_name="Regular User",
        is_active=True,
    )
    db.session.add_all([super_admin, av_admin, regular])
    db.session.flush()

    db.session.add(UserRole(user_id=super_admin.id, role_code=ROLE_SUPER_ADMIN))
    db.session.add(UserRole(
        user_id=av_admin.id, role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=wt.id,
    ))
    db.session.commit()

    return {
        "cycle": cycle,
        "work_type": wt,
        "approval_group": av_group,
        "super_admin": super_admin,
        "av_admin": av_admin,
        "regular": regular,
    }


@pytest.fixture
def av_admin_client(client, av_seed):
    """Test client logged in as the AV admin."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_seed["av_admin"].id
    return client


@pytest.fixture
def super_admin_client(client, av_seed):
    """Test client logged in as super admin."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_seed["super_admin"].id
    return client


@pytest.fixture
def regular_client(client, av_seed):
    """Test client logged in as a regular (non-admin) user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_seed["regular"].id
    return client


@pytest.fixture
def existing_space(av_seed):
    """Create a pre-existing Space for edit/archive tests."""
    space = Space(
        event_cycle_id=av_seed["cycle"].id,
        code="EXISTING_SPACE",
        name="Existing Space",
        location="Hall A",
        square_feet=2000,
        is_active=True,
        created_by_user_id=av_seed["av_admin"].id,
    )
    db.session.add(space)
    db.session.commit()
    return space


# ---------------------------------------------------------------------------
# Tests: list page
# ---------------------------------------------------------------------------

class TestListSpaces:
    def test_av_admin_can_view_list(self, av_admin_client, av_seed):
        resp = av_admin_client.get("/admin/av/spaces/")
        assert resp.status_code == 200

    def test_super_admin_can_view_list(self, super_admin_client, av_seed):
        resp = super_admin_client.get("/admin/av/spaces/")
        assert resp.status_code == 200

    def test_regular_user_gets_403(self, regular_client, av_seed):
        resp = regular_client.get("/admin/av/spaces/")
        assert resp.status_code == 403

    def test_unauthenticated_redirected(self, client, av_seed):
        resp = client.get("/admin/av/spaces/")
        assert resp.status_code in (302, 401, 403)

    def test_list_shows_existing_space(self, av_admin_client, existing_space):
        resp = av_admin_client.get("/admin/av/spaces/")
        assert resp.status_code == 200
        assert b"EXISTING_SPACE" in resp.data

    def test_list_shows_new_space_button(self, av_admin_client, av_seed):
        resp = av_admin_client.get("/admin/av/spaces/")
        assert b"New Space" in resp.data


# ---------------------------------------------------------------------------
# Tests: create
# ---------------------------------------------------------------------------

class TestCreateSpace:
    def test_av_admin_can_view_new_form(self, av_admin_client, av_seed):
        resp = av_admin_client.get("/admin/av/spaces/new")
        assert resp.status_code == 200

    def test_av_admin_can_create_space(self, av_admin_client, av_seed):
        resp = av_admin_client.post(
            "/admin/av/spaces/new",
            data={
                "code": "TEST_SPACE",
                "name": "Test Space",
                "location": "Hall A",
                "square_feet": "1000",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        space = Space.query.filter_by(
            code="TEST_SPACE",
            event_cycle_id=av_seed["cycle"].id,
        ).first()
        assert space is not None
        assert space.name == "Test Space"
        assert space.is_active is True

    def test_super_admin_can_create_space(self, super_admin_client, av_seed):
        resp = super_admin_client.post(
            "/admin/av/spaces/new",
            data={"code": "SUPER_SPACE", "name": "Super Space"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        space = Space.query.filter_by(
            code="SUPER_SPACE",
            event_cycle_id=av_seed["cycle"].id,
        ).first()
        assert space is not None

    def test_regular_user_cannot_create_space(self, regular_client, av_seed):
        resp = regular_client.post(
            "/admin/av/spaces/new",
            data={"code": "BLOCKED", "name": "Blocked"},
        )
        assert resp.status_code == 403

    def test_unauthenticated_cannot_create_space(self, client, av_seed):
        resp = client.post(
            "/admin/av/spaces/new",
            data={"code": "UNAUTH", "name": "Unauth"},
        )
        assert resp.status_code in (302, 401, 403)

    def test_create_missing_code_rejected(self, av_admin_client, av_seed):
        resp = av_admin_client.post(
            "/admin/av/spaces/new",
            data={"code": "", "name": "No Code"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Should not have created a space
        space = Space.query.filter_by(name="No Code").first()
        assert space is None

    def test_create_missing_name_rejected(self, av_admin_client, av_seed):
        resp = av_admin_client.post(
            "/admin/av/spaces/new",
            data={"code": "NONAME", "name": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        space = Space.query.filter_by(code="NONAME").first()
        assert space is None

    def test_create_duplicate_code_rejected(self, av_admin_client, existing_space, av_seed):
        resp = av_admin_client.post(
            "/admin/av/spaces/new",
            data={"code": "EXISTING_SPACE", "name": "Duplicate"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Should still be only one space with this code
        spaces = Space.query.filter_by(
            code="EXISTING_SPACE",
            event_cycle_id=av_seed["cycle"].id,
        ).all()
        assert len(spaces) == 1

    def test_create_logs_activity_event(self, av_admin_client, av_seed):
        from app.models import ActivityEvent
        before_count = ActivityEvent.query.count()
        av_admin_client.post(
            "/admin/av/spaces/new",
            data={"code": "ACTIVITY_SPACE", "name": "Activity Space"},
            follow_redirects=True,
        )
        after_count = ActivityEvent.query.count()
        assert after_count == before_count + 1
        event = ActivityEvent.query.order_by(ActivityEvent.id.desc()).first()
        assert event.event_type == "AV_SPACE_CREATED"


# ---------------------------------------------------------------------------
# Tests: edit
# ---------------------------------------------------------------------------

class TestEditSpace:
    def test_av_admin_can_view_edit_form(self, av_admin_client, existing_space):
        resp = av_admin_client.get(f"/admin/av/spaces/{existing_space.id}/edit")
        assert resp.status_code == 200

    def test_av_admin_can_edit_space(self, av_admin_client, existing_space):
        resp = av_admin_client.post(
            f"/admin/av/spaces/{existing_space.id}/edit",
            data={
                "code": existing_space.code,
                "name": "Renamed Space",
                "location": "Hall B",
                "square_feet": "3000",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Reload from DB
        db.session.expire(existing_space)
        space = db.session.get(Space, existing_space.id)
        assert space.name == "Renamed Space"
        assert space.location == "Hall B"

    def test_regular_user_cannot_edit(self, regular_client, existing_space):
        resp = regular_client.post(
            f"/admin/av/spaces/{existing_space.id}/edit",
            data={"code": existing_space.code, "name": "Hijacked"},
        )
        assert resp.status_code == 403

    def test_edit_nonexistent_space_returns_404(self, av_admin_client, av_seed):
        resp = av_admin_client.get("/admin/av/spaces/99999/edit")
        assert resp.status_code == 404

    def test_code_field_shown_for_unlocked_space(self, av_admin_client, existing_space):
        resp = av_admin_client.get(f"/admin/av/spaces/{existing_space.id}/edit")
        body = resp.get_data(as_text=True)
        # Code field should be editable (no 'disabled' on code input)
        assert 'name="code"' in body

    def test_code_locked_when_av_requests_exist(self, av_admin_client, existing_space, av_seed):
        """When non-DRAFT AV requests reference the space, code field is locked."""
        from app.models import (
            WorkPortfolio, WorkItem, WorkLine,
            Department,
            REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_SUBMITTED,
            WORK_LINE_STATUS_PENDING, REVIEW_STAGE_APPROVAL_GROUP,
        )
        from app.models.av import AVRequestDetail

        dept = Department(code="AVDEPT", name="AV Dept", is_active=True)
        db.session.add(dept)
        db.session.flush()

        portfolio = WorkPortfolio(
            work_type_id=av_seed["work_type"].id,
            event_cycle_id=av_seed["cycle"].id,
            department_id=dept.id,
            created_by_user_id=av_seed["av_admin"].id,
        )
        db.session.add(portfolio)
        db.session.flush()

        work_item = WorkItem(
            portfolio_id=portfolio.id,
            request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED,
            public_id="TST2026-AVDEPT-AV-1",
            created_by_user_id=av_seed["av_admin"].id,
        )
        db.session.add(work_item)
        db.session.flush()

        av_detail = AVRequestDetail(
            work_item_id=work_item.id,
            space_id=existing_space.id,
            priority="MUST_HAVE",
            duration_model="FULL_EVENT",
            dept_sourced_gear_mode="NONE",
            primary_contact_name="Test",
            primary_contact_email="test@test.local",
            created_by_user_id=av_seed["av_admin"].id,
        )
        db.session.add(av_detail)
        db.session.commit()

        resp = av_admin_client.get(f"/admin/av/spaces/{existing_space.id}/edit")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "locked" in body.lower() or "disabled" in body.lower()

    def test_code_not_changeable_via_post_when_locked(self, av_admin_client, existing_space, av_seed):
        """Even if form is tampered, code change is blocked when requests exist."""
        from app.models import (
            WorkPortfolio, WorkItem, WorkLine,
            Department,
            REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_SUBMITTED,
        )
        from app.models.av import AVRequestDetail

        dept = Department(code="AVDEPT2", name="AV Dept 2", is_active=True)
        db.session.add(dept)
        db.session.flush()

        portfolio = WorkPortfolio(
            work_type_id=av_seed["work_type"].id,
            event_cycle_id=av_seed["cycle"].id,
            department_id=dept.id,
            created_by_user_id=av_seed["av_admin"].id,
        )
        db.session.add(portfolio)
        db.session.flush()

        work_item = WorkItem(
            portfolio_id=portfolio.id,
            request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED,
            public_id="TST2026-AVDEPT2-AV-1",
            created_by_user_id=av_seed["av_admin"].id,
        )
        db.session.add(work_item)
        db.session.flush()

        av_detail = AVRequestDetail(
            work_item_id=work_item.id,
            space_id=existing_space.id,
            priority="MUST_HAVE",
            duration_model="FULL_EVENT",
            dept_sourced_gear_mode="NONE",
            primary_contact_name="Test",
            primary_contact_email="test@test.local",
            created_by_user_id=av_seed["av_admin"].id,
        )
        db.session.add(av_detail)
        db.session.commit()

        original_code = existing_space.code
        resp = av_admin_client.post(
            f"/admin/av/spaces/{existing_space.id}/edit",
            data={"code": "TAMPERED_CODE", "name": "Renamed"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.expire(existing_space)
        space = db.session.get(Space, existing_space.id)
        assert space.code == original_code  # code unchanged


# ---------------------------------------------------------------------------
# Tests: archive
# ---------------------------------------------------------------------------

class TestArchiveSpace:
    def test_av_admin_can_archive_space(self, av_admin_client, existing_space):
        resp = av_admin_client.post(
            f"/admin/av/spaces/{existing_space.id}/archive",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.expire(existing_space)
        space = db.session.get(Space, existing_space.id)
        assert space.is_active is False

    def test_regular_user_cannot_archive(self, regular_client, existing_space):
        resp = regular_client.post(
            f"/admin/av/spaces/{existing_space.id}/archive",
        )
        assert resp.status_code == 403
        # Space still active
        db.session.expire(existing_space)
        space = db.session.get(Space, existing_space.id)
        assert space.is_active is True

    def test_archive_nonexistent_space_returns_404(self, av_admin_client, av_seed):
        resp = av_admin_client.post("/admin/av/spaces/99999/archive")
        assert resp.status_code == 404

    def test_archive_already_archived_shows_warning(self, av_admin_client, existing_space):
        """Archiving an already-archived space redirects with warning, not 500."""
        # First archive it
        av_admin_client.post(
            f"/admin/av/spaces/{existing_space.id}/archive",
        )
        # Try again
        resp = av_admin_client.post(
            f"/admin/av/spaces/{existing_space.id}/archive",
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Fixtures: department + space-with-dept (for assignment tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def department(av_seed):
    """A Department for use in assignment tests."""
    dept = Department(
        code="ASNDEPT",
        name="Assignment Test Dept",
        is_active=True,
    )
    db.session.add(dept)
    db.session.commit()
    return dept


@pytest.fixture
def space(av_seed):
    """A bare Space (no assignments) for assignment tests."""
    s = Space(
        event_cycle_id=av_seed["cycle"].id,
        code="ASNSPACE",
        name="Assignment Test Space",
        is_active=True,
        created_by_user_id=av_seed["av_admin"].id,
    )
    db.session.add(s)
    db.session.commit()
    return s


@pytest.fixture
def space_with_dept(av_seed, space, department):
    """A Space that already has an active assignment for `department`."""
    assignment = SpaceDepartmentAssignment(
        space_id=space.id,
        department_id=department.id,
        assigned_by_user_id=av_seed["av_admin"].id,
    )
    db.session.add(assignment)
    db.session.commit()
    return space, assignment


# ---------------------------------------------------------------------------
# Tests: manage assignments
# ---------------------------------------------------------------------------

class TestManageAssignments:
    def test_av_admin_assigns_dept(self, av_admin_client, space, department):
        response = av_admin_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "assign", "department_id": str(department.id)},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assignment = SpaceDepartmentAssignment.query.filter_by(
            space_id=space.id, department_id=department.id, unassigned_at=None,
        ).first()
        assert assignment is not None

    def test_av_admin_unassigns_dept(self, av_admin_client, space_with_dept, department):
        space, _ = space_with_dept
        response = av_admin_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "unassign", "department_id": str(department.id)},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assignment = SpaceDepartmentAssignment.query.filter_by(
            space_id=space.id, department_id=department.id,
        ).first()
        assert assignment.unassigned_at is not None

    def test_regular_user_cannot_manage_assignments(self, regular_client, space, department):
        response = regular_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "assign", "department_id": str(department.id)},
        )
        assert response.status_code == 403

    def test_assignment_page_shows_assigned_status(self, av_admin_client, space_with_dept, department):
        space, _ = space_with_dept
        response = av_admin_client.get(f"/admin/av/spaces/{space.id}/assignments")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # The assigned dept should appear with some "Assigned" indicator
        assert department.name in body
        assert "Assigned" in body

    def test_assignment_page_loads_for_av_admin(self, av_admin_client, space):
        response = av_admin_client.get(f"/admin/av/spaces/{space.id}/assignments")
        assert response.status_code == 200

    def test_regular_user_cannot_view_assignment_page(self, regular_client, space):
        response = regular_client.get(f"/admin/av/spaces/{space.id}/assignments")
        assert response.status_code == 403

    def test_reassigning_creates_new_row(self, av_admin_client, space, department):
        # Assign, unassign, re-assign — should produce 2 rows total (history preserved)
        av_admin_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "assign", "department_id": str(department.id)},
        )
        av_admin_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "unassign", "department_id": str(department.id)},
        )
        av_admin_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "assign", "department_id": str(department.id)},
        )
        rows = SpaceDepartmentAssignment.query.filter_by(
            space_id=space.id, department_id=department.id,
        ).all()
        assert len(rows) == 2  # one historical (unassigned_at set), one active
        active_rows = [r for r in rows if r.unassigned_at is None]
        assert len(active_rows) == 1

    def test_assign_idempotent_when_already_assigned(self, av_admin_client, space_with_dept, department):
        """Assigning a dept that is already assigned should not create a duplicate."""
        space, _ = space_with_dept
        av_admin_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "assign", "department_id": str(department.id)},
            follow_redirects=True,
        )
        rows = SpaceDepartmentAssignment.query.filter_by(
            space_id=space.id, department_id=department.id, unassigned_at=None,
        ).all()
        assert len(rows) == 1  # still only one active assignment

    def test_assign_logs_activity_event(self, av_admin_client, space, department):
        from app.models import ActivityEvent
        before_count = ActivityEvent.query.count()
        av_admin_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "assign", "department_id": str(department.id)},
            follow_redirects=True,
        )
        after_count = ActivityEvent.query.count()
        assert after_count == before_count + 1
        event = ActivityEvent.query.order_by(ActivityEvent.id.desc()).first()
        assert event.event_type == "AV_SPACE_DEPT_ASSIGNED"

    def test_unassign_logs_activity_event(self, av_admin_client, space_with_dept, department):
        from app.models import ActivityEvent
        space, _ = space_with_dept
        before_count = ActivityEvent.query.count()
        av_admin_client.post(
            f"/admin/av/spaces/{space.id}/assignments",
            data={"action": "unassign", "department_id": str(department.id)},
            follow_redirects=True,
        )
        after_count = ActivityEvent.query.count()
        assert after_count == before_count + 1
        event = ActivityEvent.query.order_by(ActivityEvent.id.desc()).first()
        assert event.event_type == "AV_SPACE_DEPT_UNASSIGNED"


# ---------------------------------------------------------------------------
# Fixtures: clone-from-previous tests
# ---------------------------------------------------------------------------

@pytest.fixture
def current_event(av_seed):
    """The target EventCycle to clone INTO (distinct from av_seed's cycle)."""
    cycle = EventCycle(
        code="TST2027", name="Test Event 2027",
        is_active=True, is_default=False, sort_order=2,
    )
    db.session.add(cycle)
    db.session.commit()
    return cycle


@pytest.fixture
def prior_event_with_spaces(av_seed):
    """The prior EventCycle (av_seed cycle) with two active Spaces seeded into it."""
    cycle = av_seed["cycle"]  # TST2026, sort_order=1
    s1 = Space(
        event_cycle_id=cycle.id,
        code="STAGE_A",
        name="Stage A",
        location="Main Hall",
        square_feet=5000,
        notes="Big stage",
        is_active=True,
        created_by_user_id=av_seed["av_admin"].id,
    )
    s2 = Space(
        event_cycle_id=cycle.id,
        code="PANEL_1",
        name="Panel Room 1",
        is_active=True,
        created_by_user_id=av_seed["av_admin"].id,
    )
    db.session.add_all([s1, s2])
    db.session.commit()
    return cycle


@pytest.fixture
def prior_event_with_spaces_and_assignments(av_seed):
    """Prior EventCycle with one Space that has an active department assignment.

    Returns (prior_event_cycle, department_id).
    """
    cycle = av_seed["cycle"]

    dept = Department(code="CLONEDEPT", name="Clone Test Dept", is_active=True)
    db.session.add(dept)
    db.session.flush()

    s = Space(
        event_cycle_id=cycle.id,
        code="ASSIGN_SPACE",
        name="Assignable Space",
        is_active=True,
        created_by_user_id=av_seed["av_admin"].id,
    )
    db.session.add(s)
    db.session.flush()

    assignment = SpaceDepartmentAssignment(
        space_id=s.id,
        department_id=dept.id,
        assigned_by_user_id=av_seed["av_admin"].id,
    )
    db.session.add(assignment)
    db.session.commit()
    return cycle, dept.id


# ---------------------------------------------------------------------------
# Tests: clone from previous event
# ---------------------------------------------------------------------------

class TestCloneFromPrevious:
    def test_clone_creates_spaces_in_target_event(self, av_admin_client, prior_event_with_spaces, current_event):
        """Clone copies all spaces from prior event to current event."""
        response = av_admin_client.post(
            "/admin/av/spaces/clone-from-previous",
            data={"event_cycle_id": str(current_event.id)},
            follow_redirects=True,
        )
        assert response.status_code == 200

        prior_codes = {s.code for s in Space.query.filter_by(event_cycle_id=prior_event_with_spaces.id).all()}
        current_codes = {s.code for s in Space.query.filter_by(event_cycle_id=current_event.id).all()}
        assert prior_codes == current_codes
        assert len(prior_codes) > 0

    def test_clone_skips_existing_codes(self, av_admin_client, prior_event_with_spaces, current_event):
        """Existing space in current event is preserved (not overwritten)."""
        # Pre-create one with a code that exists in prior
        s = Space(
            event_cycle_id=current_event.id,
            code="EXISTING_CODE",
            name="Pre-existing",
            is_active=True,
            created_by_user_id="seed",
        )
        db.session.add(s)
        db.session.commit()

        # Add a Space with the same code to prior event so the clone would conflict
        prior_dup = Space(
            event_cycle_id=prior_event_with_spaces.id,
            code="EXISTING_CODE",
            name="Different name in prior",
            is_active=True,
            created_by_user_id="seed",
        )
        db.session.add(prior_dup)
        db.session.commit()

        av_admin_client.post(
            "/admin/av/spaces/clone-from-previous",
            data={"event_cycle_id": str(current_event.id)},
            follow_redirects=True,
        )

        existing = Space.query.filter_by(
            event_cycle_id=current_event.id, code="EXISTING_CODE",
        ).first()
        assert existing.name == "Pre-existing"  # not overwritten

    def test_clone_includes_assignments_by_default(self, av_admin_client, prior_event_with_spaces_and_assignments, current_event):
        """When clone_assignments=true (default), SpaceDepartmentAssignment rows are also cloned."""
        prior_event, dept_id = prior_event_with_spaces_and_assignments
        av_admin_client.post(
            "/admin/av/spaces/clone-from-previous",
            data={"event_cycle_id": str(current_event.id)},
            follow_redirects=True,
        )

        cloned_space = Space.query.filter_by(
            event_cycle_id=current_event.id,
        ).first()
        assert cloned_space is not None
        new_assignment = SpaceDepartmentAssignment.query.filter_by(
            space_id=cloned_space.id, department_id=dept_id, unassigned_at=None,
        ).first()
        assert new_assignment is not None

    def test_clone_skips_assignments_when_unchecked(self, av_admin_client, prior_event_with_spaces_and_assignments, current_event):
        """When skip_assignments is set, only spaces are copied, no assignments."""
        prior_event, dept_id = prior_event_with_spaces_and_assignments
        av_admin_client.post(
            "/admin/av/spaces/clone-from-previous",
            data={"event_cycle_id": str(current_event.id), "skip_assignments": "1"},
            follow_redirects=True,
        )

        cloned_space = Space.query.filter_by(
            event_cycle_id=current_event.id,
        ).first()
        assert cloned_space is not None
        assignment = SpaceDepartmentAssignment.query.filter_by(
            space_id=cloned_space.id, department_id=dept_id,
        ).first()
        assert assignment is None  # no assignment cloned

    def test_regular_user_cannot_clone(self, regular_client, current_event):
        response = regular_client.post(
            "/admin/av/spaces/clone-from-previous",
            data={"event_cycle_id": str(current_event.id)},
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests: nav integration (Tasks 21-22)
# ---------------------------------------------------------------------------

class TestNavIntegration:
    def test_av_admin_sees_av_admin_link_in_nav(self, av_admin_client, av_seed):
        """AV admins see the AV Admin dropdown and Manage Spaces link in the nav."""
        response = av_admin_client.get("/")
        assert response.status_code == 200
        assert b"/admin/av/spaces" in response.data or b"AV Admin" in response.data

    def test_av_admin_sees_manage_spaces_link(self, av_admin_client, av_seed):
        """AV admins see the Manage Spaces link pointing to /admin/av/spaces/."""
        response = av_admin_client.get("/")
        assert response.status_code == 200
        assert b"/admin/av/spaces/" in response.data
        assert b"Manage Spaces" in response.data

    def test_regular_user_does_not_see_av_admin_link(self, regular_client, av_seed):
        """Non-admin users do not see the AV Admin dropdown in the nav."""
        response = regular_client.get("/")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "AV Admin" not in body

    def test_super_admin_sees_av_admin_link(self, super_admin_client, av_seed):
        """Super admins (who implicitly admin all work types) see the AV Admin dropdown."""
        response = super_admin_client.get("/")
        assert response.status_code == 200
        assert b"AV Admin" in response.data
        assert b"/admin/av/spaces/" in response.data
