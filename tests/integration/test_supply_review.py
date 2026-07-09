"""
Tests for SUPPLY reviewer templates (Task 12) — line_review.html + quick_review.html.

These templates are consumed by the existing polymorphic review routes
(approvals.line_review / work.quick_review); no new routes are added here.
Harness mirrors tests/integration/test_supply_submit.py's _seed_supply
helper, extended with an APPROVER UserRole so a reviewer (not just a
super-admin) can exercise checkout + review decisions.

Test 2 also pins the has_admin_final=True flag combo at the review-decision
boundary: approving the only line must NOT auto-finalize the work item
(app/routes/work/helpers/lifecycle.py:143-144 — try_auto_finalize no-ops
when config.has_admin_final is True). Task 11's tests pinned the same flag
combo at submit time; this pins it at the "last line decided" trigger point
inside apply_review_decision.
"""
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
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_PENDING,
    ROLE_APPROVER,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.supply.form_utils import PICKUP_TIME_OPTIONS


def _login(client, user_id):
    """Set the session to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_supply(seed_workflow_data):
    """Add an active SUPPLY work type + config to the seeded data.

    Mirrors test_supply_submit.py's _seed_supply: uses_dispatch=False,
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


def _seed_item(category, name="Sharpie Markers", notes_required=False, is_active=True,
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


def _seed_reviewer(seed_workflow_data, group):
    """Grant the shared test:reviewer user an APPROVER role for `group`."""
    reviewer = seed_workflow_data["reviewer"]
    db.session.add(UserRole(
        user_id=reviewer.id, role_code=ROLE_APPROVER, approval_group_id=group.id,
    ))
    db.session.commit()
    return reviewer


def _setup_submitted_order(app, client, seed_workflow_data, quantity=2, notes="for tech booth",
                            unit_cost_cents=500):
    """Build a submitted supply order with one line, routed + reviewed.

    Returns (wt, cycle, dept, group, item, work_item, line).
    """
    wt = _seed_supply(seed_workflow_data)
    cycle = seed_workflow_data["cycle"]
    dept = seed_workflow_data["department"]
    group = _seed_approval_group(wt)
    category = _seed_category(approval_group=group)
    item = _seed_item(category, unit_cost_cents=unit_cost_cents)

    work_item = _make_draft_order(wt, cycle, dept)
    line = _add_line(work_item, item, quantity=quantity, notes=notes)
    _set_pickup_details(work_item)

    _seed_reviewer(seed_workflow_data, group)

    _login(client, "test:admin")
    response = client.post(
        f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/submit"
    )
    assert response.status_code == 302

    db.session.refresh(work_item)
    assert work_item.status == WORK_ITEM_STATUS_SUBMITTED

    return wt, cycle, dept, group, item, work_item, line


class TestSupplyLineReviewPage:
    """Test 1: reviewer sees item name, qty, requester notes, AND cost."""

    def test_line_review_shows_item_qty_notes_and_cost(self, app, client, seed_workflow_data):
        wt, cycle, dept, group, item, work_item, line = _setup_submitted_order(
            app, client, seed_workflow_data, quantity=3, notes="need for tech booth",
            unit_cost_cents=500,
        )

        _login(client, "test:reviewer")
        checkout_resp = client.post(
            f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/checkout"
        )
        assert checkout_resp.status_code == 302

        db.session.refresh(work_item)
        assert work_item.checked_out_by_user_id == "test:reviewer"

        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/line/{line.line_number}/review"
        )

        assert response.status_code == 200
        body = response.data.decode()
        assert item.item_name in body
        assert "3" in body  # qty requested
        assert "need for tech booth" in body
        # Reviewers see cost: unit cost $5.00 and line total $15.00 (3 * $5.00)
        assert "$5.00" in body
        assert "$15.00" in body


class TestSupplyLineApprove:
    """Test 2: approve the only line -> APPROVED, work_item stays SUBMITTED
    because has_admin_final=True must block try_auto_finalize's no-dispatch
    auto-finalize path (lifecycle.py:143-144)."""

    def test_approve_does_not_auto_finalize(self, app, client, seed_workflow_data):
        wt, cycle, dept, group, item, work_item, line = _setup_submitted_order(
            app, client, seed_workflow_data,
        )

        _login(client, "test:reviewer")
        client.post(f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/checkout")

        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/line/{line.line_number}/approve"
        )
        assert response.status_code == 302

        review = WorkLineReview.query.filter_by(
            work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        ).one()
        assert review.status == REVIEW_STATUS_APPROVED

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_SUBMITTED


class TestSupplyNeedsAdjustmentKickback:
    """Test 3: NEEDS_ADJUSTMENT kickback -> requester updates qty via
    work.supply_line_update -> responds via the shared line_respond flow ->
    review back to PENDING."""

    def test_needs_adjustment_then_requester_response(self, app, client, seed_workflow_data):
        wt, cycle, dept, group, item, work_item, line = _setup_submitted_order(
            app, client, seed_workflow_data, quantity=3,
        )

        # Reviewer kicks the line back for adjustment.
        _login(client, "test:reviewer")
        client.post(f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/checkout")

        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/line/{line.line_number}/needs-adjustment",
            data={"note": "Please reduce quantity to 2 — budget constraint."},
        )
        assert response.status_code == 302

        review = WorkLineReview.query.filter_by(
            work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        ).one()
        assert review.status == REVIEW_STATUS_NEEDS_ADJUSTMENT

        db.session.refresh(line)
        assert line.needs_requester_action is True

        # Requester (super-admin here) updates the quantity via Task 10's route.
        _login(client, "test:admin")
        update_resp = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/lines/{line.line_number}/update",
            data={"quantity": "2", "notes": "reduced per reviewer request"},
        )
        assert update_resp.status_code == 302

        detail = SupplyOrderLineDetail.query.filter_by(work_line_id=line.id).one()
        assert detail.quantity_requested == 2

        # Requester responds through the shared respond flow.
        respond_resp = client.post(
            f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/line/{line.line_number}/respond",
            data={"response": "Reduced quantity to 2 as requested."},
        )
        assert respond_resp.status_code == 302

        db.session.refresh(review)
        assert review.status == REVIEW_STATUS_PENDING

        db.session.refresh(line)
        assert line.needs_requester_action is False


class TestSupplyQueueTableRendersItemAndQty:
    """approvals/_queue_table.html's generic branch reads detail.description,
    which SupplyOrderLineDetail doesn't have — it renders '-' for every
    supply line. The dashboard's Kicked Back section (queues.kicked_back)
    goes through this same partial, so a kicked-back supply line is the
    cheapest way to exercise the SUPPLY-specific branch end to end."""

    def test_kicked_back_supply_line_shows_item_name_and_qty(
        self, app, client, seed_workflow_data
    ):
        wt, cycle, dept, group, item, work_item, line = _setup_submitted_order(
            app, client, seed_workflow_data, quantity=3, notes="need for tech booth",
        )

        _login(client, "test:reviewer")
        client.post(f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/checkout")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/line/{line.line_number}/needs-adjustment",
            data={"note": "Please reduce quantity to 2 — budget constraint."},
        )
        assert response.status_code == 302

        dashboard_resp = client.get(f"/approvals/{group.code}")
        assert dashboard_resp.status_code == 200
        body = dashboard_resp.data.decode()
        assert item.item_name in body
        assert "&times;3" in body
        assert "need for tech booth" in body
