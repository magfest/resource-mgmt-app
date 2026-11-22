"""
Tests for SUPPLY admin-final (Task 13) — the FestOps queue + finalize flow.

SUPPLY runs uses_dispatch=False + has_admin_final=True, so try_auto_finalize
never completes an order (pinned by test_supply_review). The terminal stage is
this admin-final flow: a SUPPLY worktype admin (or super-admin) sets the
authoritative approved quantity per line and finalizes the order.

Harness mirrors tests/integration/test_supply_review.py's _seed_* helpers.
The three tests pin:
  1. finalize writes quantities/statuses + an ADMIN_FINAL review row per line,
     and flips the order to FINALIZED;
  2. overriding a review-group decision (zeroing an approved line) WITHOUT a
     note is rejected (200 + flash, order still SUBMITTED, nothing written);
  3. a line still PENDING at APPROVAL_GROUP auto-approves at its requested
     quantity when finalized (BUDGET auto-approve semantics = the qty default).
"""
from datetime import datetime, timedelta, timezone

from app import db
from app.models import (
    ApprovalGroup,
    Department,
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
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_REJECTED,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_REJECTED,
)
from app.routes.work.supply.form_utils import PICKUP_TIME_OPTIONS


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_supply(seed_workflow_data):
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
               unit_cost_cents=500):
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


def _make_draft_order(wt, cycle, dept, public_id="TST2026-TESTDEPT-SUP-1"):
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
        public_id=public_id,
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
        # Submit requires a confidence on every line; these tests are not about it.
        quantity_confidence="KNOWN",
        requester_notes=notes,
    ))
    db.session.commit()
    return line


def _set_pickup_details(work_item, pickup_time=PICKUP_TIME_OPTIONS[0], notes=None):
    detail = work_item.supply_order_detail
    detail.pickup_time = pickup_time
    detail.additional_notes = notes
    db.session.commit()


def _submit(client, cycle, dept, work_item):
    resp = client.post(
        f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/submit"
    )
    assert resp.status_code == 302
    db.session.refresh(work_item)
    assert work_item.status == WORK_ITEM_STATUS_SUBMITTED
    return resp


def _approve_ag_review(line):
    """Set the line's APPROVAL_GROUP review to APPROVED (skips the reviewer UI)."""
    review = WorkLineReview.query.filter_by(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
    ).one()
    review.status = REVIEW_STATUS_APPROVED
    db.session.commit()
    return review


class TestSupplyFinalizeWritesQuantities:
    """finalize writes authoritative quantities + statuses + ADMIN_FINAL rows,
    and flips the order to FINALIZED."""

    def test_finalize_writes_quantities_and_status(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        line1 = _add_line(work_item, item, quantity=5, notes="keep", line_number=1)
        line2 = _add_line(work_item, item, quantity=5, notes="drop", line_number=2)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)

        _approve_ag_review(line1)
        _approve_ag_review(line2)

        resp = client.post(
            f"/admin/supply/order/{work_item.public_id}/finalize",
            data={
                "approved_qty_1": "5",
                "note_1": "",
                # Zeroing an approved line = override -> note required (supplied).
                "approved_qty_2": "0",
                "note_2": "Not needed this cycle.",
            },
        )
        assert resp.status_code == 302

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_FINALIZED
        assert work_item.finalized_by_user_id == "test:admin"

        d1 = SupplyOrderLineDetail.query.filter_by(work_line_id=line1.id).one()
        d2 = SupplyOrderLineDetail.query.filter_by(work_line_id=line2.id).one()
        assert d1.quantity_approved == 5
        assert d2.quantity_approved == 0

        db.session.refresh(line1)
        db.session.refresh(line2)
        assert line1.status == WORK_LINE_STATUS_APPROVED
        assert line2.status == WORK_LINE_STATUS_REJECTED

        for line in (line1, line2):
            admin_review = WorkLineReview.query.filter_by(
                work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL,
            ).one()
            assert admin_review.approval_group_id is None
        af1 = WorkLineReview.query.filter_by(
            work_line_id=line1.id, stage=REVIEW_STAGE_ADMIN_FINAL,
        ).one()
        af2 = WorkLineReview.query.filter_by(
            work_line_id=line2.id, stage=REVIEW_STAGE_ADMIN_FINAL,
        ).one()
        assert af1.status == REVIEW_STATUS_APPROVED
        assert af2.status == REVIEW_STATUS_REJECTED
        # unit_cost 500, qty 5 -> 2500 cents
        assert af1.approved_amount_cents == 2500


