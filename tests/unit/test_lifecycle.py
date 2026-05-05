"""
Tests for the engine lifecycle helpers (submit_work_item + try_auto_finalize).

Covers the uses_dispatch=True/False submit branches and the
has_admin_final=False auto-finalize path. BUDGET-shaped fixtures verify
existing behavior isn't regressed; TECHOPS-shaped fixtures cover the new
non-dispatch flow end to end.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    ApprovalGroup,
    AUDIT_EVENT_RECALL_TO_DRAFT,
    AUDIT_EVENT_SUBMIT,
    BudgetLineDetail,
    Department,
    EventCycle,
    ExpenseAccount,
    SpendType,
    TechOpsLineDetail,
    TechOpsServiceType,
    User,
    WorkItem,
    WorkItemAuditEvent,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_PENDING,
    ROUTING_STRATEGY_CATEGORY,
    ROUTING_STRATEGY_EXPENSE_ACCOUNT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.routes.work.helpers.lifecycle import (
    recall_to_draft,
    submit_work_item,
    try_auto_finalize,
)


class _FakeUserCtx:
    """Minimal stand-in for UserContext in lifecycle helpers — only user_id is read."""
    def __init__(self, user_id: str):
        self.user_id = user_id


@pytest.fixture(scope="function")
def techops_setup(app):
    """TECHOPS work type with a service type, draft work item, and one line
    ready to be submitted."""
    user = User(id="test:user", email="user@test.local",
                display_name="User", is_active=True)
    cycle = EventCycle(code="TST", name="Test Event",
                       is_active=True, sort_order=1)
    dept = Department(code="DEPT", name="Test Dept", is_active=True)
    db.session.add_all([user, cycle, dept])

    wt = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(wt)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="techops",
        public_id_prefix="TEC", line_detail_type="techops",
        routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    )
    db.session.add(wtc)

    net_group = ApprovalGroup(
        work_type_id=wt.id, code="TECHOPS_NET",
        name="TechOps Networking", is_active=True,
    )
    db.session.add(net_group)
    db.session.flush()

    wifi = TechOpsServiceType(
        code="WIFI", name="WiFi",
        default_approval_group_id=net_group.id, is_active=True,
    )
    db.session.add(wifi)

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
        public_id="TST-DEPT-TEC-1",
        created_by_user_id=user.id,
    )
    db.session.add(work_item)
    db.session.flush()

    line = WorkLine(
        work_item_id=work_item.id, line_number=1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(line)
    db.session.flush()

    db.session.add(TechOpsLineDetail(
        work_line_id=line.id,
        service_type_id=wifi.id,
        description="WiFi for press box",
    ))
    db.session.commit()

    return {
        "user_ctx": _FakeUserCtx(user.id),
        "work_item": work_item,
        "line": line,
        "net_group": net_group,
    }


@pytest.fixture(scope="function")
def budget_setup(app):
    """BUDGET work type with one line ready for submit. Used to verify the
    uses_dispatch=True branch still works as before."""
    user = User(id="test:budget_user", email="budget@test.local",
                display_name="User", is_active=True)
    cycle = EventCycle(code="TSTB", name="Test Event B",
                       is_active=True, sort_order=1)
    dept = Department(code="DEPTB", name="Test Dept B", is_active=True)
    db.session.add_all([user, cycle, dept])

    wt = WorkType(code="BUDGET", name="Budget", is_active=True)
    db.session.add(wt)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="budget",
        public_id_prefix="BUD", line_detail_type="budget",
        routing_strategy=ROUTING_STRATEGY_EXPENSE_ACCOUNT,
        uses_dispatch=True, has_admin_final=True,
    )
    db.session.add(wtc)

    group = ApprovalGroup(
        work_type_id=wt.id, code="TECH",
        name="Tech Team", is_active=True,
    )
    db.session.add(group)
    db.session.flush()

    spend = SpendType(code="BANK", name="Bank", is_active=True)
    ea = ExpenseAccount(
        code="TEST", name="Test Account",
        approval_group_id=group.id, is_active=True,
    )
    db.session.add_all([spend, ea])
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
        public_id="TSTB-DEPTB-BUD-1",
        created_by_user_id=user.id,
    )
    db.session.add(work_item)
    db.session.flush()

    line = WorkLine(
        work_item_id=work_item.id, line_number=1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(line)
    db.session.flush()

    db.session.add(BudgetLineDetail(
        work_line_id=line.id,
        expense_account_id=ea.id,
        spend_type_id=spend.id,
        quantity=1, unit_price_cents=1000,
    ))
    db.session.commit()

    return {
        "user_ctx": _FakeUserCtx(user.id),
        "work_item": work_item,
        "line": line,
        "group": group,
    }


# ============================================================
# submit_work_item
# ============================================================

def test_techops_submit_creates_reviews_inline_and_transitions_to_submitted(techops_setup):
    """For uses_dispatch=False: submit routes lines, creates WorkLineReview
    rows, and goes straight to SUBMITTED."""
    work_item = techops_setup["work_item"]
    line = techops_setup["line"]
    net_group = techops_setup["net_group"]

    new_status = submit_work_item(work_item, techops_setup["user_ctx"])
    db.session.commit()

    assert new_status == WORK_ITEM_STATUS_SUBMITTED
    assert work_item.status == WORK_ITEM_STATUS_SUBMITTED
    assert work_item.submitted_at is not None
    assert line.techops_detail.routed_approval_group_id == net_group.id

    reviews = db.session.query(WorkLineReview).all()
    assert len(reviews) == 1
    assert reviews[0].stage == REVIEW_STAGE_APPROVAL_GROUP
    assert reviews[0].status == REVIEW_STATUS_PENDING
    assert reviews[0].approval_group_id == net_group.id


def test_budget_submit_still_goes_to_awaiting_dispatch(budget_setup):
    """For uses_dispatch=True (BUDGET): submit transitions to AWAITING_DISPATCH
    and does NOT create reviews — that's still dispatch's job."""
    work_item = budget_setup["work_item"]

    new_status = submit_work_item(work_item, budget_setup["user_ctx"])
    db.session.commit()

    assert new_status == WORK_ITEM_STATUS_AWAITING_DISPATCH
    assert work_item.status == WORK_ITEM_STATUS_AWAITING_DISPATCH
    assert work_item.submitted_at is not None
    assert db.session.query(WorkLineReview).count() == 0


# ============================================================
# try_auto_finalize
# ============================================================

def test_techops_auto_finalizes_when_last_review_decided(techops_setup):
    """For has_admin_final=False: when the only pending review is decided,
    try_auto_finalize transitions to FINALIZED."""
    work_item = techops_setup["work_item"]
    submit_work_item(work_item, techops_setup["user_ctx"])
    db.session.commit()

    # Decide the single review
    review = db.session.query(WorkLineReview).one()
    review.status = REVIEW_STATUS_APPROVED
    db.session.flush()

    fired = try_auto_finalize(work_item, techops_setup["user_ctx"])
    db.session.commit()

    assert fired is True
    assert work_item.status == WORK_ITEM_STATUS_FINALIZED
    assert work_item.finalized_at is not None


def test_techops_does_not_finalize_while_a_review_is_pending(techops_setup):
    """try_auto_finalize is a no-op when at least one review is still pending."""
    work_item = techops_setup["work_item"]
    submit_work_item(work_item, techops_setup["user_ctx"])
    db.session.commit()

    fired = try_auto_finalize(work_item, techops_setup["user_ctx"])
    db.session.commit()

    assert fired is False
    assert work_item.status == WORK_ITEM_STATUS_SUBMITTED


def test_budget_does_not_auto_finalize_even_when_all_reviews_decided(budget_setup):
    """For has_admin_final=True: even when all approval-group reviews are
    decided, try_auto_finalize is a no-op — admin_final still owns the
    finalize transition."""
    work_item = budget_setup["work_item"]
    line = budget_setup["line"]
    group = budget_setup["group"]
    user_ctx = budget_setup["user_ctx"]

    # Manually create + decide a review (mimicking dispatch + review)
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    review = WorkLineReview(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=group.id,
        status=REVIEW_STATUS_APPROVED,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(review)
    db.session.commit()

    fired = try_auto_finalize(work_item, user_ctx)
    db.session.commit()

    assert fired is False
    assert work_item.status == WORK_ITEM_STATUS_SUBMITTED
    assert work_item.finalized_at is None


def test_auto_finalize_skips_already_finalized_item(techops_setup):
    """try_auto_finalize is idempotent — calling it on an already-FINALIZED
    item returns False without altering state."""
    work_item = techops_setup["work_item"]
    work_item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()

    fired = try_auto_finalize(work_item, techops_setup["user_ctx"])

    assert fired is False
    assert work_item.status == WORK_ITEM_STATUS_FINALIZED


def test_auto_finalize_skips_item_with_no_reviews(techops_setup):
    """try_auto_finalize won't finalize a SUBMITTED item that somehow has zero
    approval-group reviews — guards against pathological state."""
    work_item = techops_setup["work_item"]
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    db.session.commit()

    # Note: no submit_work_item call, so no reviews exist
    fired = try_auto_finalize(work_item, techops_setup["user_ctx"])

    assert fired is False
    assert work_item.status == WORK_ITEM_STATUS_SUBMITTED


# ============================================================
# recall_to_draft
# ============================================================

def test_recall_from_awaiting_dispatch_returns_to_draft(budget_setup):
    """Happy path: an AWAITING_DISPATCH budget request returns to DRAFT with
    submitted_at and submitted_by_user_id cleared so reports/sorting don't
    see stale timestamps."""
    work_item = budget_setup["work_item"]
    user_ctx = budget_setup["user_ctx"]

    submit_work_item(work_item, user_ctx)
    db.session.commit()
    assert work_item.status == WORK_ITEM_STATUS_AWAITING_DISPATCH
    assert work_item.submitted_at is not None
    assert work_item.submitted_by_user_id is not None

    recall_to_draft(work_item, user_ctx)
    db.session.commit()

    assert work_item.status == WORK_ITEM_STATUS_DRAFT
    assert work_item.submitted_at is None
    assert work_item.submitted_by_user_id is None


def test_recall_writes_audit_event_with_from_status_snapshot(budget_setup):
    """Recall writes an AUDIT_EVENT_RECALL_TO_DRAFT row whose snapshot
    captures the prior status, so the audit log explains the transition."""
    work_item = budget_setup["work_item"]
    user_ctx = budget_setup["user_ctx"]

    submit_work_item(work_item, user_ctx)
    db.session.commit()

    recall_to_draft(work_item, user_ctx)
    db.session.commit()

    recall_events = (
        db.session.query(WorkItemAuditEvent)
        .filter_by(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_RECALL_TO_DRAFT,
        )
        .all()
    )
    assert len(recall_events) == 1
    assert recall_events[0].created_by_user_id == user_ctx.user_id
    assert recall_events[0].snapshot == {"from_status": WORK_ITEM_STATUS_AWAITING_DISPATCH}


def test_recall_preserves_original_submit_audit_row(budget_setup):
    """submit → recall → resubmit should leave both AUDIT_EVENT_SUBMIT rows
    plus the AUDIT_EVENT_RECALL_TO_DRAFT row in the log. The original SUBMIT
    is never rewritten or deleted."""
    work_item = budget_setup["work_item"]
    user_ctx = budget_setup["user_ctx"]

    submit_work_item(work_item, user_ctx)
    db.session.commit()
    recall_to_draft(work_item, user_ctx)
    db.session.commit()
    submit_work_item(work_item, user_ctx)
    db.session.commit()

    submit_events = (
        db.session.query(WorkItemAuditEvent)
        .filter_by(work_item_id=work_item.id, event_type=AUDIT_EVENT_SUBMIT)
        .all()
    )
    recall_events = (
        db.session.query(WorkItemAuditEvent)
        .filter_by(work_item_id=work_item.id, event_type=AUDIT_EVENT_RECALL_TO_DRAFT)
        .all()
    )
    assert len(submit_events) == 2
    assert len(recall_events) == 1


def test_recall_does_not_touch_lines_or_reviews(budget_setup):
    """Recall is a metadata-only transition: line/detail rows are untouched
    and no WorkLineReview rows are created or deleted (BUDGET still has zero
    reviews at AWAITING_DISPATCH; dispatch is what creates them)."""
    work_item = budget_setup["work_item"]
    line = budget_setup["line"]
    user_ctx = budget_setup["user_ctx"]

    detail = line.budget_detail
    quantity_before = detail.quantity
    unit_price_before = detail.unit_price_cents
    line_count_before = len(work_item.lines)

    submit_work_item(work_item, user_ctx)
    db.session.commit()
    assert db.session.query(WorkLineReview).count() == 0

    recall_to_draft(work_item, user_ctx)
    db.session.commit()

    assert len(work_item.lines) == line_count_before
    assert detail.quantity == quantity_before
    assert detail.unit_price_cents == unit_price_before
    assert db.session.query(WorkLineReview).count() == 0
