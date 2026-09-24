"""spaces_for_department(): the spaces subsystem's first outside accessor.

Every scenario mirrors a rule build_space_rows() applies on the Spaces
admin page (app/routes/spaces/helpers.py), so a department sees the same
name and status an admin would.
"""
from sqlalchemy import event

from app import db
from app.models import (
    Department, EventCycle, Space, SpaceAssignment, SpaceEventOverride,
    SPACE_KIND_FREEFORM, SPACE_KIND_ROOM, SPACE_KIND_SLICE, Venue,
)
from app.routes.spaces.helpers import build_space_rows
from app.routes.spaces.queries import spaces_for_department


def _venue_and_cycle():
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()
    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    return venue, cycle


def _department(code="SPACEDEPT"):
    dept = Department(code=code, name=code.title(), is_active=True)
    db.session.add(dept)
    db.session.flush()
    return dept


def _assign(space, dept, cycle):
    db.session.add(SpaceAssignment(space_id=space.id, event_cycle_id=cycle.id,
                                   department_id=dept.id))


def test_a_department_assigned_a_room_gets_it_with_its_slices_as_covers(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Room One", code="R1",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    slice_a = Space(venue_id=venue.id, name="Room One A", code="R1-A",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    slice_b = Space(venue_id=venue.id, name="Room One B", code="R1-B",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    db.session.add_all([slice_a, slice_b])
    db.session.flush()
    _assign(room, dept, cycle)
    db.session.commit()

    result = spaces_for_department(dept.id, cycle.id)

    assert len(result) == 1
    assert result[0]["space"].code == "R1"
    assert [s.code for s in result[0]["covers"]] == ["R1-A", "R1-B"]


def test_a_department_assigned_a_primary_gets_the_folded_slices_as_covers(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Room Two", code="R2",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    primary = Space(venue_id=venue.id, name="Room Two A", code="R2-A",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    member = Space(venue_id=venue.id, name="Room Two B", code="R2-B",
                   kind=SPACE_KIND_SLICE, parent_id=room.id)
    db.session.add_all([primary, member])
    db.session.flush()
    _assign(primary, dept, cycle)
    db.session.add(SpaceEventOverride(
        space_id=member.id, event_cycle_id=cycle.id,
        combined_into_space_id=primary.id))
    db.session.commit()

    result = spaces_for_department(dept.id, cycle.id)

    assert len(result) == 1
    assert result[0]["space"].code == "R2-A"
    assert [s.code for s in result[0]["covers"]] == ["R2-B"]


def test_a_plain_slice_covers_nothing(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Room Three", code="R3",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    solo = Space(venue_id=venue.id, name="Room Three A", code="R3-A",
                kind=SPACE_KIND_SLICE, parent_id=room.id)
    db.session.add(solo)
    db.session.flush()
    _assign(solo, dept, cycle)
    db.session.commit()

    result = spaces_for_department(dept.id, cycle.id)

    assert len(result) == 1
    assert result[0]["covers"] == []
    assert result[0]["display_name"] == "Room Three A"


def test_an_archived_space_appears_nowhere(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Room Four", code="R4",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    active_slice = Space(venue_id=venue.id, name="Room Four A", code="R4-A",
                         kind=SPACE_KIND_SLICE, parent_id=room.id)
    archived_slice = Space(venue_id=venue.id, name="Room Four B", code="R4-B",
                           kind=SPACE_KIND_SLICE, parent_id=room.id,
                           is_active=False)
    db.session.add_all([active_slice, archived_slice])
    db.session.flush()
    _assign(room, dept, cycle)

    archived_room = Space(venue_id=venue.id, name="Room Five", code="R5",
                          kind=SPACE_KIND_ROOM, is_active=False)
    db.session.add(archived_room)
    db.session.flush()
    _assign(archived_room, dept, cycle)
    db.session.commit()

    result = spaces_for_department(dept.id, cycle.id)

    assert len(result) == 1
    assert result[0]["space"].code == "R4"
    assert [s.code for s in result[0]["covers"]] == ["R4-A"]


def test_an_alias_replaces_the_name(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Room Six", code="R6",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    _assign(room, dept, cycle)
    db.session.add(SpaceEventOverride(
        space_id=room.id, event_cycle_id=cycle.id, alias="Consoles"))
    db.session.commit()

    result = spaces_for_department(dept.id, cycle.id)

    assert result[0]["display_name"] == "Consoles"


def test_an_unavailable_space_reports_its_reason(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Room Seven", code="R7",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    _assign(room, dept, cycle)
    db.session.add(SpaceEventOverride(
        space_id=room.id, event_cycle_id=cycle.id,
        is_available=False, unavailable_reason="Flooded"))
    db.session.commit()

    result = spaces_for_department(dept.id, cycle.id)

    assert result[0]["is_available"] is False
    assert result[0]["unavailable_reason"] == "Flooded"


def test_a_folded_primarys_name_composes_from_its_members(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Chesapeake", code="CHES",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    primary = Space(venue_id=venue.id, name="Chesapeake 4", code="CHES-4",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    member = Space(venue_id=venue.id, name="Chesapeake 5", code="CHES-5",
                   kind=SPACE_KIND_SLICE, parent_id=room.id)
    db.session.add_all([primary, member])
    db.session.flush()
    _assign(primary, dept, cycle)
    db.session.add(SpaceEventOverride(
        space_id=member.id, event_cycle_id=cycle.id,
        combined_into_space_id=primary.id))
    db.session.commit()

    result = spaces_for_department(dept.id, cycle.id)

    assert result[0]["display_name"] == "Chesapeake 4/5"


def test_an_alias_on_a_folded_primary_wins_over_composition(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Chesapeake", code="CHES2",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    primary = Space(venue_id=venue.id, name="Chesapeake 4", code="CHES2-4",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    member = Space(venue_id=venue.id, name="Chesapeake 5", code="CHES2-5",
                   kind=SPACE_KIND_SLICE, parent_id=room.id)
    db.session.add_all([primary, member])
    db.session.flush()
    _assign(primary, dept, cycle)
    db.session.add(SpaceEventOverride(
        space_id=member.id, event_cycle_id=cycle.id,
        combined_into_space_id=primary.id))
    db.session.add(SpaceEventOverride(
        space_id=primary.id, event_cycle_id=cycle.id, alias="Main Stage"))
    db.session.commit()

    result = spaces_for_department(dept.id, cycle.id)

    assert result[0]["display_name"] == "Main Stage"


def test_the_department_page_and_the_spaces_admin_page_agree_on_a_combined_name(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    room = Space(venue_id=venue.id, name="Chesapeake", code="CHES3",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    primary = Space(venue_id=venue.id, name="Chesapeake 4", code="CHES3-4",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    member = Space(venue_id=venue.id, name="Chesapeake 5", code="CHES3-5",
                   kind=SPACE_KIND_SLICE, parent_id=room.id)
    db.session.add_all([primary, member])
    db.session.flush()
    _assign(primary, dept, cycle)
    db.session.add(SpaceEventOverride(
        space_id=member.id, event_cycle_id=cycle.id,
        combined_into_space_id=primary.id))
    db.session.commit()

    department_page_name = spaces_for_department(dept.id, cycle.id)[0]["display_name"]
    admin_row = next(r for r in build_space_rows(cycle)
                     if r["space"].id == primary.id)

    assert department_page_name == "Chesapeake 4/5"
    assert admin_row["display_name"] == department_page_name


def test_a_department_with_nothing_gets_an_empty_list(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()
    db.session.commit()

    assert spaces_for_department(dept.id, cycle.id) == []


def test_query_count_stays_flat_for_ten_spaces(app):
    venue, cycle = _venue_and_cycle()
    dept = _department()

    spaces = []
    for i in range(10):
        space = Space(venue_id=venue.id, name=f"Popup {i}", code=f"POP-{i}",
                     kind=SPACE_KIND_FREEFORM, event_cycle_id=cycle.id)
        db.session.add(space)
        spaces.append(space)
    db.session.flush()
    for space in spaces:
        _assign(space, dept, cycle)
    db.session.commit()

    # Read the ids before counting: commit() expires every instance, so the
    # first post-commit attribute access on dept/cycle is its own refresh
    # query, not a cost spaces_for_department incurs.
    dept_id = dept.id
    cycle_id = cycle.id

    statements = []
    engine = db.session.get_bind()

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        result = spaces_for_department(dept_id, cycle_id)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(result) == 10
    assert len(statements) == 4, (
        f"Expected a flat 4 queries regardless of department size, got "
        f"{len(statements)}"
    )
