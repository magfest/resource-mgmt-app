"""Integration tests for AV request lifecycle (dept side).

Tests the AV portfolio landing page (/<event>/<dept>/av/).
Pattern mirrors tests/integration/test_av_admin_pages.py.
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
    WorkLineReview,
    ApprovalGroup,
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
    ROUTING_STRATEGY_DIRECT,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.models.space import Space, SpaceDepartmentAssignment


# ---------------------------------------------------------------------------
# Per-file base seed fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def av_base(app):
    """Seed the minimum AV work type + org structure required for portfolio tests.

    Creates:
    - EventCycle TST2026
    - AV WorkType + WorkTypeConfig + AV_TEAM ApprovalGroup
    - super_admin user (SUPER_ADMIN role)
    - av_admin user (WORKTYPE_ADMIN scoped to AV)
    - dept_a (the dept under test)
    - dept_member user with AV view access to dept_a
    - other_dept (a different dept with no memberships for dept_member)
    - other_dept_user with AV view access only to other_dept
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

    # Users
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
    other_dept_user = User(
        id="test:other_dept_user", email="other_dept_user@test.local",
        auth_subject="test:other_dept_user", display_name="Other Dept User",
        is_active=True,
    )
    db.session.add_all([super_admin, av_admin, dept_member, other_dept_user])
    db.session.flush()

    db.session.add(UserRole(user_id=super_admin.id, role_code=ROLE_SUPER_ADMIN))
    db.session.add(UserRole(
        user_id=av_admin.id, role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=wt.id,
    ))

    # Departments
    dept_a = Department(code="ATEST", name="AV Test Dept", is_active=True)
    other_dept = Department(code="OTHERTEST", name="Other Test Dept", is_active=True)
    db.session.add_all([dept_a, other_dept])
    db.session.flush()

    # dept_member has AV view access to dept_a
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

    # other_dept_user has AV view access only to other_dept
    other_membership = DepartmentMembership(
        user_id=other_dept_user.id,
        department_id=other_dept.id,
        event_cycle_id=cycle.id,
    )
    db.session.add(other_membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=other_membership.id,
        work_type_id=wt.id,
        can_view=True,
        can_edit=False,
    ))

    db.session.commit()

    return {
        "cycle": cycle,
        "work_type": wt,
        "approval_group": av_group,
        "super_admin": super_admin,
        "av_admin": av_admin,
        "dept_member": dept_member,
        "other_dept_user": other_dept_user,
        "dept_a": dept_a,
        "other_dept": other_dept,
    }


# ---------------------------------------------------------------------------
# Client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dept_member_client(client, av_base):
    """Test client logged in as a dept member with AV view access to dept_a."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_base["dept_member"].id
    return client


@pytest.fixture
def super_admin_client(client, av_base):
    """Test client logged in as super admin."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_base["super_admin"].id
    return client


