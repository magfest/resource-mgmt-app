"""The deployment report and its CSV."""
import csv
import io

from app import db
from app.inventory.allocation import allocate
from app.inventory.deployment import deployment_rows, group_rows
from app.inventory.ledger import set_condition
from app.models import Space, SpaceAssignment, Venue
from tests.inventory.helpers import (
    login, make_allocation, make_asset, supply_line, techops_line,
)

HEADER = ["Department", "Room", "Item or service", "Request", "Line", "What they asked for",
          "Requested", "Approved", "Allocated", "Short", "Assets", "Warnings"]


def make_room(name, code, dept=None, event=None, venue=None):
    venue = venue or Venue.query.first() or Venue(code="VEN", name="Test Venue")
    db.session.add(venue)
    db.session.flush()
    space = Space(venue_id=venue.id, name=name, code=code, kind="ROOM")
    db.session.add(space)
    db.session.flush()
    if dept is not None:
        db.session.add(SpaceAssignment(space_id=space.id, event_cycle_id=event.id,
                                       department_id=dept.id))
        db.session.flush()
    return space


def test_rows_carry_assets_short_and_warnings(alloc):
    line = supply_line(alloc, alloc.alpha, 4, approved=3, notes="one 4K")
    unit = make_asset(alloc.tv, "U-0001", name="Lobby TV")
    allocate([unit], line.id, "test:invadmin")
    set_condition(unit, "REPAIR")
    row = deployment_rows(alloc.event.id)[0]
    assert (row.department, row.what, row.words, row.requested, row.approved,
            row.allocated, row.short) == ("Alpha Team", "Test TV", "one 4K", "4", "3", 1, 2)
    assert row.assets == ["U-0001 Lobby TV"] and row.warnings == ["U-0001 is in repair"]


def test_open_lines_with_nothing_allocated_are_listed(alloc):
    supply_line(alloc, alloc.alpha, 2)
    row = deployment_rows(alloc.event.id)[0]
    assert (row.approved, row.allocated, row.short, row.assets) == ("not reviewed", 0, 2, [])


def test_supply_room_is_blank_when_the_department_holds_two_rooms(alloc):
    supply_line(alloc, alloc.alpha, 1)
    make_room("Hall A", "HA", alloc.alpha, alloc.event)
    assert deployment_rows(alloc.event.id)[0].room == "Hall A"
    make_room("Hall B", "HB", alloc.alpha, alloc.event)
    assert deployment_rows(alloc.event.id)[0].room == ""


def test_a_department_wide_techops_line_has_no_room(alloc):
    room = make_room("Hall A", "HA", alloc.alpha, alloc.event)
    techops_line(alloc, alloc.alpha, space=None)
    assert deployment_rows(alloc.event.id)[0].room == ""
    techops_line(alloc, alloc.beta, space=room)
    rows = {r.department: r for r in deployment_rows(alloc.event.id)}
    assert rows["Beta Team"].room == "Hall A"


def test_grouping_by_room_puts_no_room_last(alloc):
    room = make_room("Hall A", "HA")
    techops_line(alloc, alloc.alpha, space=None)
    techops_line(alloc, alloc.beta, space=room)
    groups = group_rows(deployment_rows(alloc.event.id), "room")
    assert [name for name, _ in groups] == ["Hall A", "No room"]


def test_removed_rows_are_not_repeated_across_selections(alloc):
    line = techops_line(alloc, alloc.alpha)
    unit = make_asset(alloc.printer, "U-0001")
    make_allocation(unit, line)
    db.session.delete(line)
    db.session.commit()
    rows = deployment_rows(alloc.event.id)
    assert [r.assets for r in rows] == [["U-0001"]]


def _csv(client, alloc):
    login(client, "test:operator")
    return client.get(f"/inv/allocation/report.csv?event={alloc.event.id}")


def test_csv_matches_the_rows(client, alloc):
    supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    supply_line(alloc, alloc.beta, 1)
    db.session.commit()
    response = _csv(client, alloc)
    assert response.headers["Content-Disposition"] == 'attachment; filename="deployment-TST27.csv"'
    parsed = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert parsed[0] == HEADER
    assert len(parsed) == 1 + len(deployment_rows(alloc.event.id)) == 3


def test_report_is_readable_by_operators_and_refused_to_nobody(client, alloc):
    supply_line(alloc, alloc.alpha, 2)
    db.session.commit()
    for path in ("/inv/allocation/report", "/inv/allocation/report.csv"):
        login(client, "test:nobody")
        assert client.get(path).status_code == 403
        login(client, "test:operator")
        assert client.get(path).status_code == 200


def test_page_groups_and_links_the_csv(client, alloc):
    supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    db.session.commit()
    login(client, "test:operator")
    page = client.get(f"/inv/allocation/report?event={alloc.event.id}").get_data(as_text=True)
    assert "Deployment report" in page and "Download CSV" in page
    assert "Alpha Team" in page and "2 short" in page and 'class="scroll-x"' in page
    by_room = client.get(f"/inv/allocation/report?group=room").get_data(as_text=True)
    assert "No room" in by_room


def test_a_formula_looking_note_is_neutralised(client, alloc):
    supply_line(alloc, alloc.alpha, 1, notes="=SUM(A1)")
    db.session.commit()
    assert "'=SUM(A1)" in _csv(client, alloc).get_data(as_text=True)
