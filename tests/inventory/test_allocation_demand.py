"""Demand: Supply and TechOps lines read for allocation, with their states and warnings."""
from sqlalchemy import event as sa_event

from app import db
from app.inventory.demand import (
    LINE_ARCHIVED, LINE_DRAFT, LINE_OPEN, Shortfall, allocation_events, asset_warning,
    demand_line, demand_lines, find_selection, line_label, line_words, pick_event,
    selection_assets, selections, shortfall,
)
from app.models import WorkItem, WorkPortfolio
from tests.inventory.helpers import make_allocation, make_asset, supply_line, techops_line


def test_a_service_with_no_linked_item_is_not_a_selection(alloc):
    names = [s.name for s in selections(alloc.event.id)]
    assert names == ["Test TV", "TechOps: Printer"]


def test_a_linked_item_is_listed_only_with_supply_demand(alloc):
    techops_line(alloc, alloc.alpha)
    assert "Test Printer" not in [s.name for s in selections(alloc.event.id)]
    supply_line(alloc, alloc.alpha, 1, item=alloc.printer)
    assert "Test Printer" in [s.name for s in selections(alloc.event.id)]
    assert "Test Printer" not in [s.name for s in selections(alloc.other_event.id)]


def test_find_selection_defaults_to_the_first(alloc):
    assert find_selection(None, None, alloc.event.id).name == "Test TV"
    service = find_selection("service", alloc.printer_service.id, alloc.event.id)
    assert (service.kind, service.item_ids) == ("service", (alloc.printer.id,))
    assert find_selection("service", alloc.radio_service.id, alloc.event.id) is None


def test_only_submitted_lines_count_and_drafts_stay_hidden(alloc):
    supply_line(alloc, alloc.alpha, 3, status="DRAFT")
    supply_line(alloc, alloc.beta, 4)
    lines = demand_lines(alloc.event.id, find_selection("item", alloc.tv.id, alloc.event.id))
    assert [(l.department_name, l.requested, l.approved) for l in lines] == [("Beta Team", 4, None)]


def test_techops_lines_count_one_each_and_only_approved_is_approved(alloc):
    for status in ("APPROVED", "APPROVED_NEEDS_REVIEW", "PENDING"):
        techops_line(alloc, alloc.alpha, line_status=status)
    sel = find_selection("service", alloc.printer_service.id, alloc.event.id)
    lines = demand_lines(alloc.event.id, sel)
    assert sorted(l.approved for l in lines if l.approved) == [1]
    assert shortfall(sel, lines) == Shortfall(requested=3, approved=1, allocated=0, free=0)


def test_a_rejected_line_shows_only_while_something_is_allocated(alloc):
    line = supply_line(alloc, alloc.alpha, 2, line_status="REJECTED")
    sel = find_selection("item", alloc.tv.id, alloc.event.id)
    assert demand_lines(alloc.event.id, sel) == []
    make_allocation(make_asset(alloc.tv, "U-0001"), line)
    assert demand_lines(alloc.event.id, sel)[0].warnings == ["Line changed: rejected"]


def test_draft_and_archived_lines_with_allocations_warn(alloc):
    draft = supply_line(alloc, alloc.alpha, 2, status="DRAFT")
    archived = supply_line(alloc, alloc.beta, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), draft)
    make_allocation(make_asset(alloc.tv, "U-0002"), archived)
    db.session.get(WorkItem, archived.work_item_id).is_archived = True
    db.session.flush()
    rows = demand_lines(alloc.event.id, find_selection("item", alloc.tv.id, alloc.event.id))
    assert [(r.state, r.warnings, r.left) for r in rows] == [
        (LINE_DRAFT, ["Line changed: back in draft"], 0),
        (LINE_ARCHIVED, ["Line changed: archived"], 0)]


def test_an_archived_portfolio_archives_its_lines(alloc):
    line = supply_line(alloc, alloc.alpha, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), line)
    item = db.session.get(WorkItem, line.work_item_id)
    db.session.get(WorkPortfolio, item.portfolio_id).is_archived = True
    db.session.flush()
    assert demand_line(line.id).state == LINE_ARCHIVED


def test_a_cut_line_warns_and_keeps_its_allocations(alloc):
    line = supply_line(alloc, alloc.alpha, 4, approved=2)
    for label in ("U-0001", "U-0002", "U-0003"):
        make_allocation(make_asset(alloc.tv, label), line)
    line.supply_detail.quantity_requested = 2
    db.session.commit()
    row = demand_lines(alloc.event.id, find_selection("item", alloc.tv.id, alloc.event.id))[0]
    assert row.allocated == 3 and row.left == 0
    assert row.warnings == ["Line changed: now asks for 2"]


def test_over_approved_warns_only_when_an_approval_exists(alloc):
    approved = supply_line(alloc, alloc.alpha, 4, approved=2)
    unreviewed = supply_line(alloc, alloc.beta, 4)
    for i in range(1, 4):
        make_allocation(make_asset(alloc.tv, f"U-000{i}"), approved)
        make_allocation(make_asset(alloc.tv, f"U-001{i}"), unreviewed)
    rows = demand_lines(alloc.event.id, find_selection("item", alloc.tv.id, alloc.event.id))
    assert [(r.department_name, r.approved, r.warnings, r.left) for r in rows] == [
        ("Alpha Team", 2, ["Over the approved 2"], 1),
        ("Beta Team", None, [], 1)]


