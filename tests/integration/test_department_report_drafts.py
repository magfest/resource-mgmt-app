"""Department Summary flags departments holding an unsubmitted budget draft."""
from app import db
from app.models import (
    Department,
    WorkItem,
    WorkPortfolio,
    WorkType,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes.admin_final.department_report import (
    compute_department_stats,
    get_department_data,
)


def _draft(portfolio, admin, public_id):
    db.session.add(WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id=public_id,
        created_by_user_id=admin.id,
    ))


def _portfolio(data, work_type, department):
    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=data["cycle"].id,
        department_id=department.id, created_by_user_id=data["admin"].id,
    )
    db.session.add(portfolio)
    db.session.flush()
    return portfolio


def test_department_with_draft_lines_is_flagged(app, seed_draft_work_item):
    rows = get_department_data(seed_draft_work_item["cycle"].id)

    assert len(rows) == 1
    assert rows[0].draft_request_count == 1
    assert rows[0].draft_cents == 5000
    assert compute_department_stats(rows).departments_with_drafts == 1


def test_empty_draft_adds_a_row(app, seed_workflow_data):
    """A draft with no lines still shows; it is the likeliest forgotten request."""
    data = seed_workflow_data
    _draft(data["portfolio"], data["admin"], "TST2026-TESTDEPT-BUD-1")
    db.session.commit()

    rows = get_department_data(data["cycle"].id)

    assert len(rows) == 1
    assert rows[0].department_code == "TESTDEPT"
    assert rows[0].draft_request_count == 1
    assert rows[0].requested_cents == 0


def test_other_work_type_drafts_are_not_counted(app, seed_workflow_data):
    data = seed_workflow_data
    techops = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    other_dept = Department(code="OTHERDEPT", name="Other Department", is_active=True)
    db.session.add_all([techops, other_dept])
    db.session.flush()
    _draft(_portfolio(data, techops, other_dept), data["admin"], "TST2026-OTHERDEPT-TO-1")
    db.session.commit()

    assert get_department_data(data["cycle"].id) == []


def test_badge_renders(app, client, seed_draft_work_item):
    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"

    page = client.get(
        f"/admin/budget/departments/?event={seed_draft_work_item['cycle'].code}")

    assert page.status_code == 200
    assert b"1 unsubmitted draft" in page.data
