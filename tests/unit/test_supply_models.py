"""
Tests for Supply-specific models (SupplyCategory, SupplyItem, SupplyOrderLineDetail,
SupplyOrderDetail).

Verifies basic CRUD, relationships, and cascade-delete behavior. Setup is
local to this module so the Supply tests don't depend on (or contaminate)
the BUDGET-shaped seed_workflow_data fixture used elsewhere.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    EventCycle,
    SupplyOrderDetail,
    SupplyOrderLineDetail,
    User,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_PENDING,
)


@pytest.fixture(scope="function")
def supply_seed(app):
    """Minimal Supply fixture: WorkType + config, one approval group, one
    DRAFT work item with one work line, ready for tests to attach
    detail objects to."""
    user = User(
        id="test:supply_user", email="supply@test.local",
        display_name="Supply Tester", is_active=True,
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

    wt = WorkType(code="SUPPLY", name="Supply Orders", is_active=True)
    db.session.add(wt)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="supply",
        public_id_prefix="SUP", line_detail_type="supply",
        routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    )
    db.session.add(wtc)

    supply_group = ApprovalGroup(
        work_type_id=wt.id, code="SUPPLY_DEFAULT",
        name="Supply Approvers", is_active=True,
    )
    db.session.add(supply_group)
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
        public_id="TST2026-TESTDEPT-SUP-1",
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
        "work_type": wt,
        "supply_group": supply_group,
        "work_item": work_item,
        "line": line,
    }


def test_supply_order_detail_round_trip_and_cascade(supply_seed):
    """SupplyOrderDetail attaches to WorkItem and cascades on delete."""
    detail = SupplyOrderDetail(
        work_item_id=supply_seed["work_item"].id,
        pickup_time="Tuesday Evening (after 6 PM)",
        additional_notes=None,
        created_by_user_id="test:supply_user",
    )
    db.session.add(detail)
    db.session.commit()

    item = db.session.query(WorkItem).filter_by(id=supply_seed["work_item"].id).one()
    assert item.supply_order_detail is not None
    assert item.supply_order_detail.pickup_time == "Tuesday Evening (after 6 PM)"

    db.session.delete(item)
    db.session.commit()

    assert db.session.query(SupplyOrderDetail).count() == 0


def test_line_detail_has_no_order_level_columns():
    """Order-level pickup fields live on SupplyOrderDetail, not per line."""
    from app.models import SupplyOrderLineDetail
    assert not hasattr(SupplyOrderLineDetail, "pickup_time")
    assert not hasattr(SupplyOrderLineDetail, "additional_notes")
