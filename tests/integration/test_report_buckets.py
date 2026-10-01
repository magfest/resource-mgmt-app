"""Department Summary and Master Ledger put every budget line in exactly one bucket."""
from app import db
from app.models import (
    BudgetLineDetail,
    WorkItem,
    WorkLine,
    REQUEST_KIND_PRIMARY,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STAGE_APPROVAL_GROUP,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_PAUSED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_REJECTED,
)
from app.routes.admin_final.department_report import get_department_data
from app.routes.admin_final.ledger_report import get_ledger_data

AG = REVIEW_STAGE_APPROVAL_GROUP
ADMIN = REVIEW_STAGE_ADMIN_FINAL

# (item status, line status, stage, requested cents, approved cents)
# Requested amounts are distinct powers of two, so a wrong bucket changes the sum.
LINES = [
    (WORK_ITEM_STATUS_DRAFT, WORK_LINE_STATUS_PENDING, AG, 100, None),
    (WORK_ITEM_STATUS_AWAITING_DISPATCH, WORK_LINE_STATUS_PENDING, None, 200, None),
    (WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_PENDING, AG, 400, None),
    # Reviewer decisions are recommendations; both stay in review.
    (WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_APPROVED, AG, 800, 300),
    (WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_REJECTED, AG, 1600, None),
    # Budget admin decisions count before the item is finalized.
    (WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_APPROVED, ADMIN, 3200, 2000),
    (WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_REJECTED, ADMIN, 6400, None),
    (WORK_ITEM_STATUS_PAUSED, WORK_LINE_STATUS_PENDING, AG, 12800, None),
    (WORK_ITEM_STATUS_FINALIZED, WORK_LINE_STATUS_APPROVED, ADMIN, 25600, 25600),
    # A finalized item is decided whatever stage the line was left at.
    (WORK_ITEM_STATUS_FINALIZED, WORK_LINE_STATUS_REJECTED, AG, 51200, None),
]


def _seed_lines(data):
    for n, (item_status, line_status, stage, price, approved) in enumerate(LINES, 1):
        item = WorkItem(
            portfolio_id=data["portfolio"].id,
            request_kind=REQUEST_KIND_PRIMARY,
            status=item_status,
            public_id=f"TST2026-TESTDEPT-BUD-{n}",
            created_by_user_id=data["admin"].id,
        )
        db.session.add(item)
        db.session.flush()
        line = WorkLine(
            work_item_id=item.id, line_number=1, status=line_status,
            current_review_stage=stage, approved_amount_cents=approved,
        )
        db.session.add(line)
        db.session.flush()
        db.session.add(BudgetLineDetail(
            work_line_id=line.id,
            expense_account_id=data["expense_account"].id,
            spend_type_id=data["spend_type"].id,
            quantity=1, unit_price_cents=price,
        ))
    db.session.commit()


def _assert_buckets(row):
    assert row.draft_cents == 100
    assert row.in_review_cents == 200 + 400 + 800 + 1600 + 12800
    assert row.approved_requested_cents == 3200 + 25600
    assert row.approved_cents == 2000 + 25600
    assert row.rejected_cents == 6400 + 51200
    assert row.reduced_cents == 1200
    assert row.ceiling_cents == row.approved_cents + row.in_review_cents
    # Nothing dropped or double counted: requested is every non-draft line.
    all_lines = sum(price for _, _, _, price, _ in LINES)
    assert row.requested_cents == all_lines - row.draft_cents


def test_department_summary_buckets(app, seed_workflow_data):
    _seed_lines(seed_workflow_data)

    rows = get_department_data(seed_workflow_data["cycle"].id)

    assert len(rows) == 1
    _assert_buckets(rows[0])


def test_master_ledger_buckets(app, seed_workflow_data):
    _seed_lines(seed_workflow_data)

    rows = get_ledger_data(seed_workflow_data["cycle"].id)

    assert len(rows) == 1
    _assert_buckets(rows[0])


def test_reports_and_exports_render(app, client, seed_workflow_data):
    _seed_lines(seed_workflow_data)
    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"
    event = seed_workflow_data["cycle"].code

    for path in ("/admin/budget/departments/", "/admin/budget/ledger/"):
        page = client.get(f"{path}?event={event}")
        assert page.status_code == 200
        assert b"Current Ceiling" in page.data

        csv = client.get(f"{path}export?event={event}")
        assert csv.status_code == 200
        header = csv.data.decode().splitlines()[0]
        assert "Requested,In Review,Approved,Reduced,Rejected,Current Ceiling" in header
