"""Unit tests for AV status timeline helper."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (
    ActivityEvent,
    ApprovalGroup,
    Department,
    EventCycle,
    User,
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROUTING_STRATEGY_DIRECT,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.models.av import AVRequestPlan, AVScope, AVScopeIncorporatedRequest
from app.routes.work.av.timeline import build_status_timeline, TimelineEvent


# ---------------------------------------------------------------------------
# Local seed fixture (mirrors test_av_models.py av_seed pattern)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def av_seed(app):
    """Minimal AV fixture: one DRAFT WorkItem with one WorkLine."""
    user = User(
        id="test:av_tl_user", email="av_tl@test.local",
        display_name="AV Timeline Tester", is_active=True,
    )
    db.session.add(user)

    cycle = EventCycle(
        code="TL2026", name="Timeline Test Event 2026",
        is_active=True, is_default=False, sort_order=99,
    )
    dept = Department(
        code="TLDEPT", name="Timeline Test Department", is_active=True,
    )
    db.session.add_all([cycle, dept])

    wt = WorkType(code="AV_TL", name="AV Timeline Requests", is_active=True)
    db.session.add(wt)
    db.session.flush()

    av_group = ApprovalGroup(
        work_type_id=wt.id, code="AV_TL_TEAM",
        name="AV Timeline Team", is_active=True,
    )
    db.session.add(av_group)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="av_tl",
        public_id_prefix="AVTL", line_detail_type="av",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
        default_approval_group_id=av_group.id,
        uses_dispatch=True, has_admin_final=True,
    )
    db.session.add(wtc)

    portfolio = WorkPortfolio(
        work_type_id=wt.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=user.id,
    )
    db.session.add(portfolio)
    db.session.flush()

    t0 = datetime(2026, 5, 1, 10, 0, 0)
    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TL2026-TLDEPT-AVTL-1",
        created_by_user_id=user.id,
        created_at=t0,
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
        "work_item": work_item,
        "line": line,
        "t0": t0,
    }


# ---------------------------------------------------------------------------
# Convenience fixture aliases used in the tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def av_draft_request(av_seed):
    """A plain DRAFT WorkItem with no additional events."""
    return av_seed["work_item"]


@pytest.fixture(scope="function")
def av_submitted_request_with_activity(av_seed):
    """DRAFT→SUBMITTED WorkItem with a matching AV_REQUEST_SUBMITTED ActivityEvent."""
    work_item = av_seed["work_item"]
    t0 = av_seed["t0"]
    wt = av_seed["work_type"]

    t_submit = t0 + timedelta(hours=1)
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    work_item.submitted_at = t_submit
    work_item.submitted_by_user_id = av_seed["user"].id

    ae = ActivityEvent(
        event_type="AV_REQUEST_SUBMITTED",
        work_item_id=work_item.id,
        work_type_id=wt.id,
        actor_user_id=av_seed["user"].id,
        occurred_at=t_submit,
    )
    db.session.add(ae)
    db.session.commit()
    return work_item


@pytest.fixture(scope="function")
def av_logged_request_with_plans(av_seed):
    """WorkItem with two AVRequestPlan rows (rev 1 and rev 2)."""
    work_item = av_seed["work_item"]
    t0 = av_seed["t0"]

    plan1 = AVRequestPlan(
        work_item_id=work_item.id,
        revision=1,
        gear_spec="2x wireless lavs",
        authored_by_user_id=av_seed["user"].id,
        created_at=t0 + timedelta(days=1),
    )
    plan2 = AVRequestPlan(
        work_item_id=work_item.id,
        revision=2,
        gear_spec="2x wireless lavs + confidence monitor",
        authored_by_user_id=av_seed["user"].id,
        created_at=t0 + timedelta(days=2),
    )
    db.session.add_all([plan1, plan2])
    db.session.commit()
    return work_item


@pytest.fixture(scope="function")
def av_incorporated_request(av_seed):
    """WorkItem incorporated into a locked AVScope."""
    from app.models.space import Space

    work_item = av_seed["work_item"]
    cycle = av_seed["cycle"]
    user = av_seed["user"]
    t0 = av_seed["t0"]

    space = Space(
        event_cycle_id=cycle.id,
        code="TL_ROOM1",
        name="Timeline Room 1",
        location="Building A",
        is_active=True,
        created_by_user_id=user.id,
    )
    db.session.add(space)
    db.session.flush()

    t_locked = t0 + timedelta(days=5)
    scope = AVScope(
        space_id=space.id,
        version=1,
        state="LOCKED",
        scope_text="Final scope",
        authored_by_user_id=user.id,
        locked_at=t_locked,
        locked_by_user_id=user.id,
    )
    db.session.add(scope)
    db.session.flush()

    inc = AVScopeIncorporatedRequest(
        scope_id=scope.id,
        work_item_id=work_item.id,
        incorporated_at=t_locked,
    )
    db.session.add(inc)
    db.session.commit()
    return work_item


@pytest.fixture(scope="function")
def av_request_with_full_lifecycle(av_seed):
    """Created → Submitted → Plan rev 1 → Plan rev 2 → Incorporated, all with distinct timestamps."""
    from app.models.space import Space

    work_item = av_seed["work_item"]
    wt = av_seed["work_type"]
    cycle = av_seed["cycle"]
    user = av_seed["user"]
    t0 = av_seed["t0"]

    # Submitted activity
    t_submit = t0 + timedelta(hours=2)
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    work_item.submitted_at = t_submit
    work_item.submitted_by_user_id = user.id

    ae = ActivityEvent(
        event_type="AV_REQUEST_SUBMITTED",
        work_item_id=work_item.id,
        work_type_id=wt.id,
        actor_user_id=user.id,
        occurred_at=t_submit,
    )
    db.session.add(ae)

    # Plan rev 1
    plan1 = AVRequestPlan(
        work_item_id=work_item.id,
        revision=1,
        gear_spec="Initial spec",
        authored_by_user_id=user.id,
        created_at=t0 + timedelta(days=1),
    )
    # Plan rev 2
    plan2 = AVRequestPlan(
        work_item_id=work_item.id,
        revision=2,
        gear_spec="Updated spec",
        authored_by_user_id=user.id,
        created_at=t0 + timedelta(days=2),
    )
    db.session.add_all([plan1, plan2])

    # Space + scope + incorporation
    space = Space(
        event_cycle_id=cycle.id,
        code="TL_ROOM_FULL",
        name="Full Lifecycle Room",
        location="Building B",
        is_active=True,
        created_by_user_id=user.id,
    )
    db.session.add(space)
    db.session.flush()

    t_locked = t0 + timedelta(days=7)
    scope = AVScope(
        space_id=space.id,
        version=1,
        state="LOCKED",
        scope_text="Final",
        authored_by_user_id=user.id,
        locked_at=t_locked,
        locked_by_user_id=user.id,
    )
    db.session.add(scope)
    db.session.flush()

    inc = AVScopeIncorporatedRequest(
        scope_id=scope.id,
        work_item_id=work_item.id,
        incorporated_at=t_locked,
    )
    db.session.add(inc)
    db.session.commit()
    return work_item


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_timeline_for_draft_only(av_draft_request):
    """A bare DRAFT request yields exactly one 'Created' event."""
    events = build_status_timeline(av_draft_request)
    assert len(events) == 1
    assert events[0].label == "Created"
    assert events[0].kind == "created"


def test_timeline_for_submitted(av_submitted_request_with_activity):
    """SUBMITTED requests have at least Created + Submitted."""
    events = build_status_timeline(av_submitted_request_with_activity)
    labels = [e.label for e in events]
    assert "Created" in labels
    assert "Submitted" in labels


def test_timeline_for_logged_with_two_plans(av_logged_request_with_plans):
    """Each plan revision appears as a 'Logged · rev N' event."""
    events = build_status_timeline(av_logged_request_with_plans)
    labels = [e.label for e in events]
    assert any("Logged · rev 1" in l for l in labels)
    assert any("Logged · rev 2" in l for l in labels)


def test_timeline_for_incorporated(av_incorporated_request):
    """When incorporated into a locked scope, label includes space name and version."""
    events = build_status_timeline(av_incorporated_request)
    labels = [e.label for e in events]
    assert any("Incorporated into" in l for l in labels)
    # Verify the full label format
    inc_label = next(l for l in labels if "Incorporated into" in l)
    assert "Timeline Room 1" in inc_label
    assert "v1" in inc_label


def test_timeline_chronological_order(av_request_with_full_lifecycle):
    """Events appear in chronological order."""
    events = build_status_timeline(av_request_with_full_lifecycle)
    timestamps = [e.timestamp for e in events]
    assert timestamps == sorted(timestamps)


def test_timeline_returns_list_of_timeline_events(av_draft_request):
    """Return type is a list of TimelineEvent namedtuples."""
    events = build_status_timeline(av_draft_request)
    assert isinstance(events, list)
    for e in events:
        assert isinstance(e, TimelineEvent)
        assert hasattr(e, "timestamp")
        assert hasattr(e, "label")
        assert hasattr(e, "actor_user_id")
        assert hasattr(e, "kind")


def test_timeline_kickback_review(av_seed):
    """A NEEDS_INFO review on the work line produces a kickback timeline event."""
    work_item = av_seed["work_item"]
    line = av_seed["line"]
    av_group = av_seed["av_group"]
    t0 = av_seed["t0"]

    t_kickback = t0 + timedelta(hours=3)
    review = WorkLineReview(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=av_group.id,
        status=REVIEW_STATUS_NEEDS_INFO,
        decided_at=t_kickback,
        decided_by_user_id=av_seed["user"].id,
        created_by_user_id=av_seed["user"].id,
    )
    db.session.add(review)
    db.session.commit()

    events = build_status_timeline(work_item)
    labels = [e.label for e in events]
    assert "AV requested more info" in labels


def test_timeline_rejected_review(av_seed):
    """A REJECTED review on the work line produces a rejection timeline event."""
    work_item = av_seed["work_item"]
    line = av_seed["line"]
    av_group = av_seed["av_group"]
    t0 = av_seed["t0"]

    t_rejected = t0 + timedelta(hours=4)
    review = WorkLineReview(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=av_group.id,
        status=REVIEW_STATUS_REJECTED,
        decided_at=t_rejected,
        decided_by_user_id=av_seed["user"].id,
        created_by_user_id=av_seed["user"].id,
    )
    db.session.add(review)
    db.session.commit()

    events = build_status_timeline(work_item)
    labels = [e.label for e in events]
    assert "AV rejected the request" in labels
