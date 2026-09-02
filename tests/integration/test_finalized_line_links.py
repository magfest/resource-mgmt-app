"""Integration tests: per-line action link on a FINALIZED budget request.

A board-held budget is persisted as FINALIZED; "Pending FY Budget Approval"
is a derived display string. Both cases must still expose the line detail
page, which renders read-only because every decision form requires a
checkout and FINALIZED items cannot be checked out.
"""
import re
from datetime import datetime

from app import db
from app.models import (
    WorkItem, WorkLine, BudgetLineDetail,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_FINALIZED, WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED, WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_ADMIN_FINAL, REVIEW_STAGE_APPROVAL_GROUP,
)

DETAIL_URL = "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1"

# The per-line action cell, captured with its label so the test can tell
# "Review" (actionable) from "View" (finalized) apart.
LINE_ACTION = re.compile(
    r'<a class="btn btn-muted" href="[^"]*/line/1/review"[^>]*>\s*(\w+)\s*</a>'
)


def _make_item(data, status, line_status, stage):
    work_item = WorkItem(
        portfolio_id=data["portfolio"].id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=status,
        public_id="TST2026-TESTDEPT-BUD-1",
        created_by_user_id=data["admin"].id,
    )
    if status == WORK_ITEM_STATUS_FINALIZED:
        work_item.finalized_at = datetime.utcnow()
        work_item.finalized_by_user_id = data["admin"].id
    db.session.add(work_item)
    db.session.flush()

    line = WorkLine(
        work_item_id=work_item.id, line_number=1,
        status=line_status, current_review_stage=stage,
        approved_amount_cents=450_000 if line_status == WORK_LINE_STATUS_APPROVED else None,
    )
    db.session.add(line)
    db.session.flush()
    db.session.add(BudgetLineDetail(
        work_line_id=line.id,
        expense_account_id=data["expense_account"].id,
        spend_type_id=data["spend_type"].id,
        quantity=1, unit_price_cents=450_000,
        routed_approval_group_id=data["approval_group"].id,
    ))
    db.session.commit()
    return work_item


def _finalized(data):
    return _make_item(data, WORK_ITEM_STATUS_FINALIZED,
                      WORK_LINE_STATUS_APPROVED, REVIEW_STAGE_ADMIN_FINAL)


def _submitted(data):
    return _make_item(data, WORK_ITEM_STATUS_SUBMITTED,
                      WORK_LINE_STATUS_PENDING, REVIEW_STAGE_APPROVAL_GROUP)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_finalized_item_keeps_a_link_to_the_line(app, client, seed_workflow_data):
    _finalized(seed_workflow_data)
    _login(client, "test:admin")

    resp = client.get(DETAIL_URL)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The board hold is what the team reported against.
    assert "Pending FY Budget Approval" in html
    assert LINE_ACTION.search(html), "no per-line link on a finalized request"


def test_finalized_line_link_is_labelled_view(app, client, seed_workflow_data):
    _finalized(seed_workflow_data)
    _login(client, "test:admin")

    html = client.get(DETAIL_URL).get_data(as_text=True)
    assert LINE_ACTION.search(html).group(1) == "View"


def test_finalized_item_hides_quick_review(app, client, seed_workflow_data):
    _finalized(seed_workflow_data)
    _login(client, "test:admin")

    html = client.get(DETAIL_URL).get_data(as_text=True)
    assert "quick-review" not in html


def test_submitted_item_still_shows_review_and_quick_review(app, client, seed_workflow_data):
    _submitted(seed_workflow_data)
    _login(client, "test:admin")

    html = client.get(DETAIL_URL).get_data(as_text=True)
    assert LINE_ACTION.search(html).group(1) == "Review"
    assert "quick-review" in html
