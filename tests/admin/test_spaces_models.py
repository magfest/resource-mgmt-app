"""Constraint coverage for the space catalog."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Space, SPACE_KIND_ROOM, SPACE_KIND_SLICE, Venue


@pytest.fixture
def venue(app):
    v = Venue(code="GAYLORD_NAT", name="Gaylord National")
    db.session.add(v)
    db.session.commit()
    return v


def test_space_code_is_unique_within_a_venue(app, venue):
    db.session.add(Space(venue_id=venue.id, name="Expo Hall E",
                         code="EX-E", kind=SPACE_KIND_ROOM))
    db.session.commit()

    db.session.add(Space(venue_id=venue.id, name="Something else",
                         code="EX-E", kind=SPACE_KIND_ROOM))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_same_code_is_allowed_at_a_second_venue(app, venue):
    other = Venue(code="MWEST_HALL", name="MAGWest Hall")
    db.session.add(other)
    db.session.commit()

    db.session.add(Space(venue_id=venue.id, name="Hall A",
                         code="A", kind=SPACE_KIND_ROOM))
    db.session.add(Space(venue_id=other.id, name="Hall A",
                         code="A", kind=SPACE_KIND_ROOM))
    db.session.commit()

    assert db.session.query(Space).filter_by(code="A").count() == 2


def test_a_slice_points_at_its_parent_room(app, venue):
    combo = Space(venue_id=venue.id, name="Woodrow Wilson Ballroom",
                  code="WW", kind=SPACE_KIND_ROOM)
    db.session.add(combo)
    db.session.flush()

    db.session.add(Space(venue_id=venue.id, name="Woodrow Wilson B",
                         code="WW-B", kind=SPACE_KIND_SLICE,
                         parent_id=combo.id))
    db.session.commit()

    assert [c.code for c in combo.children] == ["WW-B"]


from app.models import Department, EventCycle, SpaceAssignment, SpaceEventOverride
from app.routes.spaces.helpers import get_effective_space_name


@pytest.fixture
def cycle(app):
    c = EventCycle(code="SMF2027", name="Super MAGFest 2027")
    db.session.add(c)
    db.session.commit()
    return c


@pytest.fixture
def dept(app):
    d = Department(code="REG", name="Registration", is_active=True)
    db.session.add(d)
    db.session.commit()
    return d


@pytest.fixture
def expo(app, venue):
    s = Space(venue_id=venue.id, name="Expo Hall B",
              code="EX-B", kind=SPACE_KIND_ROOM)
    db.session.add(s)
    db.session.commit()
    return s


def test_a_department_is_assigned_to_a_space_once_per_event(app, expo, cycle, dept):
    db.session.add(SpaceAssignment(space_id=expo.id, event_cycle_id=cycle.id,
                                   department_id=dept.id))
    db.session.commit()

    db.session.add(SpaceAssignment(space_id=expo.id, event_cycle_id=cycle.id,
                                   department_id=dept.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_a_space_has_one_override_per_event(app, expo, cycle):
    db.session.add(SpaceEventOverride(space_id=expo.id, event_cycle_id=cycle.id,
                                      alias="Consoles"))
    db.session.commit()

    db.session.add(SpaceEventOverride(space_id=expo.id, event_cycle_id=cycle.id,
                                      alias="Something else"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_effective_name_uses_the_event_alias(app, expo, cycle):
    db.session.add(SpaceEventOverride(space_id=expo.id, event_cycle_id=cycle.id,
                                      alias="Consoles"))
    db.session.commit()

    assert get_effective_space_name(expo, cycle.id) == "Consoles"


def test_effective_name_falls_back_to_the_venue_name(app, expo, cycle):
    assert get_effective_space_name(expo, cycle.id) == "Expo Hall B"


def test_an_override_without_an_alias_does_not_rename_the_space(app, expo, cycle):
    db.session.add(SpaceEventOverride(space_id=expo.id, event_cycle_id=cycle.id,
                                      is_available=False,
                                      unavailable_reason="Water damage"))
    db.session.commit()

    assert get_effective_space_name(expo, cycle.id) == "Expo Hall B"


def test_an_override_can_point_at_another_space(app, venue, cycle):
    room = Space(venue_id=venue.id, name="Woodrow Wilson Ballroom",
                 code="WW", kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()

    primary = Space(venue_id=venue.id, name="Woodrow Wilson C", code="WW-C",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    member = Space(venue_id=venue.id, name="Woodrow Wilson D", code="WW-D",
                   kind=SPACE_KIND_SLICE, parent_id=room.id)
    db.session.add_all([primary, member])
    db.session.flush()

    override = SpaceEventOverride(space_id=member.id, event_cycle_id=cycle.id,
                                  combined_into_space_id=primary.id)
    db.session.add(override)
    db.session.commit()

    assert override.combined_into.code == "WW-C"
    assert list(primary.combined_members) == [override]
