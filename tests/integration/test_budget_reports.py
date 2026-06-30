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
