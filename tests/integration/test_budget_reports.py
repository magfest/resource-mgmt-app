"""Integration tests for the three new budget admin reports."""
from app import db
from app.models import (
    WorkItem, WorkLine, BudgetLineDetail, ExpenseAccount,
    REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_DRAFT, WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_APPROVAL_GROUP,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_expense_account_report_lists_lines_for_account(
    app, client, seed_draft_work_item
):
    data = seed_draft_work_item
    event = data["cycle"].code
    account_code = data["expense_account"].code  # "TEST_ACC"
    item = data["work_item"]

    _login(client, "test:admin")

    resp = client.get(
        f"/admin/budget/expense-account/?event={event}&account={account_code}"
    )
    assert resp.status_code == 200
    # The seeded line's request public_id and department appear in the table.
    assert item.public_id.encode() in resp.data
    assert b"Test Department" in resp.data


def test_expense_account_report_no_account_shows_picker(
    app, client, seed_draft_work_item
):
    event = seed_draft_work_item["cycle"].code
    _login(client, "test:admin")
    resp = client.get(f"/admin/budget/expense-account/?event={event}")
    assert resp.status_code == 200
    # No account selected -> no line table, but page renders.
    assert seed_draft_work_item["work_item"].public_id.encode() not in resp.data


def test_expense_account_report_export_returns_csv(
    app, client, seed_draft_work_item
):
    data = seed_draft_work_item
    event = data["cycle"].code
    account_code = data["expense_account"].code
    _login(client, "test:admin")
    resp = client.get(
        f"/admin/budget/expense-account/export?event={event}&account={account_code}"
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
    assert b"Requested" in resp.data  # header row present


def test_expense_account_report_unresolved_event_shows_no_event_state(
    app, client, seed_draft_work_item
):
    """
    A query-string event code that doesn't resolve to any EventCycle (stale
    bookmark, renamed/deactivated event, hand-edited URL) must show the
    "Select an Event Cycle" empty state, not fall through to the no-data
    branch with a None selected_event_cycle (which renders a blank
    "... in <strong></strong>." artifact).
    """
    account_code = seed_draft_work_item["expense_account"].code
    _login(client, "test:admin")

    resp = client.get(
        f"/admin/budget/expense-account/?event=NOSUCHEVENT&account={account_code}"
    )
    assert resp.status_code == 200
    assert b"Select an Event Cycle" in resp.data
    assert b"No Data Found" not in resp.data


def test_reviewer_group_overview_counts_dispatched_line(
    app, client, seed_draft_work_item
):
    # seed_draft_work_item's line has routed_approval_group_id = TECH group,
    # so it is "dispatched" and counts under the TECH group.
    data = seed_draft_work_item
    event = data["cycle"].code
    _login(client, "test:admin")

    resp = client.get(f"/admin/budget/reviewer-group/?event={event}")
    assert resp.status_code == 200
    assert b"Tech Team" in resp.data  # group name shown in overview


def test_reviewer_group_overview_uses_suggested_group_when_not_dispatched(
    app, client, seed_workflow_data
):
    # A line with NO routed group but whose expense account has a suggested
    # group must appear under that suggested group as "awaiting dispatch".
    data = seed_workflow_data
    ag = data["approval_group"]            # TECH
    ea = data["expense_account"]           # TEST_ACC
    ea.approval_group_id = ag.id           # give the account a suggested group
    db.session.add(ea)

    item = WorkItem(
        portfolio_id=data["portfolio"].id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-TESTDEPT-BUD-9",
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
    db.session.add(BudgetLineDetail(
        work_line_id=line.id,
        expense_account_id=ea.id,
        spend_type_id=data["spend_type"].id,
        quantity=2, unit_price_cents=2500,
        routed_approval_group_id=None,      # NOT dispatched
    ))
    db.session.commit()

    event = data["cycle"].code
    _login(client, "test:admin")
    resp = client.get(f"/admin/budget/reviewer-group/?event={event}")
    assert resp.status_code == 200
    assert b"Tech Team" in resp.data
    # Drill-down into the group shows the line.
    resp2 = client.get(f"/admin/budget/reviewer-group/?event={event}&group={ag.code}")
    assert resp2.status_code == 200
    assert item.public_id.encode() in resp2.data
    assert b"Suggested" in resp2.data  # routing-state badge


def test_reviewer_group_export_returns_csv(app, client, seed_draft_work_item):
    event = seed_draft_work_item["cycle"].code
    _login(client, "test:admin")
    resp = client.get(f"/admin/budget/reviewer-group/export?event={event}")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
