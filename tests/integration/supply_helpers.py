"""Seeding helpers shared by the Supply integration tests."""
from datetime import datetime, timedelta

from app import db
from app.models import (
    ApprovalGroup,
    SupplyCategory,
    SupplyItem,
    SupplyOrderDetail,
    SupplyOrderLineDetail,
    UserRole,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROLE_APPROVER,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.supply.form_utils import PICKUP_TIME_OPTIONS


def login(client, user_id):
    """Set the session to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def seed_supply(seed_workflow_data):
    """Add an active SUPPLY work type + config to the seeded data.

    Mirrors test_supply_submit.py's seed_supply: uses_dispatch=False,
    has_admin_final=True is the exact flag combo under test here.
    """
    wt = WorkType(code="SUPPLY", name="Supply Orders", is_active=True)
    db.session.add(wt)
    db.session.flush()
    config = WorkTypeConfig(
        work_type_id=wt.id,
        url_slug="supply",
        public_id_prefix="SUP",
        line_detail_type="supply",
        routing_strategy=ROUTING_STRATEGY_CATEGORY,
        supports_supplementary=False,
        supports_fixed_costs=False,
        uses_dispatch=False,
        has_admin_final=True,
        uses_review_sessions=False,
        item_singular="Supply Order",
        item_plural="Supply Orders",
        line_singular="Item",
        line_plural="Items",
    )
    db.session.add(config)
    db.session.commit()
    return wt


def seed_approval_group(wt, code="SUPPLY_GEN"):
    group = ApprovalGroup(
        work_type_id=wt.id, code=code, name=f"{code} Reviewers", is_active=True,
    )
    db.session.add(group)
    db.session.commit()
    return group


def seed_category(approval_group=None, code="OFFICE"):
    category = SupplyCategory(
        code=code, name=f"{code} Supplies", is_active=True, sort_order=1,
        approval_group_id=approval_group.id if approval_group else None,
    )
    db.session.add(category)
    db.session.commit()
    return category


def seed_item(category, name="Sharpie Markers", notes_required=False, is_active=True,
                unit_cost_cents=None):
    item = SupplyItem(
        category_id=category.id,
        item_name=name,
        unit="each",
        is_active=is_active,
        notes_required=notes_required,
        unit_cost_cents=unit_cost_cents,
    )
    db.session.add(item)
    db.session.commit()
    return item


def make_draft_order(wt, cycle, dept):
    portfolio = WorkPortfolio(
        work_type_id=wt.id,
        event_cycle_id=cycle.id,
        department_id=dept.id,
        created_by_user_id="test:admin",
    )
    db.session.add(portfolio)
    db.session.flush()
    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status="DRAFT",
        public_id="TST2026-TESTDEPT-SUP-1",
        created_by_user_id="test:admin",
    )
    db.session.add(work_item)
    db.session.flush()
    db.session.add(SupplyOrderDetail(
        work_item_id=work_item.id,
        created_by_user_id="test:admin",
    ))
    db.session.commit()
    return work_item


def add_line(work_item, item, quantity=1, notes=None, line_number=None):
    if line_number is None:
        line_number = 1 + max((l.line_number for l in work_item.lines), default=0)
    line = WorkLine(
        work_item_id=work_item.id,
        line_number=line_number,
    )
    db.session.add(line)
    db.session.flush()
    db.session.add(SupplyOrderLineDetail(
        work_line_id=line.id,
        item_id=item.id,
        quantity_requested=quantity,
        # Submit requires a confidence on every line; these tests are not about it.
        quantity_confidence="KNOWN",
        requester_notes=notes,
    ))
    db.session.commit()
    return line


def set_pickup_details(work_item, pickup_time=PICKUP_TIME_OPTIONS[0], notes=None):
    detail = work_item.supply_order_detail
    detail.pickup_time = pickup_time
    detail.additional_notes = notes
    db.session.commit()


def seed_reviewer(seed_workflow_data, group):
    """Grant the shared test:reviewer user an APPROVER role for `group`."""
    reviewer = seed_workflow_data["reviewer"]
    db.session.add(UserRole(
        user_id=reviewer.id, role_code=ROLE_APPROVER, approval_group_id=group.id,
    ))
    db.session.commit()
    return reviewer


def setup_submitted_order(app, client, seed_workflow_data, quantity=2, notes="for tech booth",
                            unit_cost_cents=500):
    """Build a submitted supply order with one line, routed + reviewed.

    Returns (wt, cycle, dept, group, item, work_item, line).
    """
    wt = seed_supply(seed_workflow_data)
    cycle = seed_workflow_data["cycle"]
    dept = seed_workflow_data["department"]
    group = seed_approval_group(wt)
    category = seed_category(approval_group=group)
    item = seed_item(category, unit_cost_cents=unit_cost_cents)

    work_item = make_draft_order(wt, cycle, dept)
    line = add_line(work_item, item, quantity=quantity, notes=notes)
    set_pickup_details(work_item)

    seed_reviewer(seed_workflow_data, group)

    login(client, "test:admin")
    response = client.post(
        f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/submit"
    )
    assert response.status_code == 302

    db.session.refresh(work_item)
    assert work_item.status == WORK_ITEM_STATUS_SUBMITTED

    return wt, cycle, dept, group, item, work_item, line
