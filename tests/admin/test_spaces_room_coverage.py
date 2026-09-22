"""A room assigned whole covers its slices.

Assigning Chesapeake J/K/L/M to Panels means Panels holds J, K, L and M.
The slices inherit that for display only; no SpaceAssignment row is
written for them, so the room's assignment stays the single source of
truth. A slice that carries its own assignment keeps it and is never
covered.
"""
import pytest

from app import db
from app.models import (
    Department, EventCycle, ROLE_SPACE_ADMIN, Space, SpaceAssignment,
    SPACE_KIND_ROOM, SPACE_KIND_SLICE, User, UserRole, Venue,
)
from app.routes.spaces.helpers import build_space_rows


@pytest.fixture
def chesapeake(app):
    """Chesapeake J/K/L/M: one room, four airwall-bounded slices."""
    venue = Venue(code="GAYLORD_NAT", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()

    room = Space(venue_id=venue.id, name="Chesapeake J/K/L/M",
                 code="CHES-JKLM", kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()

    slices = {}
    for letter in ("J", "K"):
        s = Space(venue_id=venue.id, name=f"Chesapeake {letter}",
                  code=f"CHES-{letter}", kind=SPACE_KIND_SLICE,
                  parent_id=room.id)
        db.session.add(s)
        slices[letter] = s
    db.session.commit()
    return {"venue": venue, "cycle": cycle, "room": room, "slices": slices}


def _department(code="PANELS"):
    dept = Department(code=code, name=code.title(), is_active=True)
    db.session.add(dept)
    db.session.flush()
    return dept


def _assign(space, dept, cycle):
    db.session.add(SpaceAssignment(space_id=space.id, event_cycle_id=cycle.id,
                                   department_id=dept.id))
    db.session.commit()


def _by_code(cycle):
    return {r["space"].code: r for r in build_space_rows(cycle)}


def test_an_assigned_rooms_slices_name_the_room_that_covers_them(
        app, chesapeake):
    dept = _department()
    _assign(chesapeake["room"], dept, chesapeake["cycle"])

    rows = _by_code(chesapeake["cycle"])

    assert rows["CHES-J"]["covered_by"]["space"].code == "CHES-JKLM"
    assert rows["CHES-K"]["covered_by"]["space"].code == "CHES-JKLM"


def test_a_covered_slice_inherits_the_rooms_departments(app, chesapeake):
    dept = _department()
    _assign(chesapeake["room"], dept, chesapeake["cycle"])

    covered = _by_code(chesapeake["cycle"])["CHES-J"]

    assert [d.name for d in covered["covered_by"]["departments"]] == ["Panels"]
    # The slice itself still holds nothing. Coverage is read off the room.
    assert covered["departments"] == []


def test_a_slice_with_its_own_assignment_is_not_covered(app, chesapeake):
    """The slice's own departments are real data an admin typed. Inheriting
    over them would read as data loss, so the slice wins."""
    room_dept = _department("PANELS")
    slice_dept = _department("TECHOPS")
    _assign(chesapeake["room"], room_dept, chesapeake["cycle"])
    _assign(chesapeake["slices"]["J"], slice_dept, chesapeake["cycle"])

    rows = _by_code(chesapeake["cycle"])

    assert rows["CHES-J"]["covered_by"] is None
    assert rows["CHES-K"]["covered_by"] is not None


def test_an_unassigned_room_covers_nothing(app, chesapeake):
    rows = _by_code(chesapeake["cycle"])

    assert rows["CHES-J"]["covered_by"] is None
    assert rows["CHES-JKLM"]["covered_by"] is None


def test_a_room_never_covers_itself(app, chesapeake):
    _assign(chesapeake["room"], _department(), chesapeake["cycle"])

    assert _by_code(chesapeake["cycle"])["CHES-JKLM"]["covered_by"] is None


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


@pytest.fixture
def admin(app):
    user = User(id="test:spaceadmin", email="space@test.local",
                display_name="Space Admin", is_active=True)
    db.session.add(user)
    db.session.flush()
    db.session.add(UserRole(user_id=user.id, role_code=ROLE_SPACE_ADMIN))
    db.session.commit()
    return user


def test_the_page_offers_no_edit_link_for_a_covered_slice(
        client, chesapeake, admin):
    _assign(chesapeake["room"], _department(), chesapeake["cycle"])
    _login(client, "test:spaceadmin")

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert f"edit={chesapeake['slices']['J'].id}" not in body
    # The room it is covered by keeps its own Edit; without this the
    # assertion above would pass on a page that rendered no rows at all.
    assert f"edit={chesapeake['room'].id}" in body


def test_a_covered_slice_editor_stays_shut_when_asked_for_by_url(
        client, chesapeake, admin):
    """Hiding the button is not the lock; the editor itself is gated."""
    _assign(chesapeake["room"], _department(), chesapeake["cycle"])
    _login(client, "test:spaceadmin")

    slice_id = chesapeake["slices"]["J"].id
    body = client.get(f"/spaces/?event=SMF2027&edit={slice_id}").get_data(
        as_text=True)

    assert f'id="dept-select-{slice_id}"' not in body
