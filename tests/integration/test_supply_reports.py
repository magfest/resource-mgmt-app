"""Supply demand reports: overview, per-item page, CSV and access."""
from datetime import datetime, timezone

import pytest

from app import db
from app.models import (
    Department,
    EventCycle,
    SupplyOrderDetail,
    SupplyOrderLineDetail,
    User,
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    REQUEST_KIND_PRIMARY,
    REVIEW_STAGE_APPROVAL_GROUP,
)
from app.routes.work.supply.reports import demand_lines, summarize_by_item

from .supply_helpers import (
    login, seed_approval_group, seed_category, seed_item, seed_reviewer, seed_supply,
)

_counter = {"n": 0}


def _dept(code):
    dept = Department(code=code, name=f"{code} Crew", is_active=True)
    db.session.add(dept)
    db.session.commit()
    return dept


def _order(wt, cycle, dept, status, lines, closed=False):
    """lines: dicts with item, qty and optional notes, review, reviewed, approved,
    removed, confidence."""
    portfolio = WorkPortfolio.query.filter_by(
        work_type_id=wt.id, event_cycle_id=cycle.id, department_id=dept.id).first()
    if portfolio is None:
        portfolio = WorkPortfolio(
            work_type_id=wt.id, event_cycle_id=cycle.id, department_id=dept.id,
            created_by_user_id="test:admin")
        db.session.add(portfolio)
        db.session.flush()
    _counter["n"] += 1
    work_item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY, status=status,
        public_id=f"{cycle.code}-{dept.code}-SUP-{_counter['n']}",
        created_by_user_id="test:admin")
    db.session.add(work_item)
    db.session.flush()
    db.session.add(SupplyOrderDetail(
        work_item_id=work_item.id, created_by_user_id="test:admin",
        closed_at=datetime.now(timezone.utc) if closed else None))
    for number, spec in enumerate(lines, start=1):
        line = WorkLine(work_item_id=work_item.id, line_number=number)
        db.session.add(line)
        db.session.flush()
        db.session.add(SupplyOrderLineDetail(
            work_line_id=line.id, item_id=spec["item"].id,
            quantity_requested=spec["qty"],
            quantity_confidence=spec.get("confidence", "KNOWN"),
            quantity_reviewed=spec.get("reviewed"),
            quantity_approved=spec.get("approved"),
            requester_notes=spec.get("notes"),
            removed_at=datetime.now(timezone.utc) if spec.get("removed") else None))
        if spec.get("review"):
            db.session.add(WorkLineReview(
                work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
                approval_group_id=spec["group"].id, status=spec["review"],
                created_by_user_id="test:admin"))
    db.session.commit()
    return work_item


@pytest.fixture
def world(app, seed_workflow_data):
    wt = seed_supply(seed_workflow_data)
    group = seed_approval_group(wt)
    category = seed_category(approval_group=group)
    pens = seed_item(category, name="Ballpoint Pens")
    boards = seed_item(category, name="Whiteboards")
    return {
        "wt": wt, "group": group, "category": category, "pens": pens, "boards": boards,
        "cycle": seed_workflow_data["cycle"], "alt": seed_workflow_data["second_event_cycle"],
        "dept_a": seed_workflow_data["department"], "dept_b": _dept("ALPHA"),
        "dept_c": _dept("BRAVO"), "data": seed_workflow_data,
    }


def _rows(world, **kw):
    kw.setdefault("include_drafts", False)
    return demand_lines(world["cycle"].id, **kw)


def _by_name(world, name, **kw):
    return {r.item_name: r for r in summarize_by_item(_rows(world, **kw))}[name]


def _basic(world):
    g = world["group"]
    _order(world["wt"], world["cycle"], world["dept_a"], "SUBMITTED",
           [{"item": world["pens"], "qty": 100, "review": "APPROVED", "group": g, "reviewed": 40}])
    _order(world["wt"], world["cycle"], world["dept_b"], "SUBMITTED",
           [{"item": world["pens"], "qty": 50, "review": "PENDING", "group": g}])