def test_a_deleted_line_shows_as_removed_and_still_counts(alloc):
    line = techops_line(alloc, alloc.alpha)
    make_allocation(make_asset(alloc.printer, "U-0001"), line, words="front desk")
    db.session.commit()
    db.session.delete(line)
    db.session.commit()
    sel = find_selection("service", alloc.printer_service.id, alloc.event.id)
    lines = demand_lines(alloc.event.id, sel)
    assert [(l.state, l.words, l.department_name, l.warnings) for l in lines] == [
        ("REMOVED", "front desk", "Alpha Team", ["Line changed: removed"])]
    assert shortfall(sel, lines).allocated == 1 and shortfall(sel, lines).free == 0


def test_full_lines_sort_last(alloc):
    alpha = supply_line(alloc, alloc.alpha, 1)
    supply_line(alloc, alloc.beta, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), alpha)
    rows = demand_lines(alloc.event.id, find_selection("item", alloc.tv.id, alloc.event.id))
    assert [(r.department_name, r.is_full) for r in rows] == [
        ("Beta Team", False), ("Alpha Team", True)]


def test_free_counts_in_service_unallocated_assets_of_any_event(alloc):
    elsewhere = supply_line(alloc, alloc.alpha, 1, event=alloc.other_event)
    make_allocation(make_asset(alloc.tv, "U-0001"), elsewhere)
    make_asset(alloc.tv, "U-0002", condition="REPAIR")
    rented = make_asset(alloc.tv, "U-0003")
    rented.ownership = "RENTED"
    make_asset(alloc.tv, "U-0004")
    db.session.flush()
    sel = find_selection("item", alloc.tv.id, alloc.event.id)
    assert shortfall(sel, demand_lines(alloc.event.id, sel)).free == 2


def test_assets_list_in_service_or_allocated(alloc):
    elsewhere = supply_line(alloc, alloc.alpha, 1, event=alloc.other_event)
    held = make_asset(alloc.tv, "U-0003", condition="REPAIR")
    make_allocation(held, elsewhere)
    make_asset(alloc.tv, "U-0002", condition="LOST")
    make_asset(alloc.tv, "U-0001")
    sel = find_selection("item", alloc.tv.id, alloc.event.id)
    assert [u.identifiers[0].value for u in selection_assets(sel, False)] == [
        "U-0001", "U-0003"]
    assert [u.identifiers[0].value for u in selection_assets(sel, True)] == ["U-0001"]


def test_events_list_and_default(alloc):
    supply_line(alloc, alloc.alpha, 1, event=alloc.other_event)
    assert [e.code for e in allocation_events()] == ["TST28"]
    assert pick_event(allocation_events(), None).code == "TST28"
    supply_line(alloc, alloc.alpha, 1)
    assert pick_event(allocation_events(), None).code == "TST27"
    assert pick_event(allocation_events(), alloc.other_event.id).code == "TST28"
    assert pick_event([], None) is None


def test_line_words(alloc):
    assert line_words(supply_line(alloc, alloc.alpha, 1, notes="one 4K")) == "one 4K"
    assert line_words(supply_line(alloc, alloc.alpha, 1)) == "Test TV"
    printer = techops_line(alloc, alloc.alpha, location="Front desk", usage="badges")
    printer.techops_detail.config = {"volume": "50_250"}
    db.session.flush()
    assert line_words(printer) == "Front desk, badges, 50 to 250 pages/day"
    long = line_words(supply_line(alloc, alloc.alpha, 1, notes="word  " * 40))
    assert len(long) == 80 and long.endswith("…") and "  " not in long


def test_line_label_joins_department_and_words(alloc):
    line = supply_line(alloc, alloc.alpha, 1, notes="one 4K")
    assert line_label(demand_line(line.id)) == "Alpha Team · one 4K"
    assert demand_line(line.id).state == LINE_OPEN


def test_asset_warning(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    assert asset_warning(unit) is None
    for condition, text in [("REPAIR", "U-0001 is in repair"), ("LOST", "U-0001 is lost"),
                            ("RETIRED", "U-0001 is retired"),
                            ("FOUND", "U-0001 is found but not checked")]:
        unit.condition = condition
        assert asset_warning(unit) == text


def test_demand_lines_run_in_a_fixed_number_of_selects(alloc):
    sel = find_selection("item", alloc.tv.id, alloc.event.id)

    def selects_for(n):
        for i in range(n):
            line = supply_line(alloc, alloc.alpha, 2)
            make_allocation(make_asset(alloc.tv, f"U-{n}{i:03d}"), line)
        db.session.commit()
        seen = [0]

        def count(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("SELECT"):
                seen[0] += 1

        sa_event.listen(db.engine, "before_cursor_execute", count)
        try:
            rows = demand_lines(alloc.event.id, sel)
            [(r.words, r.department_name, r.allocations[0].unit.identifiers) for r in rows]
        finally:
            sa_event.remove(db.engine, "before_cursor_execute", count)
        db.session.expire_all()
        return seen[0]

    assert selects_for(1) == selects_for(5)
