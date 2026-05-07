"""
Tests for AV-specific line/item detail models (AVRequestDetail, AVLineDetail).

Setup is local to this module so the AV tests don't depend on (or contaminate)
the BUDGET-shaped seed_workflow_data fixture used elsewhere.
"""
import pytest

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    EventCycle,
    User,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROUTING_STRATEGY_DIRECT,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_PENDING,
)
from app.models.space import Space
from app.models.av import AVRequestDetail, AVLineDetail, AVRequestPlan, AVScope, AVAcknowledgment, AVScopeIncorporatedRequest


@pytest.fixture(scope="function")
def av_seed(app):
    """Minimal AV fixture: WorkType + config, one approval group, one Space,
    one DRAFT work item with one work line, ready for tests to attach
    AVRequestDetail / AVLineDetail to."""
    user = User(
        id="test:av_user", email="av@test.local",
        display_name="AV Tester", is_active=True,
    )
    db.session.add(user)

    cycle = EventCycle(
        code="TST2026", name="Test Event 2026",
        is_active=True, is_default=True, sort_order=1,
    )
    dept = Department(
        code="TESTDEPT", name="Test Department", is_active=True,
    )
    db.session.add_all([cycle, dept])

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

    space = Space(
        event_cycle_id=cycle.id,
        code="PANELS_4",
        name="Panels 4",
        location="Chesapeake A/B/C",
        is_active=True,
        created_by_user_id=user.id,
    )
    db.session.add(space)
    db.session.flush()

    portfolio = WorkPortfolio(
        work_type_id=wt.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=user.id,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-TESTDEPT-AV-1",
        created_by_user_id=user.id,
    )
    db.session.add(work_item)
    db.session.flush()

    line = WorkLine(
        work_item_id=work_item.id, line_number=1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(line)
    db.session.commit()

    return {
        "user": user,
        "cycle": cycle,
        "dept": dept,
        "work_type": wt,
        "av_group": av_group,
        "space": space,
        "work_item": work_item,
        "line": line,
    }


def test_av_request_detail_create(av_seed):
    """AVRequestDetail round-trips and persists all required fields."""
    work_item = av_seed["work_item"]
    space = av_seed["space"]

    detail = AVRequestDetail(
        work_item_id=work_item.id,
        space_id=space.id,
        priority="MUST_HAVE",
        duration_model="HOURS_OF_CONTENT",
        duration_hours=1.5,
        dept_sourced_gear_mode="NONE",
        primary_contact_name="Test Contact",
        primary_contact_email="contact@example.com",
        created_by_user_id="user_1",
    )
    db.session.add(detail)
    db.session.commit()

    fetched = db.session.query(AVRequestDetail).one()
    assert fetched.work_item_id == work_item.id
    assert fetched.priority == "MUST_HAVE"
    assert float(fetched.duration_hours) == 1.5


def test_av_line_detail_create(av_seed):
    """AVLineDetail round-trips and persists required fields."""
    line = av_seed["line"]

    detail = AVLineDetail(
        work_line_id=line.id,
        description="Lab demo with two presenters",
        gear_specificity="USAGE_ONLY",
    )
    db.session.add(detail)
    db.session.commit()

    fetched = db.session.query(AVLineDetail).one()
    assert fetched.work_line_id == line.id
    assert fetched.description == "Lab demo with two presenters"
    assert fetched.gear_specificity == "USAGE_ONLY"


def test_av_request_detail_backref(av_seed):
    """WorkItem.av_request_detail back-ref returns the attached detail row."""
    work_item = av_seed["work_item"]
    space = av_seed["space"]

    detail = AVRequestDetail(
        work_item_id=work_item.id,
        space_id=space.id,
        priority="MUST_HAVE",
        duration_model="FULL_EVENT",
        dept_sourced_gear_mode="NONE",
        primary_contact_name="X",
        primary_contact_email="x@y.z",
        created_by_user_id="u",
    )
    db.session.add(detail)
    db.session.commit()

    item = db.session.query(WorkItem).filter_by(id=work_item.id).one()
    assert item.av_request_detail is not None
    assert item.av_request_detail.priority == "MUST_HAVE"


def test_av_line_detail_backref(av_seed):
    """WorkLine.av_line_detail back-ref returns the attached detail row."""
    line = av_seed["line"]

    detail = AVLineDetail(
        work_line_id=line.id,
        description="Main stage keynote setup",
        gear_specificity="SPECIFIC_GEAR",
        suggested_gear_text="2x podium mics, confidence monitor",
    )
    db.session.add(detail)
    db.session.commit()

    fetched_line = db.session.query(WorkLine).filter_by(id=line.id).one()
    assert fetched_line.av_line_detail is not None
    assert fetched_line.av_line_detail.suggested_gear_text == "2x podium mics, confidence monitor"


def test_av_request_detail_cascade_on_work_item_delete(av_seed):
    """Deleting a WorkItem cascades to its AVRequestDetail."""
    work_item = av_seed["work_item"]
    space = av_seed["space"]

    detail = AVRequestDetail(
        work_item_id=work_item.id,
        space_id=space.id,
        priority="NICE_TO_HAVE",
        duration_model="FULL_EVENT",
        dept_sourced_gear_mode="NONE",
        primary_contact_name="Cascade Test",
        primary_contact_email="cascade@test.local",
        created_by_user_id="u",
    )
    db.session.add(detail)
    db.session.commit()

    db.session.delete(av_seed["line"])
    db.session.flush()
    db.session.delete(work_item)
    db.session.commit()

    assert db.session.query(AVRequestDetail).count() == 0


def test_av_line_detail_cascade_on_work_line_delete(av_seed):
    """Deleting a WorkLine cascades to its AVLineDetail."""
    line = av_seed["line"]

    detail = AVLineDetail(
        work_line_id=line.id,
        description="Will be deleted",
        gear_specificity="USAGE_ONLY",
    )
    db.session.add(detail)
    db.session.commit()

    db.session.delete(line)
    db.session.commit()

    assert db.session.query(AVLineDetail).count() == 0


def test_av_line_detail_routed_approval_group(av_seed):
    """AVLineDetail can hold a routed_approval_group_id snapshot."""
    line = av_seed["line"]
    av_group = av_seed["av_group"]

    detail = AVLineDetail(
        work_line_id=line.id,
        description="Routed line",
        gear_specificity="USAGE_ONLY",
        routed_approval_group_id=av_group.id,
    )
    db.session.add(detail)
    db.session.commit()

    fetched = db.session.query(AVLineDetail).one()
    assert fetched.routed_approval_group.code == "AV_TEAM"


@pytest.fixture(scope="function")
def work_item(av_seed):
    """Named fixture returning the WorkItem from av_seed for plan tests."""
    return av_seed["work_item"]


def test_av_request_plan_create(work_item):
    plan = AVRequestPlan(
        work_item_id=work_item.id,
        revision=1,
        gear_spec="- 2 wireless lavs\n- HDMI capture\n- House PA patch",
        planning_notes="Patched into existing PA, no extra speakers needed",
        authored_by_user_id="av_user_1",
    )
    db.session.add(plan)
    db.session.commit()
    assert plan.id is not None
    assert plan.revision == 1


def test_av_request_plan_revision_unique(work_item):
    db.session.add(AVRequestPlan(
        work_item_id=work_item.id, revision=1, gear_spec="x",
        authored_by_user_id="u",
    ))
    db.session.commit()
    db.session.add(AVRequestPlan(
        work_item_id=work_item.id, revision=1, gear_spec="dup",
        authored_by_user_id="u",
    ))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


@pytest.fixture(scope="function")
def space(av_seed):
    """Named fixture returning the Space from av_seed for scope tests."""
    return av_seed["space"]


def test_av_scope_create_draft(space):
    scope = AVScope(
        space_id=space.id,
        version=1,
        state="DRAFT",
        scope_text="Initial scope draft",
        authored_by_user_id="av_admin_1",
    )
    db.session.add(scope)
    db.session.commit()
    assert scope.id is not None
    assert scope.state == "DRAFT"
    assert scope.published_at is None
    assert scope.locked_at is None


def test_av_scope_version_unique_per_space(space):
    db.session.add(AVScope(
        space_id=space.id, version=1, state="DRAFT",
        scope_text="x", authored_by_user_id="u",
    ))
    db.session.commit()
    db.session.add(AVScope(
        space_id=space.id, version=1, state="DRAFT",
        scope_text="dup", authored_by_user_id="u",
    ))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


@pytest.fixture(scope="function")
def department(av_seed):
    """Named fixture returning the Department from av_seed for acknowledgment tests."""
    return av_seed["dept"]


def test_av_ack_create_pending(space, department):
    scope = AVScope(
        space_id=space.id, version=1, state="OPEN_FOR_INPUT",
        scope_text="x", authored_by_user_id="u",
    )
    db.session.add(scope)
    db.session.flush()

    ack = AVAcknowledgment(
        scope_id=scope.id,
        department_id=department.id,
        state="PENDING",
    )
    db.session.add(ack)
    db.session.commit()
    assert ack.id is not None
    assert ack.state == "PENDING"
    assert ack.acknowledged_at is None


def test_av_ack_unique_per_scope_dept(space, department):
    scope = AVScope(
        space_id=space.id, version=1, state="OPEN_FOR_INPUT",
        scope_text="x", authored_by_user_id="u",
    )
    db.session.add(scope)
    db.session.flush()

    db.session.add(AVAcknowledgment(scope_id=scope.id, department_id=department.id, state="PENDING"))
    db.session.commit()
    db.session.add(AVAcknowledgment(scope_id=scope.id, department_id=department.id, state="PENDING"))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_av_scope_incorporated_request(space, work_item):
    scope = AVScope(
        space_id=space.id, version=1, state="LOCKED",
        scope_text="x", authored_by_user_id="u",
    )
    db.session.add(scope)
    db.session.flush()

    link = AVScopeIncorporatedRequest(
        scope_id=scope.id,
        work_item_id=work_item.id,
    )
    db.session.add(link)
    db.session.commit()
    assert link.incorporated_at is not None
