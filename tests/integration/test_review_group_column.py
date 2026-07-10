"""Integration tests: per-line 'Review Group' column/pill on the BUDGET
review screens (work item detail + quick review).

Mirrors the seeding pattern in test_group_subtotals_view.py.
"""
from app import db
from app.models import (
    WorkItem, WorkLine, BudgetLineDetail, ApprovalGroup, UserRole,
    REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING, REVIEW_STAGE_APPROVAL_GROUP, ROLE_APPROVER,
)


def _make_multi_group_item(data, routing):
    """Create a SUBMITTED work item with one line per (group_id, price, qty)."""
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
    ag = ApprovalGroup(
        work_type_id=data["work_type"].id,
        code="HOTEL", name="Hotel Team", is_active=True,
    )
    db.session.add(ag)
    db.session.commit()
    return ag


def test_detail_table_shows_review_group_pills(app, client, seed_workflow_data):
    data = seed_workflow_data
    tech = data["approval_group"]           # code TECH / "Tech Team"
    hotel = _second_group(data)
    _make_multi_group_item(data, [
        (tech.id, 200_00, 2),
        (hotel.id, 50_00, 3),
    ])

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"   # SUPER_ADMIN sees all lines

    resp = client.get("/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Review Group" in html               # new column header
    # Pills carry the group code as text and the full name as a title tooltip.
    assert 'title="Tech Team"' in html
    assert 'title="Hotel Team"' in html
    assert ">TECH<" in html
    assert ">HOTEL<" in html


def test_quick_review_shows_review_group_pills(app, client, seed_workflow_data):
    data = seed_workflow_data
    tech = data["approval_group"]
    hotel = _second_group(data)
    _make_multi_group_item(data, [
        (tech.id, 200_00, 2),
        (hotel.id, 50_00, 3),
    ])

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"

    resp = client.get(
        "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/quick-review"
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Review Group" in html
    assert 'title="Tech Team"' in html          # pill (no subtotal footer here)
    assert 'title="Hotel Team"' in html


def test_multi_group_reviewer_sees_both_group_pills_on_quick_review(
    app, client, seed_workflow_data
):
    """The core complaint: a reviewer in two groups can now tell their lines
    apart by group."""
    data = seed_workflow_data
    tech = data["approval_group"]
    hotel = _second_group(data)
    # Third group the reviewer does NOT belong to (its line stays hidden).
    av = ApprovalGroup(
        work_type_id=data["work_type"].id,
        code="AV", name="AV Team", is_active=True,
    )
    db.session.add(av)
    db.session.commit()

    _make_multi_group_item(data, [
        (tech.id, 200_00, 2),    # visible
        (hotel.id, 100_00, 3),   # visible
        (av.id, 50_00, 1),       # hidden from this reviewer
    ])
    for gid in (tech.id, hotel.id):
        db.session.add(UserRole(
            user_id=data["reviewer"].id, role_code=ROLE_APPROVER,
            approval_group_id=gid,
        ))
    db.session.commit()

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:reviewer"

    resp = client.get(
        "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/quick-review"
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'title="Tech Team"' in html
    assert 'title="Hotel Team"' in html
    assert 'title="AV Team"' not in html        # hidden group's line not shown


def test_predispatch_line_shows_muted_dash(app, client, seed_workflow_data):
    """A line with no routing snapshot yet renders a muted dash, not a pill."""
    data = seed_workflow_data
    _make_multi_group_item(data, [
        (None, 200_00, 2),
    ])

    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"

    resp = client.get("/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Review Group" in html               # column still present