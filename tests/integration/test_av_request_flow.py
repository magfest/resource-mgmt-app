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