@pytest.fixture
def other_dept_member_client(client, av_base):
    """Test client logged in as a user who only has access to other_dept."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = av_base["other_dept_user"].id
    return client


# ---------------------------------------------------------------------------
# Scenario fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dept_with_av_space(av_base):
    """dept_a with one active Space assigned to it.

    Returns (event_cycle, dept_a, space).
    """
    seed = av_base
    space = Space(
        event_cycle_id=seed["cycle"].id,
        code="MAIN_STAGE",
        name="Main Stage",
        location="Hall A",
        is_active=True,
        created_by_user_id=seed["av_admin"].id,
    )
    db.session.add(space)
    db.session.flush()

    assignment = SpaceDepartmentAssignment(
        space_id=space.id,
        department_id=seed["dept_a"].id,
        assigned_by_user_id=seed["av_admin"].id,
    )
    db.session.add(assignment)
    db.session.commit()

    return seed["cycle"], seed["dept_a"], space


@pytest.fixture
def dept_no_spaces(av_base):
    """dept_a with no Space assignments.

    Returns (event_cycle, dept_a).
    """
    seed = av_base
    return seed["cycle"], seed["dept_a"]


@pytest.fixture
def dept_with_existing_request(av_base, dept_with_av_space):
    """dept_a with one existing AV WorkItem (DRAFT).

    Returns (event_cycle, dept_a, space, work_item).
    """
    from app.models.av import AVRequestDetail
    seed = av_base
    event, dept, space = dept_with_av_space

    portfolio = WorkPortfolio(
        work_type_id=seed["work_type"].id,
        event_cycle_id=event.id,
        department_id=dept.id,
        created_by_user_id=seed["av_admin"].id,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
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
    db.session.commit()

    return event, dept, space, work_item


# ---------------------------------------------------------------------------
# Tests: portfolio landing
# ---------------------------------------------------------------------------

class TestPortfolioLanding:
    def test_assigned_dept_member_sees_portfolio(
        self, dept_member_client, dept_with_av_space,
    ):
        """A dept member with AV access gets 200 and sees their dept + assigned space."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(f"/{event.code}/{dept.code}/av/")
        assert response.status_code == 200
        assert dept.name.encode() in response.data
        assert space.name.encode() in response.data

    def test_unassigned_dept_member_blocked(
        self, other_dept_member_client, av_base,
    ):
        """A user with AV access to a different dept cannot view dept_a's portfolio."""
        seed = av_base
        response = other_dept_member_client.get(
            f"/{seed['cycle'].code}/{seed['dept_a'].code}/av/"
        )
        assert response.status_code == 403

    def test_no_assigned_spaces_shows_empty_state(
        self, dept_member_client, dept_no_spaces,
    ):
        """When no spaces are assigned, page renders 200 with empty-state message."""
        event, dept = dept_no_spaces
        response = dept_member_client.get(f"/{event.code}/{dept.code}/av/")
        assert response.status_code == 200
        # Should mention the dept name even in empty state
        assert dept.name.encode() in response.data

    def test_super_admin_can_view_any_dept(self, super_admin_client, av_base):
        """Super admin can view the portfolio even without dept membership."""
        seed = av_base
        response = super_admin_client.get(
            f"/{seed['cycle'].code}/{seed['dept_a'].code}/av/"
        )
        assert response.status_code == 200

    def test_unauthenticated_user_redirected(self, client, av_base):
        """Unauthenticated requests are redirected or blocked."""
        seed = av_base
        response = client.get(
            f"/{seed['cycle'].code}/{seed['dept_a'].code}/av/"
        )
        # Expect redirect to login or 401/403
        assert response.status_code in (302, 401, 403)

    def test_invalid_event_code_returns_404(self, dept_member_client, av_base):
        """Non-existent event code returns 404."""
        seed = av_base
        response = dept_member_client.get(
            f"/BADCODE/{seed['dept_a'].code}/av/"
        )
        assert response.status_code == 404

    def test_invalid_dept_code_returns_404(self, dept_member_client, av_base):
        """Non-existent dept code returns 404."""
        seed = av_base
        response = dept_member_client.get(
            f"/{seed['cycle'].code}/BADDEPT/av/"
        )
        assert response.status_code == 404

    def test_space_location_shown_when_set(
        self, dept_member_client, dept_with_av_space,
    ):
        """Space location is rendered in the assigned spaces section."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(f"/{event.code}/{dept.code}/av/")
        assert response.status_code == 200
        assert b"Hall A" in response.data

    def test_existing_requests_shown_in_table(
        self, dept_member_client, dept_with_existing_request,
    ):
        """Existing AV work items appear in the requests table."""
        event, dept, space, work_item = dept_with_existing_request
        response = dept_member_client.get(f"/{event.code}/{dept.code}/av/")
        assert response.status_code == 200
        assert work_item.public_id.encode() in response.data

    def test_no_portfolio_no_requests_still_renders(
        self, dept_member_client, dept_with_av_space,
    ):
        """When no portfolio exists (no requests filed), the page still renders."""
        event, dept, space = dept_with_av_space
        # No portfolio / work items seeded for this (event, dept, AV)
        response = dept_member_client.get(f"/{event.code}/{dept.code}/av/")
        assert response.status_code == 200

    def test_av_work_type_not_seeded_returns_404(self, client, app):
        """If the AV WorkType doesn't exist in DB, route returns 404."""
        # Seed minimal event + dept but NO AV WorkType
        cycle = EventCycle(
            code="NOAV2026", name="No AV Event",
            is_active=True, is_default=False, sort_order=99,
        )
        dept = Department(code="NOAVDEPT", name="No AV Dept", is_active=True)
        user = User(
            id="test:noav_user", email="noav@test.local",
            auth_subject="test:noav_user", display_name="No AV",
            is_active=True,
        )
        db.session.add_all([cycle, dept, user])
        db.session.commit()

        with client.session_transaction() as sess:
            sess["active_user_id"] = user.id

        response = client.get(f"/{cycle.code}/{dept.code}/av/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Fixtures for create-request tests
# ---------------------------------------------------------------------------

@pytest.fixture
def unrelated_space(av_base):
    """A Space in the same event but NOT assigned to dept_a.

    Used in tests that verify cross-dept space blocking.
    """
    seed = av_base
    space = Space(
        event_cycle_id=seed["cycle"].id,
        code="UNRELATED_STAGE",
        name="Unrelated Stage",
        is_active=True,
        created_by_user_id=seed["av_admin"].id,
    )
    db.session.add(space)
    # No SpaceDepartmentAssignment — intentionally unassigned to dept_a
    db.session.commit()
    return space


def _post_draft(client, event, dept, space_id, overrides=None):
    """Helper: POST a minimal valid save_draft payload."""
    data = {
        "space_id": str(space_id),
        "description": "Lab demo setup",
        "priority": "MUST_HAVE",
        "duration_model": "HOURS_OF_CONTENT",
        "duration_hours": "1.5",
        "gear_specificity": "USAGE_ONLY",
        "dept_sourced_gear_mode": "NONE",
        "primary_contact_name": "D. Reed",
        "primary_contact_email": "d@example.com",
        "action": "save_draft",
    }
    if overrides:
        data.update(overrides)
    return client.post(
        f"/{event.code}/{dept.code}/av/new",
        data=data,
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Tests: create request
# ---------------------------------------------------------------------------

class TestCreateRequest:
    def test_get_new_form_renders(self, dept_member_client, dept_with_av_space):
        """GET /new renders the create form for an authorised dept member."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(f"/{event.code}/{dept.code}/av/new")
        assert response.status_code == 200
        assert b"New AV Request" in response.data

    def test_get_new_form_preselects_space(self, dept_member_client, dept_with_av_space):
        """GET /new?space_id=X pre-selects the given space in the form."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            f"/{event.code}/{dept.code}/av/new?space_id={space.id}"
        )
        assert response.status_code == 200
        # The space code should appear in the rendered page
        assert space.code.encode() in response.data

    def test_dept_member_can_save_draft(self, dept_member_client, dept_with_av_space):
        """POST save_draft creates WorkItem, AVRequestDetail, WorkLine, AVLineDetail."""
        from app.models.av import AVRequestDetail, AVLineDetail

        event, dept, space = dept_with_av_space
        response = _post_draft(dept_member_client, event, dept, space.id)

        # Should redirect to portfolio landing
        assert response.status_code in (302, 303)

        item = WorkItem.query.filter_by(status="DRAFT").first()
        assert item is not None
        assert item.av_request_detail is not None
        assert item.av_request_detail.priority == "MUST_HAVE"
        assert item.av_request_detail.space_id == space.id
        assert item.av_request_detail.duration_model == "HOURS_OF_CONTENT"

        # public_id format: {EVENT}-{DEPT}-AV-1
        assert item.public_id.endswith("-AV-1")
        assert event.code in item.public_id
        assert dept.code in item.public_id

        # WorkLine + AVLineDetail created
        assert len(item.lines) == 1
        line = item.lines[0]
        assert line.line_number == 1
        assert line.av_line_detail is not None
        assert line.av_line_detail.description == "Lab demo setup"
        assert line.av_line_detail.gear_specificity == "USAGE_ONLY"

    def test_portfolio_created_on_first_request(self, dept_member_client, dept_with_av_space, av_base):
        """A WorkPortfolio is auto-created when no portfolio exists yet."""
        event, dept, space = dept_with_av_space
        seed = av_base
        wt = seed["work_type"]

        # Verify no portfolio exists yet
        assert WorkPortfolio.query.filter_by(
            event_cycle_id=event.id, department_id=dept.id, work_type_id=wt.id,
        ).first() is None

        _post_draft(dept_member_client, event, dept, space.id)

        portfolio = WorkPortfolio.query.filter_by(
            event_cycle_id=event.id, department_id=dept.id, work_type_id=wt.id,
        ).first()
        assert portfolio is not None

    def test_sequence_increments_on_second_request(self, dept_member_client, dept_with_av_space):
        """A second draft gets sequence -2."""
        event, dept, space = dept_with_av_space

        _post_draft(dept_member_client, event, dept, space.id)
        _post_draft(dept_member_client, event, dept, space.id)

        items = WorkItem.query.order_by(WorkItem.created_at).all()
        assert len(items) == 2
        assert items[0].public_id.endswith("-AV-1")
        assert items[1].public_id.endswith("-AV-2")

    def test_unassigned_space_blocked(self, dept_member_client, dept_no_spaces, unrelated_space):
        """Space not assigned to dept returns 403."""
        event, dept = dept_no_spaces
        response = _post_draft(dept_member_client, event, dept, unrelated_space.id)
        assert response.status_code in (400, 403)

    def test_validation_duration_hours_required(self, dept_member_client, dept_with_av_space):
        """Missing duration_hours when model=HOURS_OF_CONTENT re-renders form with errors."""
        event, dept, space = dept_with_av_space
        response = _post_draft(
            dept_member_client, event, dept, space.id,
            overrides={"duration_hours": ""},
        )
        # Should re-render form with error, not redirect
        assert response.status_code in (200, 400)
        assert b"required" in response.data.lower() or b"error" in response.data.lower()

    def test_validation_duration_slots_required(self, dept_member_client, dept_with_av_space):
        """Missing duration_slots when model=MULTIPLE_SLOTS re-renders form."""
        event, dept, space = dept_with_av_space
        response = _post_draft(
            dept_member_client, event, dept, space.id,
            overrides={"duration_model": "MULTIPLE_SLOTS", "duration_hours": ""},
        )
        assert response.status_code in (200, 400)

    def test_validation_suggested_gear_required_when_suggestions(self, dept_member_client, dept_with_av_space):
        """suggested_gear_text required when gear_specificity=SUGGESTIONS."""
        event, dept, space = dept_with_av_space
        response = _post_draft(
            dept_member_client, event, dept, space.id,
            overrides={"gear_specificity": "SUGGESTIONS", "suggested_gear_text": ""},
        )
        assert response.status_code in (200, 400)

    def test_full_event_duration_saves_without_hours(self, dept_member_client, dept_with_av_space):
        """FULL_EVENT model does not require duration_hours."""
        event, dept, space = dept_with_av_space
        response = _post_draft(
            dept_member_client, event, dept, space.id,
            overrides={"duration_model": "FULL_EVENT", "duration_hours": ""},
        )
        assert response.status_code in (302, 303)
        item = WorkItem.query.filter_by(status="DRAFT").first()
        assert item is not None
        assert item.av_request_detail.duration_model == "FULL_EVENT"
        assert item.av_request_detail.duration_hours is None

    def test_dept_sourced_gear_text_saved(self, dept_member_client, dept_with_av_space):
        """SOME dept_sourced_gear_mode saves the gear text."""
        event, dept, space = dept_with_av_space
        response = _post_draft(
            dept_member_client, event, dept, space.id,
            overrides={
                "dept_sourced_gear_mode": "SOME",
                "dept_sourced_gear_text": "We're bringing our own mixer.",
            },
        )
        assert response.status_code in (302, 303)
        item = WorkItem.query.filter_by(status="DRAFT").first()
        assert item.av_request_detail.dept_sourced_gear_mode == "SOME"
        assert item.av_request_detail.dept_sourced_gear_text == "We're bringing our own mixer."

    def test_no_worklinereviews_created(self, dept_member_client, dept_with_av_space):
        """WorkLineReview is NOT created at save_draft (that is Task 25)."""
        from app.models import WorkLineReview

        event, dept, space = dept_with_av_space
        _post_draft(dept_member_client, event, dept, space.id)

        item = WorkItem.query.filter_by(status="DRAFT").first()
        assert item is not None
        assert item.lines[0].reviews == []

    def test_unauthenticated_redirect(self, client, dept_with_av_space):
        """Unauthenticated POST is rejected."""
        event, dept, space = dept_with_av_space
        response = _post_draft(client, event, dept, space.id)
        assert response.status_code in (302, 401, 403)


# ---------------------------------------------------------------------------
# Fixtures for submit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def av_draft_request(av_base, dept_with_av_space):
    """A fully-formed DRAFT AV WorkItem with one line.

    Returns the WorkItem.
    """
    from app.models.av import AVRequestDetail, AVLineDetail

    seed = av_base
    event, dept, space = dept_with_av_space

    portfolio = WorkPortfolio.query.filter_by(
        event_cycle_id=event.id,
        department_id=dept.id,
        work_type_id=seed["work_type"].id,
    ).first()
    if portfolio is None:
        portfolio = WorkPortfolio(
            event_cycle_id=event.id,
            department_id=dept.id,
            work_type_id=seed["work_type"].id,
            created_by_user_id=seed["dept_member"].id,
            next_public_id_seq=1,
        )
        db.session.add(portfolio)
        db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
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
        primary_contact_name="Dept Member",
        primary_contact_email="dept_member@test.local",
        created_by_user_id=seed["dept_member"].id,
    )
    db.session.add(av_detail)

    work_line = WorkLine(
        work_item_id=work_item.id,
        line_number=1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(work_line)
    db.session.flush()

    from app.models.av import AVLineDetail
    line_detail = AVLineDetail(
        work_line_id=work_line.id,
        description="Need a projector and screen",
        gear_specificity="USAGE_ONLY",
    )
    db.session.add(line_detail)
    db.session.commit()

    return work_item


@pytest.fixture
def av_submitted_request(av_base, av_draft_request):
    """An AV WorkItem that has already been submitted (SUBMITTED status).

    Builds on av_draft_request: calls the submit helper directly to
    transition it and create the WorkLineReview row.
    """
    from app.routes.work.av.submit import _do_submit

    seed = av_base

    # Build a minimal duck-typed user context to satisfy submit_work_item.
    class FakeUserCtx:
        user_id = seed["dept_member"].id
        is_super_admin = False
        user = seed["dept_member"]
        approval_group_ids = set()

    _do_submit(av_draft_request, FakeUserCtx())
    db.session.expire_all()

    return WorkItem.query.get(av_draft_request.id)


# ---------------------------------------------------------------------------
# Tests: submit request
# ---------------------------------------------------------------------------

def _post_submit(client, event, dept, public_id):
    """Helper: POST the submit endpoint for an existing request."""
    return client.post(
        f"/{event.code}/{dept.code}/av/item/{public_id}/submit",
        follow_redirects=False,
    )


class TestSubmit:
    def test_dept_member_submits_draft(
        self, dept_member_client, av_base, av_draft_request, dept_with_av_space,
    ):
        """Dept member can submit a DRAFT request → status becomes SUBMITTED,
        WorkLineReview created PENDING, routed to AV_TEAM."""
        event, dept, space = dept_with_av_space
        response = _post_submit(
            dept_member_client, event, dept, av_draft_request.public_id,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.status == WORK_ITEM_STATUS_SUBMITTED

        line = item.lines[0]
        assert len(line.reviews) == 1
        review = line.reviews[0]
        assert review.status == "PENDING"
        av_team = ApprovalGroup.query.filter_by(code="AV_TEAM").first()
        assert review.approval_group_id == av_team.id

        # Snapshot set on AVLineDetail
        assert line.av_line_detail.routed_approval_group_id == av_team.id

    def test_submit_blocked_for_non_draft(
        self, dept_member_client, av_base, av_submitted_request, dept_with_av_space,
    ):
        """Cannot submit a request that is already SUBMITTED."""
        event, dept, space = dept_with_av_space
        response = _post_submit(
            dept_member_client, event, dept, av_submitted_request.public_id,
        )
        assert response.status_code in (302, 303, 400, 409)
        # If it redirected (flash-based), verify status unchanged
        db.session.expire_all()
        item = WorkItem.query.get(av_submitted_request.id)
        assert item.status == WORK_ITEM_STATUS_SUBMITTED

    def test_submit_blocked_for_other_dept(
        self, other_dept_member_client, av_base, av_draft_request, dept_with_av_space,
    ):
        """User with access to a different dept cannot submit another dept's request."""
        event, dept, space = dept_with_av_space
        response = _post_submit(
            other_dept_member_client, event, dept, av_draft_request.public_id,
        )
        assert response.status_code in (302, 303, 403)
        # Request must still be DRAFT
        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.status == WORK_ITEM_STATUS_DRAFT

    def test_create_with_action_submit_creates_and_submits(
        self, dept_member_client, dept_with_av_space,
    ):
        """Posting the create form with action=submit creates the request AND
        submits it in one go — status ends up SUBMITTED, review created."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.post(
            f"/{event.code}/{dept.code}/av/new",
            data={
                "space_id": str(space.id),
                "description": "Lab demo setup",
                "priority": "MUST_HAVE",
                "duration_model": "FULL_EVENT",
                "gear_specificity": "USAGE_ONLY",
                "dept_sourced_gear_mode": "NONE",
                "primary_contact_name": "D. Reed",
                "primary_contact_email": "d@example.com",
                "action": "submit",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        item = WorkItem.query.first()
        assert item is not None
        assert item.status == WORK_ITEM_STATUS_SUBMITTED

        line = item.lines[0]
        assert len(line.reviews) == 1
        review = line.reviews[0]
        assert review.status == "PENDING"
        av_team = ApprovalGroup.query.filter_by(code="AV_TEAM").first()
        assert review.approval_group_id == av_team.id
        assert line.av_line_detail.routed_approval_group_id == av_team.id


# ---------------------------------------------------------------------------
# Helpers for edit tests
# ---------------------------------------------------------------------------

def _edit_url(event, dept, public_id):
    return f"/{event.code}/{dept.code}/av/item/{public_id}/edit"


def _minimal_edit_payload(space_id, overrides=None):
    """Minimal valid edit POST payload (save_draft)."""
    data = {
        "space_id": str(space_id),
        "description": "Updated description",
        "priority": "NICE_TO_HAVE",
        "duration_model": "FULL_EVENT",
        "gear_specificity": "USAGE_ONLY",
        "dept_sourced_gear_mode": "NONE",
        "primary_contact_name": "Updated Name",
        "primary_contact_email": "updated@x.z",
        "action": "save_draft",
    }
    if overrides:
        data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Tests: edit request
# ---------------------------------------------------------------------------

class TestEdit:
    def test_get_edit_form_prefills_fields(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """GET /edit renders the form pre-filled with the saved record's values."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            _edit_url(event, dept, av_draft_request.public_id)
        )
        assert response.status_code == 200
        # Page header references the public_id
        assert av_draft_request.public_id.encode() in response.data
        # Field value from fixture (priority=MUST_HAVE) should appear checked
        assert b"MUST_HAVE" in response.data

    def test_dept_member_can_edit_draft(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """Dept member can POST a valid edit and all changed fields are persisted."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.post(
            _edit_url(event, dept, av_draft_request.public_id),
            data=_minimal_edit_payload(space.id),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.av_request_detail.priority == "NICE_TO_HAVE"
        assert item.av_request_detail.primary_contact_name == "Updated Name"
        assert item.av_request_detail.primary_contact_email == "updated@x.z"
        assert item.lines[0].av_line_detail.description == "Updated description"

    def test_edit_preserves_draft_status(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """save_draft action leaves the request in DRAFT status."""
        event, dept, space = dept_with_av_space
        dept_member_client.post(
            _edit_url(event, dept, av_draft_request.public_id),
            data=_minimal_edit_payload(space.id),
            follow_redirects=False,
        )
        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.status == WORK_ITEM_STATUS_DRAFT

    def test_cannot_edit_submitted(
        self, dept_member_client, av_submitted_request, dept_with_av_space,
    ):
        """Editing a SUBMITTED request redirects with an error flash;
        the original data is NOT modified."""
        event, dept, space = dept_with_av_space
        original_priority = av_submitted_request.av_request_detail.priority

        response = dept_member_client.post(
            _edit_url(event, dept, av_submitted_request.public_id),
            data=_minimal_edit_payload(space.id),
            follow_redirects=False,
        )
        # Redirect (flash-based error) rather than saving
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_submitted_request.id)
        assert item.av_request_detail.priority == original_priority

    def test_other_dept_cannot_edit(
        self, other_dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """User with access only to a different dept gets 403."""
        event, dept, space = dept_with_av_space
        response = other_dept_member_client.post(
            _edit_url(event, dept, av_draft_request.public_id),
            data=_minimal_edit_payload(space.id),
            follow_redirects=False,
        )
        assert response.status_code == 403

    def test_edit_with_action_submit_submits(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """action=submit on the edit form saves changes AND transitions to SUBMITTED."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.post(
            _edit_url(event, dept, av_draft_request.public_id),
            data=_minimal_edit_payload(space.id, overrides={
                "priority": "STRONG_PREFERENCE",
                "action": "submit",
            }),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.status == WORK_ITEM_STATUS_SUBMITTED
        # Saved change is also persisted
        assert item.av_request_detail.priority == "STRONG_PREFERENCE"
        # WorkLineReview created
        assert len(item.lines[0].reviews) == 1

    def test_edit_validation_error_rerenders_form(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """Invalid POST (missing required field) re-renders the form, no DB change."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.post(
            _edit_url(event, dept, av_draft_request.public_id),
            data=_minimal_edit_payload(space.id, overrides={"description": ""}),
            follow_redirects=False,
        )
        assert response.status_code in (200, 400)
        # Original description unchanged
        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.lines[0].av_line_detail.description == "Need a projector and screen"

    def test_edit_duration_hours_model_updates_correctly(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """Switching from FULL_EVENT to HOURS_OF_CONTENT persists duration_hours."""
        event, dept, space = dept_with_av_space
        dept_member_client.post(
            _edit_url(event, dept, av_draft_request.public_id),
            data=_minimal_edit_payload(space.id, overrides={
                "duration_model": "HOURS_OF_CONTENT",
                "duration_hours": "4.5",
            }),
            follow_redirects=False,
        )
        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.av_request_detail.duration_model == "HOURS_OF_CONTENT"
        assert float(item.av_request_detail.duration_hours) == 4.5

    def test_edit_switching_to_full_event_clears_hours(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """Switching duration_model to FULL_EVENT clears duration_hours."""
        event, dept, space = dept_with_av_space
        # av_draft_request already has FULL_EVENT, but we re-save to be explicit
        dept_member_client.post(
            _edit_url(event, dept, av_draft_request.public_id),
            data=_minimal_edit_payload(space.id, overrides={
                "duration_model": "FULL_EVENT",
                "duration_hours": "",
            }),
            follow_redirects=False,
        )
        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.av_request_detail.duration_model == "FULL_EVENT"
        assert item.av_request_detail.duration_hours is None


# ---------------------------------------------------------------------------
# Fixtures for view-detail tests (Task 27)
# ---------------------------------------------------------------------------

@pytest.fixture
def other_dept_b_user(av_base):
    """A user who belongs to a THIRD dept (dept_b) with no shared space with dept_a.

    Used in test_unrelated_dept_blocked.
    """
    seed = av_base
    wt = seed["work_type"]

    dept_b = Department(code="DEPTB", name="Dept B", is_active=True)
    db.session.add(dept_b)
    db.session.flush()

    user_b = User(
        id="test:dept_b_member", email="dept_b@test.local",
        auth_subject="test:dept_b_member", display_name="Dept B Member",
        is_active=True,
    )
    db.session.add(user_b)
    db.session.flush()

    membership = DepartmentMembership(
        user_id=user_b.id,
        department_id=dept_b.id,
        event_cycle_id=seed["cycle"].id,
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

    return user_b, dept_b


@pytest.fixture
def unrelated_dept_member_client(client, av_base, other_dept_b_user):
    """Client logged in as a member of a dept with no shared space with dept_a."""
    user_b, dept_b = other_dept_b_user
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_b.id
    return client


@pytest.fixture
def other_assigned_dept_user(av_base):
    """A user assigned to dept_c, which will be given the SAME space as dept_a.

    The fixture itself doesn't assign the space — that's done by
    av_submitted_request_in_shared_space to keep setup localised.
    """
    seed = av_base
    wt = seed["work_type"]

    dept_c = Department(code="DEPTC", name="Dept C", is_active=True)
    db.session.add(dept_c)
    db.session.flush()

    user_c = User(
        id="test:dept_c_member", email="dept_c@test.local",
        auth_subject="test:dept_c_member", display_name="Dept C Member",
        is_active=True,
    )
    db.session.add(user_c)
    db.session.flush()

    membership = DepartmentMembership(
        user_id=user_c.id,
        department_id=dept_c.id,
        event_cycle_id=seed["cycle"].id,
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

    return user_c, dept_c


@pytest.fixture
def other_assigned_dept_member_client(client, av_base, other_assigned_dept_user):
    """Client logged in as a member of dept_c (shared-space scenario)."""
    user_c, dept_c = other_assigned_dept_user
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_c.id
    return client


@pytest.fixture
def av_submitted_request_in_shared_space(
    av_base, av_submitted_request, other_assigned_dept_user,
):
    """An SUBMITTED AV request for dept_a where dept_c is ALSO assigned to the same space.

    Enables the cross-dept visibility test:
    - dept_a filed the request for the space.
    - dept_c is also assigned to the same space → should be able to view it.
    """
    seed = av_base
    _, dept_c = other_assigned_dept_user

    # Retrieve the space from the submitted request's detail
    db.session.expire_all()
    item = WorkItem.query.get(av_submitted_request.id)
    space_id = item.av_request_detail.space_id

    assignment = SpaceDepartmentAssignment(
        space_id=space_id,
        department_id=dept_c.id,
        assigned_by_user_id=seed["av_admin"].id,
    )
    db.session.add(assignment)
    db.session.commit()

    return item


# ---------------------------------------------------------------------------
# Tests: view detail page (Task 27)
# ---------------------------------------------------------------------------

def _view_url(event, dept, public_id):
    return f"/{event.code}/{dept.code}/av/item/{public_id}"


class TestViewDetail:
    def test_dept_member_views_own_draft_request(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """Dept member can view their own DRAFT request detail page."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            _view_url(event, dept, av_draft_request.public_id)
        )
        assert response.status_code == 200
        assert av_draft_request.public_id.encode() in response.data

    def test_renders_request_fields(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """All key submitted form fields are visible on the detail page."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            _view_url(event, dept, av_draft_request.public_id)
        )
        assert response.status_code == 200
        # Contact name from fixture
        assert b"Dept Member" in response.data
        # Description from fixture
        assert b"Need a projector and screen" in response.data
        # Space name
        assert space.name.encode() in response.data

    def test_edit_button_visible_on_draft(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """Edit Draft button is visible when status is DRAFT."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            _view_url(event, dept, av_draft_request.public_id)
        )
        assert response.status_code == 200
        assert b"Edit Draft" in response.data

    def test_edit_button_absent_on_submitted(
        self, dept_member_client, av_submitted_request, dept_with_av_space,
    ):
        """Edit Draft button is NOT shown when request is SUBMITTED."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            _view_url(event, dept, av_submitted_request.public_id)
        )
        assert response.status_code == 200
        assert b"Edit Draft" not in response.data

    def test_recall_button_when_submitted_pending(
        self, dept_member_client, av_submitted_request, dept_with_av_space,
    ):
        """SUBMITTED request with PENDING review shows Recall to Draft button."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            _view_url(event, dept, av_submitted_request.public_id)
        )
        assert response.status_code == 200
        # Case-insensitive check for "recall" text
        lower = response.data.lower()
        assert b"recall" in lower

    def test_no_plans_shows_placeholder(
        self, dept_member_client, av_draft_request, dept_with_av_space,
    ):
        """When no AV plans exist, a placeholder message is shown."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            _view_url(event, dept, av_draft_request.public_id)
        )
        assert response.status_code == 200
        assert b"not yet published" in response.data.lower()

    def test_unrelated_dept_blocked(
        self, unrelated_dept_member_client, av_submitted_request, dept_with_av_space,
    ):
        """User with access only to an unrelated dept (no shared space) gets 403."""
        event, dept, space = dept_with_av_space
        response = unrelated_dept_member_client.get(
            _view_url(event, dept, av_submitted_request.public_id)
        )
        assert response.status_code == 403

    def test_other_assigned_dept_can_view(
        self, other_assigned_dept_member_client,
        av_submitted_request_in_shared_space,
        dept_with_av_space,
    ):
        """A user whose dept is ALSO assigned to the same space can view the request."""
        event, dept, space = dept_with_av_space
        item = av_submitted_request_in_shared_space
        response = other_assigned_dept_member_client.get(
            _view_url(event, dept, item.public_id)
        )
        assert response.status_code == 200

    def test_super_admin_can_view_any_request(
        self, super_admin_client, av_draft_request, dept_with_av_space,
    ):
        """Super admin can view any request regardless of space membership."""
        event, dept, space = dept_with_av_space
        response = super_admin_client.get(
            _view_url(event, dept, av_draft_request.public_id)
        )
        assert response.status_code == 200

    def test_invalid_public_id_returns_404(
        self, dept_member_client, dept_with_av_space,
    ):
        """Non-existent public_id returns 404."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.get(
            _view_url(event, dept, "TST2026-ATEST-AV-9999")
        )
        assert response.status_code == 404

    def test_unauthenticated_redirected(self, client, av_draft_request, dept_with_av_space):
        """Unauthenticated request is redirected or blocked."""
        event, dept, space = dept_with_av_space
        response = client.get(
            _view_url(event, dept, av_draft_request.public_id)
        )
        assert response.status_code in (302, 401, 403)