def test_demand_totals_across_departments(world):
    _basic(world)
    pens = _by_name(world, "Ballpoint Pens")
    assert (pens.departments, pens.requested) == (2, 150)


def test_reviewer_column_counts_decided_lines_only(world):
    _basic(world)
    assert _by_name(world, "Ballpoint Pens").reviewer == 40


def test_reviewer_approved_with_null_reviewed_counts_requested(world):
    g = world["group"]
    _order(world["wt"], world["cycle"], world["dept_a"], "SUBMITTED",
           [{"item": world["pens"], "qty": 70, "review": "APPROVED", "group": g}])
    assert _by_name(world, "Ballpoint Pens").reviewer == 70


def test_rejected_line_counts_in_requested_not_reviewer(world):
    g = world["group"]
    _order(world["wt"], world["cycle"], world["dept_a"], "SUBMITTED",
           [{"item": world["pens"], "qty": 30, "review": "REJECTED", "group": g, "reviewed": 0},
            {"item": world["boards"], "qty": 9, "review": "REJECTED", "group": g}])
    pens = _by_name(world, "Ballpoint Pens")
    assert (pens.requested, pens.reviewer) == (30, 0)
    assert _by_name(world, "Whiteboards").reviewer == 0


def test_kicked_back_line_excluded_from_reviewer(world):
    g = world["group"]
    _order(world["wt"], world["cycle"], world["dept_a"], "SUBMITTED",
           [{"item": world["pens"], "qty": 30, "review": "NEEDS_INFO", "group": g}])
    pens = _by_name(world, "Ballpoint Pens")
    assert (pens.requested, pens.reviewer) == (30, None)


def test_final_column_counts_finalized_orders_only(world):
    _basic(world)
    _order(world["wt"], world["cycle"], world["dept_c"], "FINALIZED",
           [{"item": world["pens"], "qty": 20, "approved": 15}])
    pens = _by_name(world, "Ballpoint Pens")
    assert pens.final == 15


def test_closed_orders_and_removed_lines_excluded(world):
    _basic(world)
    _order(world["wt"], world["cycle"], world["dept_c"], "FINALIZED",
           [{"item": world["pens"], "qty": 999, "approved": 999}], closed=True)
    _order(world["wt"], world["cycle"], world["dept_c"], "SUBMITTED",
           [{"item": world["pens"], "qty": 888, "removed": True}])
    assert _by_name(world, "Ballpoint Pens").requested == 150


def test_drafts_only_with_toggle(world):
    _basic(world)
    _order(world["wt"], world["cycle"], world["dept_c"], "DRAFT",
           [{"item": world["pens"], "qty": 5}])
    assert _by_name(world, "Ballpoint Pens").requested == 150
    with_drafts = _rows(world, include_drafts=True)
    assert sum(r.requested for r in with_drafts) == 155
    assert {r.display_status for r in with_drafts} == {"Submitted", "Draft"}


def test_other_event_excluded(world):
    _basic(world)
    _order(world["wt"], world["alt"], world["dept_a"], "SUBMITTED",
           [{"item": world["pens"], "qty": 7}])
    assert _by_name(world, "Ballpoint Pens").requested == 150


def test_category_filter(world):
    other = seed_category(approval_group=world["group"], code="TOOLS")
    saw = seed_item(other, name="Hand Saw")
    _order(world["wt"], world["cycle"], world["dept_a"], "SUBMITTED",
           [{"item": world["pens"], "qty": 5}, {"item": saw, "qty": 2}])
    rows = _rows(world, category_id=other.id)
    assert [r.item_name for r in rows] == ["Hand Saw"]


def test_query_count_is_flat(app, world):
    from sqlalchemy import event
    _basic(world)
    for code in ("CHARLIE", "DELTA", "ECHO"):
        _order(world["wt"], world["cycle"], _dept(code), "SUBMITTED",
               [{"item": world["pens"], "qty": 1}, {"item": world["boards"], "qty": 1}])
    count = []
    def counter(*args):
        count.append(1)
    event.listen(db.engine, "before_cursor_execute", counter)
    try:
        rows = _rows(world)
    finally:
        event.remove(db.engine, "before_cursor_execute", counter)
    assert len(rows) == 8
    assert len(count) <= 2


