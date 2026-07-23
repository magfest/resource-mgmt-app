"""Feature 2: finalize resolves an unresolved concern line to APPROVED."""
from app import db
from app.models import (
    WorkType, WorkTypeConfig, WorkPortfolio, WorkItem, WorkLine,
    WorkLineReview, REQUEST_KIND_PRIMARY, REVIEW_STAGE_APPROVAL_GROUP,
    ROUTING_STRATEGY_DIRECT,
    WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED,
    WORK_LINE_STATUS_PENDING, WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
)
from app.routes import UserContext
from app.routes.work.helpers.lifecycle import try_auto_finalize


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_finalize_resolves_flagged_line_to_recommended(app, client, seed_draft_work_item):
    data = seed_draft_work_item
    item = data["work_item"]
    line = data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        approved_amount_cents=4000,  # reviewer recommended $40
        created_by_user_id=data["admin"].id))
    db.session.commit()

    _login(client, "test:admin")
    resp = client.post(
        f"/admin/final-review/finalize/{item.id}",
        data={"note": "ok"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(line)
    db.session.refresh(item)

    assert line.status == WORK_LINE_STATUS_APPROVED
    assert line.approved_amount_cents == 4000
    assert item.status == WORK_ITEM_STATUS_FINALIZED


def test_finalize_fallback_to_requested_when_no_recommended_amount(app, client, seed_draft_work_item):
    """Fix 2b: flagged line whose AG review has no approved_amount_cents
    falls back to the requested amount (unit_price_cents * quantity) on
    finalize, per the fallback branch in finalize_work_item."""
    data = seed_draft_work_item
    item = data["work_item"]
    line = data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        approved_amount_cents=None,  # no reviewer-recommended amount
        created_by_user_id=data["admin"].id))
    db.session.commit()

    _login(client, "test:admin")
    resp = client.post(
        f"/admin/final-review/finalize/{item.id}",
        data={"note": "ok"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(line)
    db.session.refresh(item)

    # Fixture line: unit_price_cents=5000, quantity=1 -> requested = 5000
    assert line.status == WORK_LINE_STATUS_APPROVED
    assert line.approved_amount_cents == 5000
    assert item.status == WORK_ITEM_STATUS_FINALIZED


def test_auto_finalize_terminates_on_non_admin_final_worktype(app, seed_workflow_data):
    """Fix 2a (spec Section 2e): for a worktype with has_admin_final=False,
    APPROVED_NEEDS_REVIEW is a terminal AG decision, so try_auto_finalize
    finalizes the item once it's the only (non-pending) review."""
    data = seed_workflow_data

    wt = WorkType(code="TESTWT", name="Test Worktype", is_active=True)
    db.session.add(wt)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="testwt",
        public_id_prefix="TWT", line_detail_type="testwt",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
        uses_dispatch=False, has_admin_final=False,
    )
    db.session.add(wtc)

    portfolio = WorkPortfolio(
        work_type_id=wt.id, event_cycle_id=data["cycle"].id,
        department_id=data["department"].id,
        created_by_user_id=data["admin"].id,
    )
    db.session.add(portfolio)
    db.session.flush()

    item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-TWT-1",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(item)
    db.session.flush()

    line = WorkLine(
        work_item_id=item.id, line_number=1,
        status=WORK_LINE_STATUS_PENDING,
        current_review_stage=REVIEW_STAGE_APPROVAL_GROUP,
    )
    db.session.add(line)
    db.session.flush()

    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        created_by_user_id=data["admin"].id,
    ))
    db.session.commit()

    user_ctx = UserContext(
        user_id="test:admin", user=None,
        roles=("SUPER_ADMIN",), is_super_admin=True,
        approval_group_ids=set(),
    )

    result = try_auto_finalize(item, user_ctx)

    assert result is True
    assert item.status == WORK_ITEM_STATUS_FINALIZED