# ---------------------------------------------------------------------------
# Fixtures for recall tests (Task 29)
# ---------------------------------------------------------------------------

@pytest.fixture
def av_logged_request(av_base, av_submitted_request):
    """An AV WorkItem that is SUBMITTED and has an AVRequestPlan published.

    Simulates the AV team having already logged/planned the request, which
    should block recall.  The WorkLineReview status is left PENDING (the
    block comes from the plan existing, not the review status in this fixture).
    """
    from app.models.av import AVRequestPlan

    seed = av_base

    plan = AVRequestPlan(
        work_item_id=av_submitted_request.id,
        revision=1,
        gear_spec="Projector + screen",
        authored_by_user_id=seed["av_admin"].id,
    )
    db.session.add(plan)
    db.session.commit()
    db.session.expire_all()

    return WorkItem.query.get(av_submitted_request.id)


# ---------------------------------------------------------------------------
# Helper for recall
# ---------------------------------------------------------------------------

def _post_recall(client, event, dept, public_id):
    """POST the recall endpoint for an existing request."""
    return client.post(
        f"/{event.code}/{dept.code}/av/item/{public_id}/recall",
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Tests: recall to draft (Task 29)
# ---------------------------------------------------------------------------

class TestRecall:
    def test_dept_member_can_recall_pending_submitted(
        self, dept_member_client, av_base, av_submitted_request, dept_with_av_space,
    ):
        """Dept member can recall a SUBMITTED request with a PENDING review → DRAFT."""
        event, dept, space = dept_with_av_space
        response = _post_recall(
            dept_member_client, event, dept, av_submitted_request.public_id,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_submitted_request.id)
        assert item.status == WORK_ITEM_STATUS_DRAFT

    def test_recall_deletes_pending_review(
        self, dept_member_client, av_base, av_submitted_request, dept_with_av_space,
    ):
        """After recall, the PENDING WorkLineReview row is deleted."""
        event, dept, space = dept_with_av_space

        # Verify a review exists before recall
        item_before = WorkItem.query.get(av_submitted_request.id)
        assert len(item_before.lines[0].reviews) == 1

        _post_recall(dept_member_client, event, dept, av_submitted_request.public_id)

        db.session.expire_all()
        item = WorkItem.query.get(av_submitted_request.id)
        assert len(item.lines[0].reviews) == 0

    def test_recall_clears_submitted_fields(
        self, dept_member_client, av_base, av_submitted_request, dept_with_av_space,
    ):
        """After recall, submitted_at and submitted_by_user_id are cleared."""
        event, dept, space = dept_with_av_space
        _post_recall(dept_member_client, event, dept, av_submitted_request.public_id)

        db.session.expire_all()
        item = WorkItem.query.get(av_submitted_request.id)
        assert item.submitted_at is None
        assert item.submitted_by_user_id is None

    def test_cannot_recall_when_plan_exists(
        self, dept_member_client, av_base, av_logged_request, dept_with_av_space,
    ):
        """Once the AV team has published a Plan, recall is blocked."""
        event, dept, space = dept_with_av_space
        response = _post_recall(
            dept_member_client, event, dept, av_logged_request.public_id,
        )
        # Status must not change to DRAFT
        db.session.expire_all()
        item = WorkItem.query.get(av_logged_request.id)
        assert item.status == WORK_ITEM_STATUS_SUBMITTED

    def test_cannot_recall_draft(
        self, dept_member_client, av_base, av_draft_request, dept_with_av_space,
    ):
        """Recalling a DRAFT request is a no-op (wrong status → flash + redirect)."""
        event, dept, space = dept_with_av_space
        response = _post_recall(
            dept_member_client, event, dept, av_draft_request.public_id,
        )
        db.session.expire_all()
        item = WorkItem.query.get(av_draft_request.id)
        assert item.status == WORK_ITEM_STATUS_DRAFT  # unchanged

    def test_other_dept_cannot_recall(
        self, other_dept_member_client, av_base, av_submitted_request, dept_with_av_space,
    ):
        """User with access only to a different dept gets 403."""
        event, dept, space = dept_with_av_space
        response = _post_recall(
            other_dept_member_client, event, dept, av_submitted_request.public_id,
        )
        assert response.status_code == 403
        # Status must not change
        db.session.expire_all()
        item = WorkItem.query.get(av_submitted_request.id)
        assert item.status == WORK_ITEM_STATUS_SUBMITTED


# ---------------------------------------------------------------------------
# Fixtures for respond-to-kickback tests (Task 30)
# ---------------------------------------------------------------------------

def _make_kickback_request(av_submitted_request, review_status):
    """Mutate the latest WorkLineReview to the given kickback status.

    Returns the WorkItem (refreshed from DB).
    """
    db.session.expire_all()
    item = WorkItem.query.get(av_submitted_request.id)
    line = item.lines[0]
    review = max(line.reviews, key=lambda r: r.id)
    review.status = review_status
    db.session.commit()
    db.session.expire_all()
    return WorkItem.query.get(item.id)


@pytest.fixture
def av_request_with_needs_info(av_submitted_request):
    """A SUBMITTED AV request whose latest WorkLineReview is NEEDS_INFO."""
    return _make_kickback_request(av_submitted_request, "NEEDS_INFO")


@pytest.fixture
def av_request_with_needs_adjustment(av_submitted_request):
    """A SUBMITTED AV request whose latest WorkLineReview is NEEDS_ADJUSTMENT."""
    return _make_kickback_request(av_submitted_request, "NEEDS_ADJUSTMENT")


def _respond_url(event, dept, public_id):
    return f"/{event.code}/{dept.code}/av/item/{public_id}/respond"


# ---------------------------------------------------------------------------
# Tests: respond to kickback (Task 30)
# ---------------------------------------------------------------------------

class TestRespond:
    def test_dept_member_responds_to_needs_info(
        self, dept_member_client, av_base, av_request_with_needs_info, dept_with_av_space,
    ):
        """Dept member can respond to a NEEDS_INFO kickback; review returns to PENDING."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.post(
            _respond_url(event, dept, av_request_with_needs_info.public_id),
            data={"response_text": "We need stereo audio for the video clips"},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_request_with_needs_info.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "PENDING"

    def test_response_requires_text(
        self, dept_member_client, av_base, av_request_with_needs_info, dept_with_av_space,
    ):
        """Empty response_text is rejected; review status stays NEEDS_INFO."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.post(
            _respond_url(event, dept, av_request_with_needs_info.public_id),
            data={"response_text": ""},
            follow_redirects=False,
        )
        # Should redirect back with a flash error, not 4xx
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_request_with_needs_info.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "NEEDS_INFO"  # unchanged

    def test_cannot_respond_when_pending(
        self, dept_member_client, av_base, av_submitted_request, dept_with_av_space,
    ):
        """Responding to a PENDING review (not in kickback state) is rejected."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.post(
            _respond_url(event, dept, av_submitted_request.public_id),
            data={"response_text": "A response to nothing"},
            follow_redirects=False,
        )
        # Should redirect with flash error
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "PENDING"  # unchanged

    def test_other_dept_cannot_respond(
        self, other_dept_member_client, av_base, av_request_with_needs_info, dept_with_av_space,
    ):
        """User with access only to a different dept gets 403."""
        event, dept, space = dept_with_av_space
        response = other_dept_member_client.post(
            _respond_url(event, dept, av_request_with_needs_info.public_id),
            data={"response_text": "Unauthorized response"},
            follow_redirects=False,
        )
        assert response.status_code == 403

        db.session.expire_all()
        item = WorkItem.query.get(av_request_with_needs_info.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "NEEDS_INFO"  # unchanged

    def test_edit_resets_needs_adjustment_to_pending(
        self, dept_member_client, av_base, av_request_with_needs_adjustment, dept_with_av_space,
    ):
        """Saving the edit form resets a NEEDS_ADJUSTMENT review back to PENDING."""
        event, dept, space = dept_with_av_space
        response = dept_member_client.post(
            _edit_url(event, dept, av_request_with_needs_adjustment.public_id),
            data=_minimal_edit_payload(space.id),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_request_with_needs_adjustment.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "PENDING"

    def test_edit_blocked_for_submitted_pending_review(
        self, dept_member_client, av_base, av_submitted_request, dept_with_av_space,
    ):
        """Editing a SUBMITTED request with a PENDING review is still blocked."""
        event, dept, space = dept_with_av_space
        original_priority = av_submitted_request.av_request_detail.priority

        response = dept_member_client.post(
            _edit_url(event, dept, av_submitted_request.public_id),
            data=_minimal_edit_payload(space.id, overrides={"priority": "NICE_TO_HAVE"}),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = WorkItem.query.get(av_submitted_request.id)
        # Priority must be unchanged — edit was blocked
        assert item.av_request_detail.priority == original_priority

    def test_respond_creates_audit_event(
        self, dept_member_client, av_base, av_request_with_needs_info, dept_with_av_space,
    ):
        """A REQUESTER_RESPONSE audit event is created on the work line."""
        from app.models import WorkLineAuditEvent

        event, dept, space = dept_with_av_space
        dept_member_client.post(
            _respond_url(event, dept, av_request_with_needs_info.public_id),
            data={"response_text": "Here is the info you asked for."},
            follow_redirects=False,
        )

        db.session.expire_all()
        item = WorkItem.query.get(av_request_with_needs_info.id)
        line = item.lines[0]
        audit_events = WorkLineAuditEvent.query.filter_by(
            work_line_id=line.id,
            event_type="REQUESTER_RESPONSE",
        ).all()
        assert len(audit_events) == 1
