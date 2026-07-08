"""
Tests for SUPPLY order submit (Task 11) — the flag-combo verification.

SUPPLY runs uses_dispatch=False + has_admin_final=True, a combination no
live work type exercises (TechOps/AV are uses_dispatch=False +
has_admin_final=False; BUDGET is uses_dispatch=True). These tests pin that
submit_work_item's uses_dispatch=False branch (auto-route + inline
WorkLineReview creation, status -> SUBMITTED) behaves correctly when
has_admin_final=True, and that the cab's own pre-submit validation catches
everything the engine would otherwise silently skip.

Harness mirrors tests/integration/test_supply_routes.py's _seed_supply
helper, extended with SUPPLY-scoped approval groups and a category mapped
to one of them (the routing dependency submit needs).
"""
from app import db
from app.models import (
    ApprovalGroup,
    SupplyCategory,
    SupplyItem,
    SupplyOrderDetail,
    SupplyOrderLineDetail,
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.supply.form_utils import PICKUP_TIME_OPTIONS, PICKUP_TIME_OTHER


def _login(client, user_id):
    """Set the session to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_supply(seed_workflow_data):
    """Add an active SUPPLY work type + config to the seeded data.

    Mirrors test_supply_routes.py's _seed_supply: uses_dispatch=False,
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
        item_singular="Supply Order",
        item_plural="Supply Orders",
        line_singular="Item",
        line_plural="Items",
    )
    db.session.add(config)
    db.session.commit()
    return wt


def _seed_approval_group(wt, code="SUPPLY_GEN"):
    group = ApprovalGroup(
        work_type_id=wt.id, code=code, name=f"{code} Reviewers", is_active=True,
    )
    db.session.add(group)
    db.session.commit()
    return group


def _seed_category(approval_group=None, code="OFFICE"):
    category = SupplyCategory(
        code=code, name=f"{code} Supplies", is_active=True, sort_order=1,
        approval_group_id=approval_group.id if approval_group else None,
    )
    db.session.add(category)
    db.session.commit()
    return category


def _seed_item(category, name="Sharpie Markers", notes_required=False, is_active=True):
    item = SupplyItem(
        category_id=category.id,
        item_name=name,
        unit="each",
        is_active=is_active,
        notes_required=notes_required,
    )
    db.session.add(item)
    db.session.commit()
    return item


def _make_draft_order(wt, cycle, dept):
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
        status=WORK_ITEM_STATUS_DRAFT,
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


def _add_line(work_item, item, quantity=1, notes=None, line_number=None):
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
        requester_notes=notes,
    ))
    db.session.commit()
    return line


def _set_pickup_details(work_item, pickup_time=PICKUP_TIME_OPTIONS[0], notes=None):
    detail = work_item.supply_order_detail
    detail.pickup_time = pickup_time
    detail.additional_notes = notes
    db.session.commit()


class TestSupplySubmitAutoRoutes:
    """The flag-combo verification: uses_dispatch=False + has_admin_final=True
    must auto-route + go straight to SUBMITTED (not AWAITING_DISPATCH)."""

    def test_submit_auto_routes_and_goes_submitted(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category)

        work_item = _make_draft_order(wt, cycle, dept)
        line = _add_line(work_item, item, quantity=2, notes="for tech booth")
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/submit"
        )

        assert response.status_code == 302

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_SUBMITTED

        reviews = WorkLineReview.query.filter_by(work_line_id=line.id).all()
        assert len(reviews) == 1
        review = reviews[0]
        assert review.stage == REVIEW_STAGE_APPROVAL_GROUP
        assert review.status == REVIEW_STATUS_PENDING
        assert review.approval_group_id == group.id

        detail = SupplyOrderLineDetail.query.filter_by(work_line_id=line.id).first()
        assert detail.routed_approval_group_id == group.id


class TestSupplySubmitValidationBlocks:
    """Each failure mode must block submit (still DRAFT) and flash a loud
    error naming the offending line/item — engine would otherwise silently
    skip unroutable lines."""

    def _submit(self, client, cycle, dept, work_item):
        return client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/submit"
        )

    def test_no_lines_blocks_submit(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        response = self._submit(client, cycle, dept, work_item)

        assert response.status_code == 302
        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT

    def test_missing_pickup_time_blocks_submit(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category)
        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item)
        # No pickup details set — pickup_time is None.

        _login(client, "test:admin")
        response = self._submit(client, cycle, dept, work_item)

        assert response.status_code == 302
        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT

    def test_notes_required_blank_blocks_submit(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, name="Special Order Widget", notes_required=True)
        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item, notes=None)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        response = self._submit(client, cycle, dept, work_item)

        assert response.status_code == 302
        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT

    def test_unroutable_category_blocks_submit(self, app, client, seed_workflow_data):
        """Category has approval_group_id=None -> engine would silently
        skip this line's routing; validation must catch it first."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        category = _seed_category(approval_group=None)
        item = _seed_item(category)
        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        response = self._submit(client, cycle, dept, work_item)

        assert response.status_code == 302
        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT
        # No review rows should have been created for the unroutable line.
        assert WorkLineReview.query.count() == 0

    def test_other_pickup_without_notes_blocks_submit(self, app, client, seed_workflow_data):
        """'Other' pickup requires the preferred date/time in notes."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category)
        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item)
        _set_pickup_details(work_item, pickup_time=PICKUP_TIME_OTHER, notes=None)

        _login(client, "test:admin")
        response = self._submit(client, cycle, dept, work_item)

        assert response.status_code == 302
        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT

    def test_other_pickup_with_notes_submits(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category)
        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item, notes="for tech booth")
        _set_pickup_details(
            work_item, pickup_time=PICKUP_TIME_OTHER,
            notes="Friday 10 AM if possible",
        )

        _login(client, "test:admin")
        response = self._submit(client, cycle, dept, work_item)

        assert response.status_code == 302
        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_SUBMITTED


class TestSupplySubmitDeactivatedItem:
    def test_deactivated_item_blocks_submit_and_names_item(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, name="Discontinued Gizmo")
        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item)
        _set_pickup_details(work_item)

        item.is_active = False
        db.session.commit()

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/submit",
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"Discontinued Gizmo" in response.data

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT
