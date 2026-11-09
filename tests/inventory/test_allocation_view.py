"""Filtering and grouping of demand lines for the Lines tab."""
from app import db
from app.inventory.allocation_view import (
    departments, filter_lines, group_by_department, needs_a_lead, short_count,
)
from app.inventory.demand import demand_lines, find_selection
from tests.inventory.helpers import make_allocation, make_asset, supply_line


def _lines(alloc):
    db.session.commit()
    return demand_lines(alloc.event.id, find_selection("item", alloc.tv.id, alloc.event.id))


def _removed(alloc, label, words="Gone TVs"):
    row = make_allocation(make_asset(alloc.tv, label), supply_line(alloc, alloc.alpha, 1), words)
    row.work_line_id = None
    return row


def test_short_count_and_needs_a_lead(alloc):
    a = supply_line(alloc, alloc.alpha, 4, approved=2)
    b = supply_line(alloc, alloc.alpha, 4)
    make_allocation(make_asset(alloc.tv, "U-0001"), a)
    make_allocation(make_asset(alloc.tv, "U-0002"), b)
    _removed(alloc, "U-0003")
    by_id = {l.work_line_id: l for l in _lines(alloc)}
    assert short_count(by_id[a.id]) == 1
    assert short_count(by_id[b.id]) == 3
    removed = [l for l in by_id.values() if l.work_line_id is None][0]
    assert short_count(removed) == 0 and needs_a_lead(removed)
    assert not needs_a_lead(by_id[a.id])


def test_filter_lines_default_is_still_short(alloc):
    full = supply_line(alloc, alloc.alpha, 1, notes="full one")
    short = supply_line(alloc, alloc.alpha, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), full)
    _removed(alloc, "U-0002")
    lines = _lines(alloc)
    ids = lambda got: {l.work_line_id for l in got}
    assert full.id not in ids(filter_lines(lines, None, "short"))
    assert short.id in ids(filter_lines(lines, None, "short"))
    assert None in ids(filter_lines(lines, None, "short"))
    assert ids(filter_lines(lines, None, "junk")) == ids(filter_lines(lines, None, "short"))
    assert ids(filter_lines(lines, None, "notes")) == {full.id, None}
    assert full.id in ids(filter_lines(lines, None, "all"))
    assert filter_lines(lines, alloc.beta.id, "all") == []


def test_group_by_department_subtotals(alloc):
    supply_line(alloc, alloc.beta, 1)
    first = supply_line(alloc, alloc.alpha, 4)
    second = supply_line(alloc, alloc.alpha, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), first)
    make_allocation(make_asset(alloc.tv, "U-0002"), second)
    make_allocation(make_asset(alloc.tv, "U-0003"), second)
    groups = group_by_department(_lines(alloc))
    assert [g.name for g in groups] == ["Alpha Team", "Beta Team"]
    alpha = groups[0]
    assert (alpha.requested, alpha.allocated, alpha.short) == (6, 3, 3)
    assert departments(_lines(alloc)) == [(alloc.alpha.id, "Alpha Team"), (alloc.beta.id, "Beta Team")]
