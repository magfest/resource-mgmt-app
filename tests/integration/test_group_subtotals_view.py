"""Integration tests: per-group subtotal breakdown on the detail view."""
from app import db
from app.models import (
    WorkItem, WorkLine, BudgetLineDetail, ApprovalGroup, UserRole,
    REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING, WORK_LINE_STATUS_APPROVED,
    REVIEW_STAGE_APPROVAL_GROUP, ROLE_APPROVER,
)


def _make_multi_group_item(data, routing):
    """Create a SUBMITTED work item with one line per entry in `routing`.

    routing: list of (approval_group_id_or_None, unit_price_cents, quantity).
    Returns the WorkItem.
    """
    work_item = WorkItem(
        portfolio_id=data["portfolio"].id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED,
        public_id="TST2026-TESTDEPT-BUD-1",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(work_item)
    db.session.flush()

    for idx, (group_id, price, qty) in enumerate(routing, start=1):
        line = WorkLine(
            work_item_id=work_item.id, line_number=idx,
            status=WORK_LINE_STATUS_PENDING,
            current_review_stage=REVIEW_STAGE_APPROVAL_GROUP,
        )
        db.session.add(line)
        db.session.flush()
        db.session.add(BudgetLineDetail(
            work_line_id=line.id,
            expense_account_id=data["expense_account"].id,
            spend_type_id=data["spend_type"].id,
            quantity=qty, unit_price_cents=price,
            routed_approval_group_id=group_id,
        ))
    db.session.commit()
    return work_item


def _second_group(data):
    """Create and return a second approval group (HOTEL)."""
    ag = ApprovalGroup(
        work_type_id=data["work_type"].id,
        code="HOTEL", name="Hotel Team", is_active=True,
    )
    db.session.add(ag)
    db.session.commit()
    return ag


def test_reviewer_sees_their_group_subtotal_and_relabeled_grand_total(app, client, seed_workflow_data):
    data = seed_workflow_data
    tech = data["approval_group"]           # "Tech Team"
    hotel = _second_group(data)
    # 2 lines routed to TECH (reviewer's group), 1 to HOTEL (hidden).
    _make_multi_group_item(data, [
        (tech.id, 200_00, 2),   # 400.00
        (tech.id, 100_00, 1),   # 100.00
        (hotel.id, 50_00, 3),   # 150.00 — not visible to this reviewer
    ])
    # Make the reviewer an APPROVER for TECH only.
    db.session.add(UserRole(
        user_id=data["reviewer"].id, role_code=ROLE_APPROVER,
        approval_group_id=tech.id,
    ))
    db.session.commit()

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:reviewer"

    resp = client.get("/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Your total (2 lines):" in html
    assert "$500.00" in html               # TECH subtotal / your total
    assert "Full request total:" in html   # relabeled for filtered view
    assert "$650.00" in html               # full request total (500 + 150)
    assert "Grand Total Requested:" not in html


def test_admin_sees_all_group_subtotals_no_your_total(app, client, seed_workflow_data):
    data = seed_workflow_data
    tech = data["approval_group"]
    hotel = _second_group(data)
    _make_multi_group_item(data, [
        (tech.id, 200_00, 2),   # 400.00
        (hotel.id, 50_00, 3),   # 150.00
    ])

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"   # SUPER_ADMIN

    resp = client.get("/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Tech Team (1 line):" in html
    assert "Hotel Team (1 line):" in html
    assert "Your total" not in html                 # admin view, not filtered
    assert "Grand Total Requested:" in html         # admin keeps original label


def test_single_group_admin_request_has_no_breakdown(app, client, seed_workflow_data):
    data = seed_workflow_data
    tech = data["approval_group"]
    _make_multi_group_item(data, [
        (tech.id, 200_00, 2),
        (tech.id, 100_00, 1),
    ])

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"

    resp = client.get("/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Tech Team (" not in html                # breakdown suppressed
    assert "Grand Total Requested:" in html


def test_predispatch_unrouted_lines_have_no_breakdown(app, client, seed_workflow_data):
    data = seed_workflow_data
    _make_multi_group_item(data, [
        (None, 200_00, 2),
        (None, 100_00, 1),
    ])

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"

    resp = client.get("/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Unassigned (" not in html
    assert "Grand Total Requested:" in html


def test_multi_group_reviewer_sees_each_group_and_combined_total(app, client, seed_workflow_data):
    data = seed_workflow_data
    tech = data["approval_group"]           # "Tech Team"
    hotel = _second_group(data)
    # Third, hidden group the reviewer does NOT belong to.
    av = ApprovalGroup(
        work_type_id=data["work_type"].id,
        code="AV", name="AV Team", is_active=True,
    )
    db.session.add(av)
    db.session.commit()

    _make_multi_group_item(data, [
        (tech.id, 200_00, 2),    # 400.00 - visible (reviewer's group)
        (hotel.id, 100_00, 3),   # 300.00 - visible (reviewer's group)
        (av.id, 50_00, 1),       # 50.00  - hidden
    ])
    # Reviewer is an APPROVER for BOTH tech and hotel.
    db.session.add(UserRole(
        user_id=data["reviewer"].id, role_code=ROLE_APPROVER,
        approval_group_id=tech.id,
    ))
    db.session.add(UserRole(
        user_id=data["reviewer"].id, role_code=ROLE_APPROVER,
        approval_group_id=hotel.id,
    ))
    db.session.commit()

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:reviewer"

    resp = client.get("/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Tech Team (" in html
    assert "Hotel Team (" in html
    assert "AV Team (" not in html               # hidden group not in breakdown
    assert "Your total (2 lines):" in html
    assert "$700.00" in html                     # combined visible total (400 + 300)
    assert "$400.00" in html                     # tech-only subtotal, differs from combined
    assert "$300.00" in html                     # hotel-only subtotal, differs from combined
    assert "Full request total:" in html         # relabeled for filtered view
    assert "Grand Total Requested:" not in html
    assert "$750.00" in html                      # full request total (400 + 300 + 50)


def test_approved_subtotals_render_when_approved_column_shown(app, client, seed_workflow_data):
    data = seed_workflow_data
    tech = data["approval_group"]
    hotel = _second_group(data)
    work_item = _make_multi_group_item(data, [
        (tech.id, 200_00, 2),   # 400.00
        (hotel.id, 100_00, 3),  # 300.00
    ])

    tech_line = WorkLine.query.filter_by(
        work_item_id=work_item.id, line_number=1,
    ).first()
    tech_line.status = WORK_LINE_STATUS_APPROVED
    tech_line.approved_amount_cents = 350_00
    db.session.commit()

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"   # SUPER_ADMIN

    resp = client.get("/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Tech Team (" in html
    assert "Hotel Team (" in html
    assert "$350.00" in html                     # approved subtotal for the TECH group