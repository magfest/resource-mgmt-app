"""Integration tests for the three new budget admin reports."""
from app import db
from app.models import (
    WorkItem, WorkLine, BudgetLineDetail, ExpenseAccount,
    REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_DRAFT, WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_APPROVAL_GROUP, UI_GROUP_HOTEL_SERVICES,
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


def test_hotel_rooms_report_groups_by_pay_type(app, client, seed_workflow_data):
    data = seed_workflow_data

    magpaid = ExpenseAccount(
        code="HTL_STD_MAGPAID", name="Standard (MAGFest-paid)", is_active=True,
        ui_display_group=UI_GROUP_HOTEL_SERVICES,
    )
    held = ExpenseAccount(
        code="HTL_STD_HELD", name="Standard (held)", is_active=True,
        ui_display_group=UI_GROUP_HOTEL_SERVICES,
    )
    db.session.add_all([magpaid, held])
    db.session.flush()

    item = WorkItem(
        portfolio_id=data["portfolio"].id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="TST2026-TESTDEPT-BUD-3",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(item)
    db.session.flush()
    for n, (acc, price) in enumerate([(magpaid, 15000), (held, 0)], start=1):
        line = WorkLine(
            work_item_id=item.id, line_number=n,
            status=WORK_LINE_STATUS_PENDING,
            current_review_stage=REVIEW_STAGE_APPROVAL_GROUP,
        )
        db.session.add(line)
        db.session.flush()
        db.session.add(BudgetLineDetail(
            work_line_id=line.id, expense_account_id=acc.id,
            spend_type_id=data["spend_type"].id,
            quantity=2, unit_price_cents=price,
        ))
    db.session.commit()

    event = data["cycle"].code
    _login(client, "test:admin")
    resp = client.get(f"/admin/budget/hotel-rooms/?event={event}")
    assert resp.status_code == 200
    assert b"MAGFest-paid" in resp.data
    assert b"Third-party held" in resp.data
    assert b"Standard" in resp.data


def test_hotel_rooms_report_export_returns_csv(app, client, seed_workflow_data):
    data = seed_workflow_data

    magpaid = ExpenseAccount(
        code="HTL_EXEC_MAGPAID", name="Executive (MAGFest-paid)", is_active=True,
        ui_display_group=UI_GROUP_HOTEL_SERVICES,
    )
    db.session.add(magpaid)
    db.session.flush()

    item = WorkItem(
        portfolio_id=data["portfolio"].id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="TST2026-TESTDEPT-BUD-4",
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
        work_line_id=line.id, expense_account_id=magpaid.id,
        spend_type_id=data["spend_type"].id,
        quantity=2, unit_price_cents=30000,
    ))
    db.session.commit()

    event = data["cycle"].code
    _login(client, "test:admin")
    resp = client.get(f"/admin/budget/hotel-rooms/export?event={event}")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
    assert b"Pay Type" in resp.data  # header row present
    assert b"MAGFest-paid" in resp.data  # the seeded row is included


def test_hotel_rooms_report_empty_when_no_hotel_lines(
    app, client, seed_draft_work_item
):
    # seed_draft_work_item uses TEST_ACC (not a hotel account) -> no hotel rows.
    event = seed_draft_work_item["cycle"].code
    _login(client, "test:admin")
    resp = client.get(f"/admin/budget/hotel-rooms/?event={event}")
    assert resp.status_code == 200
    assert b"No Data Found" in resp.data


def test_parse_hotel_account_code():
    from app.routes.admin_final.hotel_rooms_report import parse_hotel_account_code
    assert parse_hotel_account_code("HTL_EXEC_MAGPAID") == ("Executive", "MAGFest-paid", "MAGPAID")
    assert parse_hotel_account_code("HTL_HOSP_CRASH") == ("Hospitality", "Self-paid suite for staff", "CRASH")
    # Unknown segments fall back gracefully.
    room, pay, key = parse_hotel_account_code("HTL_WAT_FOO")
    assert key == "FOO"


def test_derive_rooms_and_nights():
    from app.routes.admin_final.hotel_rooms_report import (
        parse_room_count, derive_rooms_and_nights,
    )
    from decimal import Decimal

    # The wizard stores quantity = room-nights and prepends "N rooms:" when >1.
    # 60 room-nights over 12 rooms = 5 nights each.
    assert derive_rooms_and_nights(Decimal(60), "12 rooms: Hotel room") == (12, 5, 60)
    # No prefix -> single room, so nights == room-nights.
    assert derive_rooms_and_nights(Decimal(4), None) == (1, 4, 4)
    assert derive_rooms_and_nights(Decimal(4), "Hotel room for staff") == (1, 4, 4)
    # Non-divisible (e.g. hand-edited description) -> nights rounded to 1 decimal.
    assert derive_rooms_and_nights(Decimal(5), "2 rooms: Hotel room") == (2, 2.5, 5)

    # parse_room_count: prefix, default, and guard against "0 rooms:".
    assert parse_room_count("3 rooms: x") == 3
    assert parse_room_count("Hotel room") == 1
    assert parse_room_count(None) == 1
    assert parse_room_count("0 rooms: x") == 1


def test_hotel_rooms_report_derives_rooms_and_nights_from_description(
    app, client, seed_workflow_data
):
    # A multi-room line: quantity=60 room-nights, "12 rooms:" prefix -> 12 rooms, 5 nights.
    data = seed_workflow_data
    acc = ExpenseAccount(
        code="HTL_EXEC_MAGPAID", name="Executive (MAGFest-paid)", is_active=True,
        ui_display_group=UI_GROUP_HOTEL_SERVICES,
    )
    db.session.add(acc)
    db.session.flush()

    item = WorkItem(
        portfolio_id=data["portfolio"].id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="TST2026-TESTDEPT-BUD-7",
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
        work_line_id=line.id, expense_account_id=acc.id,
        spend_type_id=data["spend_type"].id,
        quantity=60, unit_price_cents=30000,
        description="12 rooms: Hotel room for external partner",
    ))
    db.session.commit()

    event = data["cycle"].code
    _login(client, "test:admin")
    resp = client.get(f"/admin/budget/hotel-rooms/?event={event}")
    assert resp.status_code == 200
    # Report separates Rooms and Nights columns rather than showing room-nights.
    assert b"Nights" in resp.data
    assert b"Summary" in resp.data