class TestSupplyFinalizeOverrideNeedsNote:
    """Overriding a review-group decision (zeroing an approved line) without a
    note must be rejected: 200 + flash, order still SUBMITTED, nothing written."""

    def test_override_without_note_rejected(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        line1 = _add_line(work_item, item, quantity=4, line_number=1)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)
        _approve_ag_review(line1)

        resp = client.post(
            f"/admin/supply/order/{work_item.public_id}/finalize",
            data={"approved_qty_1": "0", "note_1": ""},
        )
        # Re-renders the finalize screen (no redirect) with a flash error.
        assert resp.status_code == 200

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_SUBMITTED

        # Nothing written: no ADMIN_FINAL review, no approved quantity.
        assert WorkLineReview.query.filter_by(
            stage=REVIEW_STAGE_ADMIN_FINAL,
        ).count() == 0
        d1 = SupplyOrderLineDetail.query.filter_by(work_line_id=line1.id).one()
        assert d1.quantity_approved is None


class TestSupplyFinalizeAutoApprovesPending:
    """A line still PENDING at APPROVAL_GROUP auto-approves at its requested
    quantity when finalized (the qty default carries the BUDGET semantics)."""

    def test_finalize_auto_approves_pending(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        line1 = _add_line(work_item, item, quantity=7, line_number=1)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)
        # NOTE: line1's APPROVAL_GROUP review is left PENDING (no reviewer decision).

        resp = client.post(
            f"/admin/supply/order/{work_item.public_id}/finalize",
            data={"approved_qty_1": "7", "note_1": ""},
        )
        assert resp.status_code == 302

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_FINALIZED

        d1 = SupplyOrderLineDetail.query.filter_by(work_line_id=line1.id).one()
        assert d1.quantity_approved == 7

        db.session.refresh(line1)
        assert line1.status == WORK_LINE_STATUS_APPROVED

        af1 = WorkLineReview.query.filter_by(
            work_line_id=line1.id, stage=REVIEW_STAGE_ADMIN_FINAL,
        ).one()
        assert af1.status == REVIEW_STATUS_APPROVED


class TestSupplyFinalizeBlocksOnKickback:
    """BUDGET semantics: finalize hard-blocks while any line is kicked back
    (NEEDS_INFO/NEEDS_ADJUSTMENT awaiting requester response) — mirrors
    can_finalize_work_item's 'Line N is awaiting requester response.' guard
    (admin_final/helpers.py:265-267)."""

    def test_finalize_blocked_by_kicked_back_line(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        line1 = _add_line(work_item, item, quantity=5, line_number=1)
        line2 = _add_line(work_item, item, quantity=3, line_number=2)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)

        _approve_ag_review(line1)
        # Kick line 2 back (mirror what apply_review_decision writes).
        review2 = WorkLineReview.query.filter_by(
            work_line_id=line2.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        ).one()
        review2.status = REVIEW_STATUS_NEEDS_ADJUSTMENT
        line2.needs_requester_action = True
        db.session.commit()

        resp = client.post(
            f"/admin/supply/order/{work_item.public_id}/finalize",
            data={
                "approved_qty_1": "5", "note_1": "",
                "approved_qty_2": "3", "note_2": "",
            },
        )
        # Write-free re-render, error naming the kicked-back line.
        assert resp.status_code == 200
        assert b"Line 2 is awaiting requester response" in resp.data

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_SUBMITTED

        assert WorkLineReview.query.filter_by(
            stage=REVIEW_STAGE_ADMIN_FINAL,
        ).count() == 0
        for line in (line1, line2):
            detail = SupplyOrderLineDetail.query.filter_by(work_line_id=line.id).one()
            assert detail.quantity_approved is None


