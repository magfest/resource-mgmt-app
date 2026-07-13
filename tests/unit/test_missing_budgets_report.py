"""Missing-budgets report must not count non-BUDGET work items as budgets."""
from app import db
from app.models import (
    Department, WorkType, WorkPortfolio, WorkItem,
    REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_DRAFT,
)
from app.routes.admin_final.missing_budgets_report import (
    get_departments_without_budgets,
)


def test_dept_with_only_techops_item_is_still_missing_budget(app, seed_workflow_data):
    data = seed_workflow_data

    dept2 = Department(code="TECHDEPT", name="Tech Dept", is_active=True)
    db.session.add(dept2)
    techops_wt = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(techops_wt)
    db.session.flush()

    portfolio = WorkPortfolio(
        work_type_id=techops_wt.id,
        event_cycle_id=data["cycle"].id,
        department_id=dept2.id,
        created_by_user_id=data["admin"].id,
    )
    db.session.add(portfolio)
    db.session.flush()
    db.session.add(WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-TECHDEPT-TEC-1",
        created_by_user_id=data["admin"].id,
    ))
    db.session.commit()

    rows = get_departments_without_budgets(data["cycle"].id)
    codes = {r.department_code for r in rows}
    # dept2 has a TECHOPS item but no BUDGET item — it IS missing a budget.
    assert "TECHDEPT" in codes
