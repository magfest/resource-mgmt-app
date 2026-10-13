"""One room has one name across every request on the TechOps worklist."""
from app import db
from app.models import (
    Department, EventCycle, Space, SpaceAssignment, SpaceEventOverride, Venue,
    SPACE_KIND_ROOM,
)
from app.routes.spaces.queries import event_space_names, spaces_for_department


def _space(venue, name, code):
    space = Space(venue_id=venue.id, name=name, code=code,
                  kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(space)
    db.session.flush()
    return space


def test_names_match_the_department_view_for_alias_fold_and_plain(app):
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()
    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027", is_active=True,
                       venue_id=venue.id)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()
    aliased = _space(venue, "Chesapeake A", "CHA")
    primary = _space(venue, "RiverView 1", "RV1")
    member = _space(venue, "RiverView 2", "RV2")
    plain = _space(venue, "Storage Room", "STOR")
    for space in (aliased, primary, plain):
        db.session.add(SpaceAssignment(space_id=space.id, event_cycle_id=cycle.id,
                                       department_id=dept.id))
    db.session.add(SpaceEventOverride(space_id=aliased.id, event_cycle_id=cycle.id,
                                      alias="Main Stage"))
    db.session.add(SpaceEventOverride(space_id=member.id, event_cycle_id=cycle.id,
                                      combined_into_space_id=primary.id))
    db.session.commit()

    expected = {e["space"].id: e["display_name"]
                for e in spaces_for_department(dept.id, cycle.id)}
    names = event_space_names(cycle.id, [aliased.id, primary.id, plain.id, None])

    assert names == expected
    assert names[aliased.id] == "Main Stage"
    assert names[primary.id] == "RiverView 1/2"
    assert names[plain.id] == "Storage Room"


def test_alias_applies_to_a_space_no_department_holds(app):
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()
    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027", is_active=True)
    db.session.add(cycle)
    db.session.flush()
    room = _space(venue, "Expo Hall Z", "EXPOZ")
    db.session.add(SpaceEventOverride(space_id=room.id, event_cycle_id=cycle.id,
                                      alias="Arcade"))
    db.session.commit()

    assert event_space_names(cycle.id, [room.id]) == {room.id: "Arcade"}
    assert event_space_names(cycle.id, []) == {}