class TestSupplyAllOrdersAdminView:
    """Cross-department admin view of every SUPPLY order (Task 14) —
    mirrors techops_all_requests but with an item-mix summary instead of
    a service-mix summary, and no monetary column."""

    def test_supply_admin_gets_all_orders_200_with_public_id(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item, quantity=3, line_number=1)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)

        resp = client.get("/admin/supply/orders/")
        assert resp.status_code == 200
        assert work_item.public_id.encode() in resp.data
        # Rows are dicts: a renamed key would render as a silent "-" (Jinja
        # Undefined is falsy), so pin the real pickup string.
        assert PICKUP_TIME_OPTIONS[0].encode() in resp.data

    def test_supply_all_orders_forbidden_for_non_admin(self, app, client, seed_workflow_data):
        _seed_supply(seed_workflow_data)

        _login(client, "test:reviewer")
        resp = client.get("/admin/supply/orders/")
        assert resp.status_code == 403


class TestSupplyAdminQueueView:
    """GET /admin/supply/queue/ — the FestOps finalize queue table."""

    def test_queue_renders_pickup_time_for_submitted_order(
        self, app, client, seed_workflow_data
    ):
        """Rows are dicts: a renamed key would render as a silent "-" (Jinja
        Undefined is falsy), so pin the real pickup string in the response."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item, quantity=3, line_number=1)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)

        resp = client.get("/admin/supply/queue/")
        assert resp.status_code == 200
        assert work_item.public_id.encode() in resp.data
        assert PICKUP_TIME_OPTIONS[0].encode() in resp.data


class TestBuildAdminQueuesIsBudgetScoped:
    """admin_final is BUDGET-scoped in practice (CLAUDE.md): SUPPLY also has
    WorkTypeConfig.has_admin_final=True, but it finalizes through its own
    admin surface (supply_admin_finalize), not BUDGET's admin_final.finalize
    route. A SUBMITTED supply order must not surface in build_admin_queues'
    ready_for_review queue, and BUDGET's finalize route must 404 rather than
    finalize it (which would leave quantity_approved unset)."""

    def test_submitted_supply_order_absent_from_build_admin_queues(
        self, app, client, seed_workflow_data
    ):
        from app.routes.admin_final.helpers import build_admin_queues

        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        line1 = _add_line(work_item, item, quantity=3, line_number=1)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)
        _approve_ag_review(line1)

        queues = build_admin_queues()
        ready_ids = {i.work_item.id for i in queues.ready_for_review}
        assert work_item.id not in ready_ids

    def test_budget_finalize_route_404s_for_supply_order(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        _add_line(work_item, item, quantity=3, line_number=1)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)

        resp = client.post(f"/admin/final-review/finalize/{work_item.id}")
        assert resp.status_code == 404

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_SUBMITTED

    def test_budget_per_line_admin_approve_404s_for_supply_line(
        self, app, client, seed_workflow_data
    ):
        """The per-line admin-final routes (admin_final/reviews.py) guard via
        require_budget_work_type (reviews.py:40 -> context.py:352), so a
        hand-crafted /<event>/<dept>/supply/.../admin-approve URL must 404
        without writing an ADMIN_FINAL review or touching the line."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)

        work_item = _make_draft_order(wt, cycle, dept)
        line1 = _add_line(work_item, item, quantity=3, line_number=1)
        _set_pickup_details(work_item)

        _login(client, "test:admin")
        _submit(client, cycle, dept, work_item)
        _approve_ag_review(line1)

        resp = client.post(
            f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}"
            f"/line/1/admin-approve"
        )
        assert resp.status_code == 404

        assert WorkLineReview.query.filter_by(
            stage=REVIEW_STAGE_ADMIN_FINAL,
        ).count() == 0
        d1 = SupplyOrderLineDetail.query.filter_by(work_line_id=line1.id).one()
        assert d1.quantity_approved is None


class TestSupplyAdminHome:
    """Supply Admin Home landing page (nav/admin-surface revision, Part B) —
    mirrors admin_final.budget_admin_home's access pattern: 200 for a SUPPLY
    admin (super-admins qualify automatically via _require_supply_admin),
    403 for a plain user, and the page must link to the finalize queue."""

    def test_supply_admin_home_200_for_admin_and_links_to_queue(
        self, app, client, seed_workflow_data
    ):
        _seed_supply(seed_workflow_data)

        _login(client, "test:admin")
        resp = client.get("/admin/supply/")

        assert resp.status_code == 200
        assert b"/admin/supply/queue/" in resp.data

    def test_supply_admin_home_403_for_non_admin(self, app, client, seed_workflow_data):
        _seed_supply(seed_workflow_data)

        _login(client, "test:reviewer")
        resp = client.get("/admin/supply/")

        assert resp.status_code == 403


