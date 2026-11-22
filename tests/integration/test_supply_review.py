"""
Tests for SUPPLY reviewer templates (Task 12) — line_review.html + quick_review.html.

These templates are consumed by the existing polymorphic review routes
(approvals.line_review / work.quick_review); no new routes are added here.
Harness mirrors tests/integration/test_supply_submit.py's seed_supply
helper, extended with an APPROVER UserRole so a reviewer (not just a
super-admin) can exercise checkout + review decisions.

Test 2 also pins the has_admin_final=True flag combo at the review-decision
boundary: approving the only line must NOT auto-finalize the work item
(app/routes/work/helpers/lifecycle.py:143-144 — try_auto_finalize no-ops
when config.has_admin_final is True). Task 11's tests pinned the same flag
combo at submit time; this pins it at the "last line decided" trigger point
inside apply_review_decision.
"""
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
from tests.integration.supply_helpers import (
    add_line, login, make_draft_order, seed_approval_group, seed_category,
    seed_item, seed_reviewer, seed_supply, set_pickup_details,
    setup_submitted_order,
)



def _use_review_sessions(wt):
    """Turn the lock back on; these tests cover the checkout UI itself."""
    wt.config.uses_review_sessions = True
    db.session.commit()


class TestSupplyLineReviewPage:
    """Test 1: reviewer sees item name, qty, requester notes, AND cost."""

    def test_line_review_shows_item_qty_notes_and_cost(self, app, client, seed_workflow_data):
        wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
            app, client, seed_workflow_data, quantity=3, notes="need for tech booth",
            unit_cost_cents=500,
        )
        _use_review_sessions(wt)

        login(client, "test:reviewer")
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
        wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
            app, client, seed_workflow_data,
        )

        login(client, "test:reviewer")
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
        wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
            app, client, seed_workflow_data, quantity=3,
        )

        # Reviewer kicks the line back for adjustment.
        login(client, "test:reviewer")
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
        login(client, "test:admin")
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
        wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
            app, client, seed_workflow_data, quantity=3, notes="need for tech booth",
        )

        login(client, "test:reviewer")
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


class TestSupplyCheckoutButton:
    """The "Start Reviewing" button must match what the checkout route accepts.

    The old gate keyed off "do I hold the lock", which is false both when the
    item is free and when another reviewer holds it, so the button rendered in
    both cases and the POST refused in one of them.
    """

    def test_no_checkout_button_while_another_reviewer_holds_the_lock(
        self, app, client, seed_workflow_data
    ):
        wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
            app, client, seed_workflow_data,
        )
        _use_review_sessions(wt)
        work_item.checked_out_by_user_id = "test:admin"
        work_item.checked_out_at = datetime.utcnow()
        work_item.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=30)
        db.session.commit()

        login(client, "test:reviewer")
        url = (f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}"
               f"/line/{line.line_number}/review")
        response = client.get(url)

        assert response.status_code == 200
        body = response.data.decode()
        checkout_url = (f"/{cycle.code}/{dept.code}/supply/item/"
                        f"{work_item.public_id}/checkout")
        assert checkout_url not in body
        # The "Under Active Review" banner above already states this; the
        # checkout callout stays hidden rather than repeating it.
        assert "This item is checked out by another reviewer." in body
        assert "Checkout Required" not in body

    def test_the_checkout_form_targets_the_supply_route(
        self, app, client, seed_workflow_data
    ):
        """Guards the slug. The shared macro defaults to budget, so omitting it
        would post a supply order to /budget/."""
        wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
            app, client, seed_workflow_data,
        )
        _use_review_sessions(wt)

        login(client, "test:reviewer")
        url = (f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}"
               f"/line/{line.line_number}/review")
        body = client.get(url).data.decode()

        assert (f"/{cycle.code}/{dept.code}/supply/item/"
                f"{work_item.public_id}/checkout") in body
        assert (f"/{cycle.code}/{dept.code}/budget/item/"
                f"{work_item.public_id}/checkout") not in body