def test_item_page_rows_match_overview_total(client, world):
    _basic(world)
    login(client, "test:admin")
    rows = _rows(world, item_id=world["pens"].id)
    overview = _by_name(world, "Ballpoint Pens")
    assert sum(r.requested for r in rows) == overview.requested
    resp = client.get(f"/admin/supply/reports/item/{world['pens'].id}?event=TST2026")
    assert resp.status_code == 200


def test_item_page_shows_notes_and_confidence_escaped(client, world):
    _order(world["wt"], world["cycle"], world["dept_a"], "SUBMITTED",
           [{"item": world["pens"], "qty": 4, "notes": "<script>alert(1)</script>",
             "confidence": "KNOWN"}])
    login(client, "test:admin")
    html = client.get(f"/admin/supply/reports/item/{world['pens'].id}?event=TST2026").get_data(as_text=True)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html
    assert "overflow-x" in html


def test_item_page_empty_state_and_404(client, world):
    login(client, "test:admin")
    resp = client.get(f"/admin/supply/reports/item/{world['pens'].id}?event=TST2026")
    assert resp.status_code == 200
    assert "No orders" in resp.get_data(as_text=True)
    assert client.get("/admin/supply/reports/item/999999?event=TST2026").status_code == 404


def test_overview_links_carry_filters(client, world):
    _basic(world)
    login(client, "test:admin")
    html = client.get("/admin/supply/reports/demand?event=TST2026&drafts=1").get_data(as_text=True)
    assert f"/admin/supply/reports/item/{world['pens'].id}?" in html
    assert "drafts=1" in html


def test_default_event_and_unknown_event(client, world):
    _basic(world)
    login(client, "test:admin")
    html = client.get("/admin/supply/reports/demand").get_data(as_text=True)
    assert "Ballpoint Pens" in html
    resp = client.get("/admin/supply/reports/demand?event=NOPE9", follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "NOPE9" in html
    assert "Ballpoint Pens" not in html


def test_default_falls_back_to_latest_event_with_orders(client, world):
    _basic(world)
    EventCycle.query.update({EventCycle.is_default: False})
    db.session.commit()
    login(client, "test:admin")
    html = client.get("/admin/supply/reports/demand").get_data(as_text=True)
    assert "Ballpoint Pens" in html


def test_csv_has_bom_header_and_formula_safe_cells(client, world):
    _order(world["wt"], world["cycle"], world["dept_a"], "SUBMITTED",
           [{"item": world["pens"], "qty": 4, "notes": "=HYPERLINK(\"x\")"}])
    login(client, "test:admin")
    resp = client.get("/admin/supply/reports/demand?event=TST2026&format=csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert resp.data.startswith(b"\xef\xbb\xbf")
    text = resp.data.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Event,")
    resp = client.get(f"/admin/supply/reports/item/{world['pens'].id}?event=TST2026&format=csv")
    assert resp.data.startswith(b"\xef\xbb\xbf")
    assert "'=HYPERLINK" in resp.data.decode("utf-8-sig")


def test_tech_reviewer_can_read_reports_department_member_cannot(client, world):
    seed_reviewer(world["data"], world["group"])
    login(client, "test:reviewer")
    assert client.get("/admin/supply/reports/demand?event=TST2026").status_code == 200
    db.session.add(User(id="test:plain", email="plain@test.local",
                        display_name="Plain Member", is_active=True))
    db.session.commit()
    login(client, "test:plain")
    assert client.get("/admin/supply/reports/demand?event=TST2026").status_code == 403
    assert client.get(f"/admin/supply/reports/item/{world['pens'].id}").status_code == 403


def test_nav_entry_only_for_report_readers(client, world):
    seed_reviewer(world["data"], world["group"])
    login(client, "test:reviewer")
    assert "Demand Report" in client.get("/").get_data(as_text=True)
    db.session.add(User(id="test:plain", email="plain@test.local",
                        display_name="Plain Member", is_active=True))
    db.session.commit()
    login(client, "test:plain")
    assert "Demand Report" not in client.get("/").get_data(as_text=True)
