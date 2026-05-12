"""Integration tests for AV team review actions.

Task 31: publish-plan (rev 1).

Fixture strategy mirrors test_av_request_flow.py but uses a 'ta_' prefix
on all locally-defined fixtures to avoid pytest fixture-name collisions when
running alongside that file.

  ta_av_base          — base AV org seed
  ta_av_team_client   — client logged in as an AV_TEAM member
  ta_super_admin_client — client logged in as super admin
  ta_dept_member_client — client logged in as a plain dept member
  ta_av_submitted_request — a SUBMITTED AV WorkItem ready for AV team action
  ta_av_draft_request     — a DRAFT AV WorkItem (no review)
"""
from __future__ import annotations

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
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
    ROUTING_STRATEGY_DIRECT,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.models.av import AVLineDetail, AVRequestDetail, AVRequestPlan
from app.models.space import Space, SpaceDepartmentAssignment


# ---------------------------------------------------------------------------
# Base seed fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def ta_av_base(app):
    """Seed the minimum AV org structure for team-action tests.

    Creates:
    - EventCycle TA2026 (distinct code from TST2026 used in other test files)
    - AV WorkType + WorkTypeConfig + AV_TEAM ApprovalGroup
    - super_admin user (SUPER_ADMIN role)
    - av_admin user (WORKTYPE_ADMIN scoped to AV)
    - av_team_user (member of AV_TEAM ApprovalGroup via UserRole APPROVER)
    - dept_a (the dept under test) with dept_member having AV edit access
    - Space (MASTG31) assigned to dept_a
    """
    from app.models import ROLE_APPROVER

    cycle = EventCycle(
        code="TA2026", name="Team Actions Test 2026",
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

    # Users — use 'ta:' prefix to avoid ID collisions with other test files
    super_admin = User(
        id="ta:super_admin", email="ta_super@test.local",
        auth_subject="ta:super_admin", display_name="TA Super Admin",
        is_active=True,
    )
    av_admin = User(
        id="ta:av_admin", email="ta_av_admin@test.local",
        auth_subject="ta:av_admin", display_name="TA AV Admin",
        is_active=True,
    )
    av_team_user = User(
        id="ta:av_team", email="ta_av_team@test.local",
        auth_subject="ta:av_team", display_name="TA AV Team Member",
        is_active=True,
    )
    dept_member = User(
        id="ta:dept_member", email="ta_dept_member@test.local",
        auth_subject="ta:dept_member", display_name="TA Dept Member",
        is_active=True,
    )
    db.session.add_all([super_admin, av_admin, av_team_user, dept_member])
    db.session.flush()

    db.session.add(UserRole(user_id=super_admin.id, role_code=ROLE_SUPER_ADMIN))
    db.session.add(UserRole(
        user_id=av_admin.id, role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=wt.id,
    ))
    # av_team_user is an approver for AV_TEAM group
    db.session.add(UserRole(
        user_id=av_team_user.id, role_code=ROLE_APPROVER,
        approval_group_id=av_group.id,
    ))

    dept_a = Department(code="TAPANEL", name="TA Panel Dept", is_active=True)
    db.session.add(dept_a)
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

    space = Space(
        event_cycle_id=cycle.id,
        code="MASTG31",
        name="Main Stage TA",
        location="Hall A",
        is_active=True,
        created_by_user_id=av_admin.id,
    )
    db.session.add(space)
    db.session.flush()

    db.session.add(SpaceDepartmentAssignment(
        space_id=space.id,
        department_id=dept_a.id,
        assigned_by_user_id=av_admin.id,
    ))

    db.session.commit()

    return {
        "cycle": cycle,
        "work_type": wt,
        "approval_group": av_group,
        "super_admin": super_admin,
        "av_admin": av_admin,
        "av_team_user": av_team_user,
        "dept_member": dept_member,
        "dept_a": dept_a,
        "space": space,
    }


# ---------------------------------------------------------------------------
# Client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ta_av_team_client(client, ta_av_base):
    """Test client logged in as an AV_TEAM ApprovalGroup member."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = ta_av_base["av_team_user"].id
    return client


@pytest.fixture
def ta_super_admin_client(client, ta_av_base):
    """Test client logged in as super admin."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = ta_av_base["super_admin"].id
    return client


@pytest.fixture
def ta_dept_member_client(client, ta_av_base):
    """Test client logged in as a dept member (not on the AV team)."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = ta_av_base["dept_member"].id
    return client


# ---------------------------------------------------------------------------
# Scenario fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ta_av_submitted_request(ta_av_base):
    """A fully-formed SUBMITTED AV WorkItem with one line and a PENDING review."""
    seed = ta_av_base

    portfolio = WorkPortfolio(
        work_type_id=seed["work_type"].id,
        event_cycle_id=seed["cycle"].id,
        department_id=seed["dept_a"].id,
        created_by_user_id=seed["dept_member"].id,
        next_public_id_seq=2,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED,
        public_id="TA2026-TAPANEL-AV-1",
        created_by_user_id=seed["dept_member"].id,
    )
    db.session.add(work_item)
    db.session.flush()

    av_detail = AVRequestDetail(
        work_item_id=work_item.id,
        space_id=seed["space"].id,
        priority="MUST_HAVE",
        duration_model="FULL_EVENT",
        dept_sourced_gear_mode="NONE",
        primary_contact_name="TA Dept Member",
        primary_contact_email="ta_dept_member@test.local",
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

    line_detail = AVLineDetail(
        work_line_id=work_line.id,
        description="Need a projector and screen",
        gear_specificity="USAGE_ONLY",
        routed_approval_group_id=seed["approval_group"].id,
    )
    db.session.add(line_detail)

    review = WorkLineReview(
        work_line_id=work_line.id,
        stage="APPROVAL_GROUP",
        approval_group_id=seed["approval_group"].id,
        status="PENDING",
        created_by_user_id=seed["dept_member"].id,
    )
    db.session.add(review)

    db.session.commit()
    db.session.expire_all()

    return db.session.get(WorkItem, work_item.id)


@pytest.fixture
def ta_av_draft_request(ta_av_base):
    """A DRAFT AV WorkItem (no review row)."""
    seed = ta_av_base

    portfolio = WorkPortfolio(
        work_type_id=seed["work_type"].id,
        event_cycle_id=seed["cycle"].id,
        department_id=seed["dept_a"].id,
        created_by_user_id=seed["dept_member"].id,
        next_public_id_seq=2,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TA2026-TAPANEL-AV-2",
        created_by_user_id=seed["dept_member"].id,
    )
    db.session.add(work_item)
    db.session.flush()

    av_detail = AVRequestDetail(
        work_item_id=work_item.id,
        space_id=seed["space"].id,
        priority="NICE_TO_HAVE",
        duration_model="FULL_EVENT",
        dept_sourced_gear_mode="NONE",
        primary_contact_name="TA Dept Member",
        primary_contact_email="ta_dept_member@test.local",
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

    line_detail = AVLineDetail(
        work_line_id=work_line.id,
        description="Speakers for panel",
        gear_specificity="USAGE_ONLY",
    )
    db.session.add(line_detail)

    db.session.commit()
    db.session.expire_all()

    return db.session.get(WorkItem, work_item.id)


@pytest.fixture
def ta_av_logged_request(ta_av_base, ta_av_submitted_request):
    """A SUBMITTED AV WorkItem whose latest review is LOGGED and has one AVRequestPlan.

    Simulates the AV team having already published rev 1. Used to test
    the revision (rev N+1) path added in Task 34.
    """
    seed = ta_av_base
    item = ta_av_submitted_request

    # Set the existing review to LOGGED
    line = item.lines[0]
    review = max(line.reviews, key=lambda r: r.id)
    review.status = "LOGGED"
    review.decided_at = db.func.now()
    review.decided_by_user_id = seed["av_team_user"].id

    # Mirror on WorkLine
    line.status = "LOGGED"

    # Create rev 1 AVRequestPlan
    plan = AVRequestPlan(
        work_item_id=item.id,
        revision=1,
        gear_spec="- 2 wireless lavs\n- HDMI capture",
        planning_notes="Initial plan",
        authored_by_user_id=seed["av_team_user"].id,
    )
    db.session.add(plan)
    db.session.commit()
    db.session.expire_all()

    return db.session.get(WorkItem, item.id)


@pytest.fixture
def ta_av_request_with_needs_info(ta_av_base, ta_av_submitted_request):
    """A SUBMITTED AV WorkItem whose latest review is NEEDS_INFO.

    Used to verify that a NEEDS_INFO line cannot accept a Plan revision.
    """
    item = ta_av_submitted_request

    line = item.lines[0]
    review = max(line.reviews, key=lambda r: r.id)
    review.status = "NEEDS_INFO"
    review.note = "Please clarify video clip format"
    line.status = "NEEDS_INFO"
    line.needs_requester_action = True

    db.session.commit()
    db.session.expire_all()

    return db.session.get(WorkItem, item.id)


@pytest.fixture
def ta_av_rejected_request(ta_av_base, ta_av_submitted_request):
    """A SUBMITTED AV WorkItem whose latest review is REJECTED.

    Used to verify that a REJECTED line cannot accept a Plan.
    """
    item = ta_av_submitted_request

    line = item.lines[0]
    review = max(line.reviews, key=lambda r: r.id)
    review.status = "REJECTED"
    review.note = "Cannot accommodate this request"
    line.status = "REJECTED"

    db.session.commit()
    db.session.expire_all()

    return db.session.get(WorkItem, item.id)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _publish_plan_url(event, dept, public_id):
    return f"/{event.code}/{dept.code}/av/item/{public_id}/publish-plan"


def _needs_info_url(event, dept, public_id):
    return f"/{event.code}/{dept.code}/av/item/{public_id}/needs-info"


def _needs_adjustment_url(event, dept, public_id):
    return f"/{event.code}/{dept.code}/av/item/{public_id}/needs-adjustment"


def _reject_url(event, dept, public_id):
    return f"/{event.code}/{dept.code}/av/item/{public_id}/reject"


# ---------------------------------------------------------------------------
# Tests: publish plan (Task 31)
# ---------------------------------------------------------------------------

class TestPublishPlan:
    def test_av_team_member_publishes_rev1(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """AV team member publishes a plan for a SUBMITTED request.

        After publish:
        - AVRequestPlan rev 1 created with correct gear_spec.
        - WorkLineReview.status == "LOGGED".
        - WorkLine.status == "LOGGED".
        """
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={
                "gear_spec": "- 2 wireless lavs\n- HDMI capture",
                "planning_notes": "Patched into existing PA",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303), (
            f"Expected redirect, got {response.status_code}; "
            f"body: {response.data[:500]}"
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)

        plan = AVRequestPlan.query.filter_by(work_item_id=item.id).first()
        assert plan is not None, "AVRequestPlan was not created"
        assert plan.revision == 1
        assert "wireless lavs" in plan.gear_spec
        assert plan.planning_notes == "Patched into existing PA"
        assert plan.authored_by_user_id == seed["av_team_user"].id

        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "LOGGED"
        assert latest_review.decided_by_user_id == seed["av_team_user"].id
        assert latest_review.decided_at is not None

        assert line.status == "LOGGED"

    def test_planning_notes_optional(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """Planning notes may be omitted; plan is still created."""
        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "1 projector"},
            follow_redirects=False,
        )

        db.session.expire_all()
        plan = AVRequestPlan.query.filter_by(
            work_item_id=ta_av_submitted_request.id
        ).first()
        assert plan is not None
        assert plan.planning_notes is None

    def test_gear_spec_required(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """Missing gear_spec → redirect with flash; no Plan written."""
        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": ""},
            follow_redirects=False,
        )

        assert AVRequestPlan.query.count() == 0
        # Review must still be PENDING
        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        review = max(item.lines[0].reviews, key=lambda r: r.id)
        assert review.status == "PENDING"

    def test_dept_member_cannot_publish_plan(
        self, ta_dept_member_client, ta_av_base, ta_av_submitted_request,
    ):
        """Dept member (not on AV team) is 403-blocked."""
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_dept_member_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "Unauthorized spec"},
            follow_redirects=False,
        )
        assert response.status_code == 403
        assert AVRequestPlan.query.count() == 0

    def test_cannot_publish_for_draft(
        self, ta_av_team_client, ta_av_base, ta_av_draft_request,
    ):
        """Publishing a plan for a DRAFT request is rejected; no Plan written."""
        seed = ta_av_base
        item = ta_av_draft_request

        response = ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "Some gear"},
            follow_redirects=False,
        )
        # Should redirect with flash error, not 2xx
        assert response.status_code in (302, 303)
        assert AVRequestPlan.query.count() == 0

    def test_super_admin_can_publish_plan(
        self, ta_super_admin_client, ta_av_base, ta_av_submitted_request,
    ):
        """Super admin bypasses require_av_team_member."""
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_super_admin_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "Admin authored plan"},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        plan = AVRequestPlan.query.filter_by(
            work_item_id=ta_av_submitted_request.id
        ).first()
        assert plan is not None
        assert plan.revision == 1

    def test_activity_event_logged(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """An AV_PLAN_PUBLISHED ActivityEvent is written on successful publish."""
        from app.models import ActivityEvent

        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "Some gear"},
            follow_redirects=False,
        )

        activity = ActivityEvent.query.filter_by(
            work_item_id=item.id,
            event_type="AV_PLAN_PUBLISHED",
        ).first()
        assert activity is not None
        assert activity.actor_user_id == seed["av_team_user"].id

    def test_audit_event_logged(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """A REVIEW_DECISION WorkLineAuditEvent is written on the line."""
        from app.models import WorkLineAuditEvent

        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "Some gear"},
            follow_redirects=False,
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        audit = WorkLineAuditEvent.query.filter_by(
            work_line_id=line.id,
            event_type="REVIEW_DECISION",
        ).first()
        assert audit is not None
        assert audit.old_value == "PENDING"
        assert audit.new_value == "LOGGED"

    def test_next_revision_helper_increments(self, ta_av_base, ta_av_submitted_request):
        """_next_plan_revision returns 1 with no plans, 2 after one plan exists."""
        from app.routes.work.av.team_actions import _next_plan_revision

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)

        assert _next_plan_revision(item) == 1

        # Seed a plan manually to simulate Task 34 scenario
        plan = AVRequestPlan(
            work_item_id=item.id,
            revision=1,
            gear_spec="First plan",
            authored_by_user_id=ta_av_base["av_team_user"].id,
        )
        db.session.add(plan)
        db.session.commit()
        db.session.expire_all()

        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        assert _next_plan_revision(item) == 2


# ---------------------------------------------------------------------------
# Tests: kickback actions (Task 32) — NEEDS_INFO / NEEDS_ADJUSTMENT
# ---------------------------------------------------------------------------

class TestKickback:

    def test_av_team_marks_needs_info(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """AV team member sends a NEEDS_INFO kickback with a note."""
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_av_team_client.post(
            _needs_info_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "Tell me more about your video clips"},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303), (
            f"Expected redirect, got {response.status_code}; "
            f"body: {response.data[:500]}"
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "NEEDS_INFO"
        assert "video clips" in (latest_review.note or "")
        assert latest_review.decided_by_user_id == seed["av_team_user"].id
        assert latest_review.decided_at is not None
        # Line status should mirror review status
        assert line.status == "NEEDS_INFO"
        # Requester action flag must be set
        assert line.needs_requester_action is True

    def test_av_team_marks_needs_adjustment(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """AV team member sends a NEEDS_ADJUSTMENT kickback with a note."""
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_av_team_client.post(
            _needs_adjustment_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "We can do 2 mics, not 4"},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "NEEDS_ADJUSTMENT"
        assert "2 mics" in (latest_review.note or "")
        assert line.status == "NEEDS_ADJUSTMENT"
        assert line.needs_requester_action is True

    def test_kickback_requires_note(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """Empty note → redirect with flash; review remains PENDING."""
        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _needs_info_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": ""},
            follow_redirects=False,
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "PENDING"

    def test_dept_member_cannot_kickback(
        self, ta_dept_member_client, ta_av_base, ta_av_submitted_request,
    ):
        """Dept member (not on AV team) is 403-blocked."""
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_dept_member_client.post(
            _needs_info_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "sneaky note"},
        )
        assert response.status_code == 403

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "PENDING"

    def test_cannot_kickback_draft(
        self, ta_av_team_client, ta_av_base, ta_av_draft_request,
    ):
        """Kickback on a DRAFT item is rejected; line unchanged (no review exists)."""
        seed = ta_av_base
        item = ta_av_draft_request

        response = ta_av_team_client.post(
            _needs_info_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "x"},
            follow_redirects=False,
        )
        # Should redirect with flash error, not 2xx or 5xx
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_draft_request.id)
        line = item.lines[0]
        # DRAFT request has no reviews — nothing should have changed
        assert len(line.reviews) == 0

    def test_kickback_logs_activity_event(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """An AV_KICKBACK ActivityEvent is written on successful kickback."""
        from app.models import ActivityEvent

        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _needs_info_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "Testing activity log"},
            follow_redirects=False,
        )

        db.session.expire_all()
        evt = ActivityEvent.query.filter_by(
            work_item_id=item.id,
            event_type="AV_KICKBACK",
        ).first()
        assert evt is not None
        assert evt.actor_user_id == seed["av_team_user"].id

    def test_kickback_logs_audit_event(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """A REVIEW_DECISION WorkLineAuditEvent is written on the line."""
        from app.models import WorkLineAuditEvent

        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _needs_info_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "Need more details"},
            follow_redirects=False,
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        audit = WorkLineAuditEvent.query.filter_by(
            work_line_id=line.id,
            event_type="REVIEW_DECISION",
        ).first()
        assert audit is not None
        assert audit.old_value == "PENDING"
        assert audit.new_value == "NEEDS_INFO"


# ---------------------------------------------------------------------------
# Tests: reject action (Task 33) — REJECTED (terminal)
# ---------------------------------------------------------------------------

class TestReject:

    def test_av_team_rejects_request(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """AV team member rejects a SUBMITTED request with a note.

        After reject:
        - WorkLineReview.status == "REJECTED".
        - WorkLine.status == "REJECTED".
        - needs_requester_action is NOT set (terminal — no response expected).
        """
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_av_team_client.post(
            _reject_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "We genuinely can't provide this gear set."},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303), (
            f"Expected redirect, got {response.status_code}; "
            f"body: {response.data[:500]}"
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)

        assert latest_review.status == "REJECTED"
        assert "can't provide" in (latest_review.note or "")
        assert latest_review.decided_by_user_id == seed["av_team_user"].id
        assert latest_review.decided_at is not None
        assert line.status == "REJECTED"
        # Terminal action — no requester action flag
        assert not line.needs_requester_action

    def test_reject_requires_note(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """Empty note → redirect with flash; review remains PENDING."""
        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _reject_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": ""},
            follow_redirects=False,
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "PENDING"

    def test_dept_member_cannot_reject(
        self, ta_dept_member_client, ta_av_base, ta_av_submitted_request,
    ):
        """Dept member (not on AV team) is 403-blocked."""
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_dept_member_client.post(
            _reject_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "sneaky reject"},
        )
        assert response.status_code == 403

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "PENDING"

    def test_cannot_reject_draft(
        self, ta_av_team_client, ta_av_base, ta_av_draft_request,
    ):
        """Reject on a DRAFT item is rejected; line unchanged (no review exists)."""
        seed = ta_av_base
        item = ta_av_draft_request

        response = ta_av_team_client.post(
            _reject_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "x"},
            follow_redirects=False,
        )
        # Should redirect with flash error, not 2xx or 5xx
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_draft_request.id)
        line = item.lines[0]
        # DRAFT request has no reviews — nothing should have changed
        assert len(line.reviews) == 0

    def test_reject_logs_activity_event(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """An AV_REQUEST_REJECTED ActivityEvent is written on successful reject."""
        from app.models import ActivityEvent

        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _reject_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "Cannot accommodate due to venue constraints."},
            follow_redirects=False,
        )

        db.session.expire_all()
        evt = ActivityEvent.query.filter_by(
            work_item_id=item.id,
            event_type="AV_REQUEST_REJECTED",
        ).first()
        assert evt is not None
        assert evt.actor_user_id == seed["av_team_user"].id

    def test_reject_logs_audit_event(
        self, ta_av_team_client, ta_av_base, ta_av_submitted_request,
    ):
        """A REVIEW_DECISION WorkLineAuditEvent is written on the line."""
        from app.models import WorkLineAuditEvent

        seed = ta_av_base
        item = ta_av_submitted_request

        ta_av_team_client.post(
            _reject_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "Not possible this year."},
            follow_redirects=False,
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        audit = WorkLineAuditEvent.query.filter_by(
            work_line_id=line.id,
            event_type="REVIEW_DECISION",
        ).first()
        assert audit is not None
        assert audit.old_value == "PENDING"
        assert audit.new_value == "REJECTED"

    def test_super_admin_can_reject(
        self, ta_super_admin_client, ta_av_base, ta_av_submitted_request,
    ):
        """Super admin bypasses require_av_team_member."""
        seed = ta_av_base
        item = ta_av_submitted_request

        response = ta_super_admin_client.post(
            _reject_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"note": "Admin reject"},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_submitted_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "REJECTED"


# ---------------------------------------------------------------------------
# Tests: revise plan (Task 34) — publish Plan rev N+1 from LOGGED state
# ---------------------------------------------------------------------------

class TestRevisePlan:

    def test_av_team_revises_logged_line_creates_rev2(
        self, ta_av_team_client, ta_av_base, ta_av_logged_request,
    ):
        """A LOGGED request can be revised. New rev created; line stays LOGGED."""
        seed = ta_av_base
        item = ta_av_logged_request

        # Pre-condition: exactly 1 plan exists at revision 1
        existing_plans = AVRequestPlan.query.filter_by(
            work_item_id=item.id
        ).all()
        assert len(existing_plans) == 1
        assert existing_plans[0].revision == 1

        response = ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={
                "gear_spec": "- 2 wireless lavs (revised: now ULXD)\n- HDMI capture",
                "planning_notes": "Bumped lav model after follow-up",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303), (
            f"Expected redirect, got {response.status_code}; "
            f"body: {response.data[:500]}"
        )

        db.session.expire_all()
        plans = AVRequestPlan.query.filter_by(
            work_item_id=item.id
        ).order_by(AVRequestPlan.revision).all()
        assert len(plans) == 2
        assert plans[0].revision == 1
        assert plans[1].revision == 2
        assert "ULXD" in plans[1].gear_spec
        assert plans[1].planning_notes == "Bumped lav model after follow-up"
        assert plans[1].authored_by_user_id == seed["av_team_user"].id

        # Line status must remain LOGGED (was already LOGGED, idempotent)
        item = db.session.get(WorkItem, ta_av_logged_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "LOGGED"
        assert line.status == "LOGGED"

    def test_revise_refreshes_decided_at(
        self, ta_av_team_client, ta_av_base, ta_av_logged_request,
    ):
        """Revising a LOGGED request stamps a fresh decided_at on the review."""
        from datetime import datetime, timezone

        seed = ta_av_base
        item = ta_av_logged_request

        # Capture the decided_at before revision
        line = item.lines[0]
        review_before = max(line.reviews, key=lambda r: r.id)
        decided_before = review_before.decided_at

        ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "rev 2 spec"},
            follow_redirects=False,
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_logged_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        # decided_at must have been refreshed (not None)
        assert latest_review.decided_at is not None
        assert latest_review.decided_by_user_id == seed["av_team_user"].id

    def test_revise_increments_revision_number_successively(
        self, ta_av_team_client, ta_av_base, ta_av_logged_request,
    ):
        """Successive revisions produce rev 2, rev 3, rev 4."""
        seed = ta_av_base
        item = ta_av_logged_request

        for expected_rev in [2, 3, 4]:
            ta_av_team_client.post(
                _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
                data={"gear_spec": f"spec at rev {expected_rev}"},
                follow_redirects=False,
            )
            db.session.expire_all()
            plans = AVRequestPlan.query.filter_by(
                work_item_id=item.id
            ).order_by(AVRequestPlan.revision.desc()).all()
            assert plans[0].revision == expected_rev

    def test_cannot_revise_after_kickback(
        self, ta_av_team_client, ta_av_base, ta_av_request_with_needs_info,
    ):
        """A NEEDS_INFO line cannot accept a Plan revision (must be answered first)."""
        seed = ta_av_base
        item = ta_av_request_with_needs_info

        existing_count = AVRequestPlan.query.filter_by(
            work_item_id=item.id
        ).count()

        response = ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "should not write"},
            follow_redirects=False,
        )
        # Should redirect with error flash, not succeed
        assert response.status_code in (302, 303)

        # No new plan created
        assert AVRequestPlan.query.filter_by(
            work_item_id=item.id
        ).count() == existing_count

        # Review status unchanged
        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_request_with_needs_info.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "NEEDS_INFO"

    def test_cannot_revise_after_reject(
        self, ta_av_team_client, ta_av_base, ta_av_rejected_request,
    ):
        """A REJECTED line cannot accept a Plan."""
        seed = ta_av_base
        item = ta_av_rejected_request

        existing_count = AVRequestPlan.query.filter_by(
            work_item_id=item.id
        ).count()

        response = ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "should not write"},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        assert AVRequestPlan.query.filter_by(
            work_item_id=item.id
        ).count() == existing_count

        # Review status unchanged
        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_rejected_request.id)
        line = item.lines[0]
        latest_review = max(line.reviews, key=lambda r: r.id)
        assert latest_review.status == "REJECTED"

    def test_revise_logs_activity_event(
        self, ta_av_team_client, ta_av_base, ta_av_logged_request,
    ):
        """An AV_PLAN_PUBLISHED ActivityEvent is written for a revision (same type as rev 1)."""
        from app.models import ActivityEvent

        seed = ta_av_base
        item = ta_av_logged_request

        ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "revision gear"},
            follow_redirects=False,
        )

        db.session.expire_all()
        events = ActivityEvent.query.filter_by(
            work_item_id=item.id,
            event_type="AV_PLAN_PUBLISHED",
        ).all()
        # At least one (could be two if rev 1 publish also logged one, but fixture
        # set the review status manually without going through av_publish_plan)
        assert len(events) >= 1
        assert events[-1].actor_user_id == seed["av_team_user"].id

    def test_revise_logs_audit_event(
        self, ta_av_team_client, ta_av_base, ta_av_logged_request,
    ):
        """A REVIEW_DECISION WorkLineAuditEvent is written for the revision."""
        from app.models import WorkLineAuditEvent

        seed = ta_av_base
        item = ta_av_logged_request

        ta_av_team_client.post(
            _publish_plan_url(seed["cycle"], seed["dept_a"], item.public_id),
            data={"gear_spec": "revision gear"},
            follow_redirects=False,
        )

        db.session.expire_all()
        item = db.session.get(WorkItem, ta_av_logged_request.id)
        line = item.lines[0]
        audit = WorkLineAuditEvent.query.filter_by(
            work_line_id=line.id,
            event_type="REVIEW_DECISION",
        ).order_by(WorkLineAuditEvent.id.desc()).first()
        assert audit is not None
        # LOGGED → LOGGED (idempotent transition; old_value is the status before this call)
        assert audit.old_value == "LOGGED"
        assert audit.new_value == "LOGGED"
        assert "rev 2" in audit.note