class TestSupplyFinalizeForGroupCall:
    """Prefill from the reviewer's quantity, finalize-and-next, event filter."""

    def _env(self, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        group = _seed_approval_group(wt)
        category = _seed_category(approval_group=group)
        item = _seed_item(category, unit_cost_cents=500)
        return wt, item

    def _order(self, client, wt, cycle, dept, item, public_id, quantities=(5,),
               submitted_at=None):
        work_item = _make_draft_order(wt, cycle, dept, public_id=public_id)
        for n, qty in enumerate(quantities, start=1):
            _add_line(work_item, item, quantity=qty, line_number=n)
        _set_pickup_details(work_item)
        _submit(client, cycle, dept, work_item)
        if submitted_at is not None:
            work_item.submitted_at = submitted_at
            db.session.commit()
        return work_item

    def _decide(self, work_item, status=REVIEW_STATUS_APPROVED, reviewed=None):
        for line in work_item.lines:
            review = WorkLineReview.query.filter_by(
                work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
            ).one()
            review.status = status
            review.decided_by_user_id = "test:reviewer"
            review.note = "Reviewer says <b>ok</b>"
            line.supply_detail.quantity_reviewed = reviewed
        db.session.commit()

    def _other_dept(self, seed_workflow_data):
        base = seed_workflow_data["department"]
        dept = Department(code="OTHERDEPT", name="Other Department",
                          is_active=True, division_id=base.division_id)
        db.session.add(dept)
        db.session.commit()
        return dept

    def test_finalize_prefills_reviewer_quantity(self, app, client, seed_workflow_data):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-A", quantities=(100,))
        self._decide(order, reviewed=40)
        body = client.get(f"/admin/supply/order/{order.public_id}/finalize").get_data(as_text=True)
        assert 'name="approved_qty_1" min="0"\n                       value="40"' in body
        assert "Approved 40 of 100" in body
        assert "Test Reviewer" in body
        assert "Reviewer says &lt;b&gt;ok&lt;/b&gt;" in body

    def test_finalize_prefills_zero_for_rejected_and_requested_for_pending(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-B", quantities=(7, 9))
        reviews = {
            l.line_number: WorkLineReview.query.filter_by(
                work_line_id=l.id, stage=REVIEW_STAGE_APPROVAL_GROUP).one()
            for l in order.lines
        }
        reviews[1].status = REVIEW_STATUS_REJECTED
        db.session.commit()
        body = client.get(f"/admin/supply/order/{order.public_id}/finalize").get_data(as_text=True)
        assert 'name="approved_qty_1" min="0"\n                       value="0"' in body
        assert 'name="approved_qty_2" min="0"\n                       value="9"' in body

    def test_legacy_approved_line_with_null_reviewed_prefills_requested(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-C", quantities=(12,))
        self._decide(order, reviewed=None)
        body = client.get(f"/admin/supply/order/{order.public_id}/finalize").get_data(as_text=True)
        assert 'name="approved_qty_1" min="0"\n                       value="12"' in body
        assert "Approved 12 of" not in body
        # Missing field falls back to the same default and finalizes as before.
        resp = client.post(f"/admin/supply/order/{order.public_id}/finalize", data={})
        assert resp.status_code == 302
        detail = SupplyOrderLineDetail.query.filter_by(
            work_line_id=order.lines[0].id).one()
        assert detail.quantity_approved == 12

    def test_post_missing_field_uses_reviewer_quantity(self, app, client, seed_workflow_data):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-D", quantities=(100,))
        self._decide(order, reviewed=40)
        resp = client.post(f"/admin/supply/order/{order.public_id}/finalize", data={})
        assert resp.status_code == 302
        detail = SupplyOrderLineDetail.query.filter_by(
            work_line_id=order.lines[0].id).one()
        assert detail.quantity_approved == 40

    def test_finalize_and_next_redirects_to_next_ready_order_same_event(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        alt = seed_workflow_data["second_event_cycle"]
        dept = seed_workflow_data["department"]
        other = self._other_dept(seed_workflow_data)
        _login(client, "test:admin")
        now = datetime.now(timezone.utc)
        first = self._order(client, wt, cycle, dept, item, "TST-1",
                            submitted_at=now - timedelta(hours=5))
        # Older, but in another event: must be skipped.
        alt_order = self._order(client, wt, alt, dept, item, "ALT-1",
                                submitted_at=now - timedelta(hours=9))
        # Newer and not ready: skipped.
        self._order(client, wt, cycle, other, item, "TST-NOTREADY",
                    submitted_at=now - timedelta(hours=3))
        for o in (first, alt_order):
            self._decide(o)
        second_dept = Department(code="THIRD", name="Third", is_active=True,
                                 division_id=dept.division_id)
        db.session.add(second_dept)
        db.session.commit()
        second = self._order(client, wt, cycle, second_dept, item, "TST-2",
                             submitted_at=now - timedelta(hours=1))
        self._decide(second)

        resp = client.post(
            f"/admin/supply/order/{first.public_id}/finalize",
            data={"and_next": "1"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(
            f"/admin/supply/order/{second.public_id}/finalize")

    def test_finalize_and_next_returns_to_queue_when_none_ready(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-ONLY")
        self._decide(order)
        resp = client.post(
            f"/admin/supply/order/{order.public_id}/finalize",
            data={"and_next": "1"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(
            f"/admin/supply/queue/?event={cycle.code}")

    def test_and_next_not_applied_on_validation_failure(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-BAD")
        self._decide(order)
        resp = client.post(
            f"/admin/supply/order/{order.public_id}/finalize",
            data={"and_next": "1", "approved_qty_1": "0", "note_1": ""},
        )
        assert resp.status_code == 200
        db.session.refresh(order)
        assert order.status == WORK_ITEM_STATUS_SUBMITTED

    def test_queue_filters_by_event(self, app, client, seed_workflow_data):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        alt = seed_workflow_data["second_event_cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        self._order(client, wt, cycle, dept, item, "TST-IN")
        self._order(client, wt, alt, dept, item, "ALT-OUT")
        body = client.get(f"/admin/supply/queue/?event={cycle.code}").get_data(as_text=True)
        assert "TST-IN" in body and "ALT-OUT" not in body
        body = client.get("/admin/supply/queue/").get_data(as_text=True)
        assert "TST-IN" in body and "ALT-OUT" in body

    def test_queue_unknown_event_shows_error_and_empty_list(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        self._order(client, wt, cycle, dept, item, "TST-IN")
        body = client.get("/admin/supply/queue/?event=NOPE99").get_data(as_text=True)
        assert "NOPE99" in body
        assert "TST-IN" not in body

    def test_quantity_change_at_finalize_needs_no_note(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-Q", quantities=(100,))
        self._decide(order, reviewed=40)
        resp = client.post(
            f"/admin/supply/order/{order.public_id}/finalize",
            data={"approved_qty_1": "30", "note_1": ""},
        )
        assert resp.status_code == 302
        detail = SupplyOrderLineDetail.query.filter_by(
            work_line_id=order.lines[0].id).one()
        assert detail.quantity_approved == 30

    def test_flipping_group_decision_still_needs_note(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-F", quantities=(100,))
        self._decide(order, reviewed=40)
        resp = client.post(
            f"/admin/supply/order/{order.public_id}/finalize",
            data={"approved_qty_1": "0", "note_1": ""},
        )
        assert resp.status_code == 200
        assert b"requires a note" in resp.data

    def test_order_page_shows_finalize_button_to_supply_admin(
        self, app, client, seed_workflow_data
    ):
        wt, item = self._env(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        order = self._order(client, wt, cycle, dept, item, "TST-BTN")
        url = f"/{cycle.code}/{dept.code}/supply/order/{order.public_id}"
        body = client.get(url).get_data(as_text=True)
        assert f"/admin/supply/order/{order.public_id}/finalize" in body