def test_build_hotel_summary_crosstab():
    from app.routes.admin_final.hotel_rooms_report import (
        HotelRoomLineRow, build_hotel_summary,
    )

    def row(room_type, pay_type, pay_key, rooms, cents):
        return HotelRoomLineRow(
            department_name="D", work_item_id=1, work_item_public_id="X",
            line_number=1, account_code="HTL", room_type=room_type,
            pay_type=pay_type, pay_type_key=pay_key, rooms=rooms, nights=1,
            room_nights=rooms, unit_price_cents=0, total_cents=cents,
            line_status="PENDING",
        )

    rows = [
        row("Standard", "MAGFest-paid", "MAGPAID", 12, 1000),
        row("Executive", "MAGFest-paid", "MAGPAID", 8, 2000),
        row("Standard", "Third-party held", "HELD", 2, 0),
        row("Executive", "Self-paid suite for staff", "CRASH", 1, 0),
    ]
    s = build_hotel_summary(rows)

    # Pay columns follow PAY_TYPE_ORDER: MAGPAID, HELD, CRASH.
    assert s["pay_labels"] == [
        "MAGFest-paid", "Third-party held", "Self-paid suite for staff",
    ]
    # Room-type rows follow known order: Standard, Executive.
    assert [mr["room_type"] for mr in s["matrix_rows"]] == ["Standard", "Executive"]
    # Standard row cells aligned to pay order + row total.
    std = s["matrix_rows"][0]
    assert std["cells"] == [12, 2, 0]
    assert std["total"] == 14
    # Column room totals + grand total.
    assert s["col_rooms"] == [20, 2, 1]
    assert s["grand_rooms"] == 23
    # Dollars only in the MAGFest-paid column.
    assert s["col_cents"] == [3000, 0, 0]
    assert s["grand_cents"] == 3000
