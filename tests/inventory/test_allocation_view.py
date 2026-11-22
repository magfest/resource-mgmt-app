"""Filtering and grouping of demand lines for the Lines tab."""
from app import db
from app.inventory.allocation_view import (
    asset_choices, departments, filter_lines, group_by_department, needs_a_lead, short_count,
    short_order,
)
from app.inventory.demand import demand_lines, find_selection
from app.inventory.unit_fields import set_unit_value
from app.models.constants import INV_FIELD_CHOICE, INV_FIELD_NUMBER, INV_FIELD_TEXT
from tests.inventory.helpers import make_allocation, make_asset, seed_gear_kind, supply_line


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


def test_group_counts_removed_rows_apart(alloc):
    supply_line(alloc, alloc.alpha, 2)
    row = make_allocation(make_asset(alloc.tv, "U-0001"),
                          supply_line(alloc, alloc.alpha, 1, item=alloc.printer), "Gone TVs")
    row.work_line_id = None
    [alpha] = group_by_department(_lines(alloc))
    assert len(alpha.lines) == 2
    assert (alpha.line_count, alpha.requested, alpha.allocated, alpha.short, alpha.on_removed) == (
        1, 2, 0, 2, 1)


def test_short_order():
    assert short_order("TST27-ALPHA-SUP-3") == "SUP-3"
    assert short_order("TST27-FRONT-DESK-TEC-12") == "TEC-12"
    assert short_order(None) == ""


def test_asset_choices_group_open_lines_with_room(alloc):
    full = supply_line(alloc, alloc.beta, 1, notes="beta full")
    make_allocation(make_asset(alloc.tv, "U-0001"), full)
    room = supply_line(alloc, alloc.alpha, 4, notes="4 TVs, we don't care which")
    make_allocation(make_asset(alloc.tv, "U-0002"), room)
    long = supply_line(alloc, alloc.alpha, 2, notes="x" * 41)
    exact = supply_line(alloc, alloc.alpha, 2, notes="y" * 40)
    bare = supply_line(alloc, alloc.alpha, 1)
    rejected = supply_line(alloc, alloc.alpha, 2, line_status="REJECTED", notes="no thanks")
    make_allocation(make_asset(alloc.tv, "U-0003"), rejected)
    beta_room = supply_line(alloc, alloc.beta, 2, notes="beta open")
    _removed(alloc, "U-0004")
    lines = _lines(alloc)
    groups = asset_choices(lines, None)
    assert [g.department for g in groups] == ["Alpha Team", "Beta Team"]
    alpha = dict(groups[0].options)
    assert alpha[room.id] == "Alpha Team · SUP-1 line 1 · 4 TVs, we don't care which · 3 left"
    assert alpha[long.id] == f"Alpha Team · SUP-2 line 1 · {'x' * 39}… · 2 left"
    assert alpha[exact.id] == f"Alpha Team · SUP-3 line 1 · {'y' * 40} · 2 left"
    assert alpha[bare.id] == "Alpha Team · SUP-4 line 1 · Test TV · 1 left"
    assert rejected.id not in alpha
    assert [line_id for line_id, _ in groups[1].options] == [beta_room.id]
    assert full.id not in dict(groups[1].options)
    assert [g.department for g in asset_choices(lines, alloc.beta.id)] == ["Beta Team"]
    assert asset_choices(lines, 999999) == []


def _tv_kind(alloc):
    on = {"on_label": True}
    kind = seed_gear_kind("TV", "TV", [
        ("MODEL", "Model", INV_FIELD_TEXT, None),
        ("SIZE", "Size", INV_FIELD_NUMBER, "in", on),
        ("RESOLUTION", "Resolution", INV_FIELD_CHOICE, ["1080p", "4K"], on),
    ])
    alloc.tv.gear_kind_id = kind.id
    db.session.flush()
    db.session.expire(alloc.tv)
    return kind


def test_label_line(alloc):
    from app.inventory.unit_fields import label_line
    model, size, resolution = _tv_kind(alloc).fields
    named = make_asset(alloc.tv, "U-0001", name="Lobby TV 1")
    for field, raw in ((model, "LP-200"), (size, "55"), (resolution, "4K")):
        set_unit_value(named, field, raw)
    assert label_line(named) == "Test TV (55in, 4K) · Model LP-200"
    set_unit_value(named, model, "")
    assert label_line(named) == "Test TV (55in, 4K)"
    unnamed = make_asset(alloc.tv, "U-0002")
    assert label_line(unnamed) == "Test TV"
    set_unit_value(unnamed, size, "55")
    set_unit_value(unnamed, resolution, "4K")
    assert label_line(unnamed) == "Test TV · 55 in, 4K"
