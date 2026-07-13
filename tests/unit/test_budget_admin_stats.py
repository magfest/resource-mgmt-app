"""Budget admin stats must count only BUDGET items in the selected event."""
from app import db
from app.models import (
    WorkType, WorkPortfolio, WorkItem, EventCycle,
    REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.admin_final.helpers import get_budget_admin_stats


def _add_item(work_type_id, cycle_id, dept_id, admin_id, public_id):
    # seed_workflow_data already creates a WorkPortfolio for
    # (BUDGET work type, cycle, dept); WorkPortfolio has a unique
    # constraint on (work_type_id, event_cycle_id, department_id), so reuse
    # a matching portfolio instead of inserting a duplicate.
    p = WorkPortfolio.query.filter_by(
        work_type_id=work_type_id, event_cycle_id=cycle_id, department_id=dept_id
    ).first()
    if p is None:
        p = WorkPortfolio(work_type_id=work_type_id, event_cycle_id=cycle_id,
                          department_id=dept_id, created_by_user_id=admin_id)
        db.session.add(p)
        db.session.flush()
    db.session.add(WorkItem(
        portfolio_id=p.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED, public_id=public_id,
        created_by_user_id=admin_id,
    ))


def test_stats_exclude_other_worktypes(app, seed_workflow_data):
    data = seed_workflow_data
    techops_wt = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(techops_wt)
    db.session.flush()

    _add_item(data["work_type"].id, data["cycle"].id, data["department"].id,
              data["admin"].id, "TST2026-TESTDEPT-BUD-9")
    _add_item(techops_wt.id, data["cycle"].id, data["department"].id,
              data["admin"].id, "TST2026-TESTDEPT-TEC-1")
    db.session.commit()

    stats = get_budget_admin_stats(data["cycle"], show_all_events=False)
    assert stats["submitted_items"] == 1  # the TECHOPS item must not count


def test_stats_scope_to_selected_event(app, seed_workflow_data):
    data = seed_workflow_data
    cycle2 = EventCycle(code="TST2027", name="Test Event 2027",
                        is_active=True, is_default=False, sort_order=2)
    db.session.add(cycle2)
    db.session.flush()

    _add_item(data["work_type"].id, data["cycle"].id, data["department"].id,
              data["admin"].id, "TST2026-TESTDEPT-BUD-9")
    _add_item(data["work_type"].id, cycle2.id, data["department"].id,
              data["admin"].id, "TST2027-TESTDEPT-BUD-1")
    db.session.commit()

    stats = get_budget_admin_stats(data["cycle"], show_all_events=False)
    assert stats["submitted_items"] == 1

    stats_all = get_budget_admin_stats(None, show_all_events=True)
    assert stats_all["submitted_items"] == 2


def test_budget_admin_home_renders(client, seed_workflow_data):
    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"
    resp = client.get("/admin/budget/")
    assert resp.status_code == 200
