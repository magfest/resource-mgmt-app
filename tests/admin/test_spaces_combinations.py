"""The per-event combination model.

A combination is a space scoped to one event. Nothing validates that its
members are adjacent or belong to one room; the admin owns correctness.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    Department, EventCycle, ROLE_SPACE_ADMIN, Space, SpaceAssignment,
    SpaceCombinationMember, SpaceEventOverride, SPACE_KIND_COMBO,
    SPACE_KIND_ROOM, SPACE_KIND_SLICE, User, UserRole, Venue,
)


@pytest.fixture
def chesapeake(app):
    """Chesapeake J/K/L: one room, three airwall-bounded slices."""
    venue = Venue(code="GAYLORD_NAT", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()

    room = Space(venue_id=venue.id, name="Chesapeake J/K/L",
                 code="CHES-JKL", kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()

    slices = {}
    for letter in ("J", "K", "L"):
        s = Space(venue_id=venue.id, name=f"Chesapeake {letter}",
                  code=f"CHES-{letter}", kind=SPACE_KIND_SLICE,
                  parent_id=room.id)
        db.session.add(s)
        slices[letter] = s
    db.session.commit()
    return {"venue": venue, "cycle": cycle, "room": room, "slices": slices}


def test_a_combination_names_its_members(app, chesapeake):
    cycle = chesapeake["cycle"]
    combo = Space(venue_id=chesapeake["venue"].id, name="Ches J/K",
                  code="CHES-JK", kind=SPACE_KIND_COMBO,
                  event_cycle_id=cycle.id)
    db.session.add(combo)
    db.session.flush()

    for letter in ("J", "K"):
        db.session.add(SpaceCombinationMember(
            combination_space_id=combo.id,
            member_space_id=chesapeake["slices"][letter].id,
        ))
    db.session.commit()

    assert sorted(m.member.code for m in combo.combination_members) == [
        "CHES-J", "CHES-K"]


def test_the_same_member_cannot_be_added_twice(app, chesapeake):
    cycle = chesapeake["cycle"]
    combo = Space(venue_id=chesapeake["venue"].id, name="Ches J/K",
                  code="CHES-JK", kind=SPACE_KIND_COMBO,
                  event_cycle_id=cycle.id)
    db.session.add(combo)
    db.session.flush()

    for _ in range(2):
        db.session.add(SpaceCombinationMember(
            combination_space_id=combo.id,
            member_space_id=chesapeake["slices"]["J"].id,
        ))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_a_slice_may_belong_to_two_combinations(app, chesapeake):
    """Deliberate. Nothing stops an admin building overlapping groupings,
    and Maryland A with C is a legitimate pair though B sits between them.
    """
    cycle = chesapeake["cycle"]
    first = Space(venue_id=chesapeake["venue"].id, name="Ches J/K",
                  code="CHES-JK", kind=SPACE_KIND_COMBO,
                  event_cycle_id=cycle.id)
    second = Space(venue_id=chesapeake["venue"].id, name="Ches J/L",
                   code="CHES-JL", kind=SPACE_KIND_COMBO,
                   event_cycle_id=cycle.id)
    db.session.add_all([first, second])
    db.session.flush()

    for combo in (first, second):
        db.session.add(SpaceCombinationMember(
            combination_space_id=combo.id,
            member_space_id=chesapeake["slices"]["J"].id,
        ))
    db.session.commit()

    assert len(chesapeake["slices"]["J"].member_of) == 2


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


def _combine(chesapeake, letters, name, code):
    combo = Space(venue_id=chesapeake["venue"].id, name=name, code=code,
                  kind=SPACE_KIND_COMBO,
                  event_cycle_id=chesapeake["cycle"].id)
    db.session.add(combo)
    db.session.flush()
    for letter in letters:
        db.session.add(SpaceCombinationMember(
            combination_space_id=combo.id,
            member_space_id=chesapeake["slices"][letter].id,
        ))
    db.session.commit()
    return combo


def test_a_combination_lists_its_members(app, chesapeake, admin, client):
    _combine(chesapeake, ("J", "K"), "Ches J/K", "CHES-JK")
    _login(client, "test:spaceadmin")

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert "Ches J/K" in body
    assert "Chesapeake J, Chesapeake K" in body


def test_a_member_slice_names_its_combination(app, chesapeake, admin, client):
    _combine(chesapeake, ("J", "K"), "Ches J/K", "CHES-JK")
    _login(client, "test:spaceadmin")

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert 'in &#34;Ches J/K&#34;' in body or 'in "Ches J/K"' in body


def test_a_combination_sorts_under_its_members_room(app, chesapeake, admin, client):
    """A combination belongs with the room it was built from, not at the
    bottom of the page, or its relationship to the slices is invisible."""
    _combine(chesapeake, ("J", "K"), "Ches J/K", "CHES-JK")
    from app.routes.spaces.helpers import build_space_rows

    rows = build_space_rows(chesapeake["cycle"])
    codes = [r["space"].code for r in rows]

    assert codes.index("CHES-JKL") < codes.index("CHES-JK")
    assert codes.index("CHES-L") < codes.index("CHES-JK")
    assert all(r["depth"] == 1 for r in rows if r["space"].code == "CHES-JK")


def test_a_rooms_composition_excludes_its_combinations(app, chesapeake):
    """"Made up of" lists what a room divides into. A combination is
    built from those pieces, not one of them."""
    from app.routes.spaces.helpers import build_space_rows

    _combine(chesapeake, ("J", "K"), "Ches J/K", "CHES-JK")

    rows = build_space_rows(chesapeake["cycle"])
    room = next(r for r in rows if r["space"].code == "CHES-JKL")

    assert sorted(room["child_codes"]) == ["CHES-J", "CHES-K", "CHES-L"]
    assert "CHES-JK" not in room["child_codes"]


def test_assigning_a_combination_accounts_for_its_members(app, chesapeake):
    """A slice inside an assigned combination has been dealt with, even
    though the slice itself holds no assignment."""
    from app.routes.spaces.helpers import build_space_rows

    combo = _combine(chesapeake, ("J", "K"), "Ches J/K", "CHES-JK")
    dept = Department(code="FESTOPS", name="FestOps", is_active=True)
    db.session.add(dept)
    db.session.flush()
    db.session.add(SpaceAssignment(space_id=combo.id,
                                   event_cycle_id=chesapeake["cycle"].id,
                                   department_id=dept.id))
    db.session.commit()

    by_code = {r["space"].code: r for r in build_space_rows(chesapeake["cycle"])}

    assert by_code["CHES-J"]["is_accounted_for"] is True
    assert by_code["CHES-K"]["is_accounted_for"] is True
    # L is in no combination and holds no assignment.
    assert by_code["CHES-L"]["is_accounted_for"] is False


def test_combining_two_slices_creates_an_event_scoped_space(app, chesapeake, admin, client):
    _login(client, "test:spaceadmin")
    j = chesapeake["slices"]["J"]
    k = chesapeake["slices"]["K"]

    resp = client.post("/spaces/combine", data={
        "event": "SMF2027",
        "name": "Ches J/K",
        "code": "CHES-JK",
        "member_ids": [str(j.id), str(k.id)],
    }, follow_redirects=True)

    assert resp.status_code == 200
    combo = db.session.query(Space).filter_by(code="CHES-JK").one()
    assert combo.kind == SPACE_KIND_COMBO
    assert combo.event_cycle_id == chesapeake["cycle"].id
    assert len(combo.combination_members) == 2


def test_a_combination_needs_at_least_one_member(app, chesapeake, admin, client):
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/combine", data={
        "event": "SMF2027",
        "name": "Empty",
        "code": "EMPTY",
    }, follow_redirects=True)

    assert "at least one space" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(code="EMPTY").count() == 0


def test_non_adjacent_members_are_allowed(app, chesapeake, admin, client):
    """Maryland A with C is legitimate although B sits between them. Any
    guard added here is a defect."""
    _login(client, "test:spaceadmin")
    j = chesapeake["slices"]["J"]
    l = chesapeake["slices"]["L"]

    client.post("/spaces/combine", data={
        "event": "SMF2027",
        "name": "Ches J/L",
        "code": "CHES-JL",
        "member_ids": [str(j.id), str(l.id)],
    }, follow_redirects=True)

    assert db.session.query(Space).filter_by(code="CHES-JL").count() == 1


def test_a_combination_cannot_take_a_permanent_rooms_code(app, chesapeake, admin, client):
    """Permanent rows and event rows render in one table, so a shared
    code would show one name on two rooms."""
    _login(client, "test:spaceadmin")
    j = chesapeake["slices"]["J"]
    k = chesapeake["slices"]["K"]

    resp = client.post("/spaces/combine", data={
        "event": "SMF2027",
        "name": "Clashing",
        "code": "CHES-JKL",
        "member_ids": [str(j.id), str(k.id)],
    }, follow_redirects=True)

    assert "already exists" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(
        code="CHES-JKL").count() == 1


def test_a_permanent_room_cannot_take_an_event_spaces_code(app, chesapeake, admin, client):
    """A permanent room shows on every event's page, so its code must
    not collide with a space any event already uses."""
    _login(client, "test:spaceadmin")
    _combine(chesapeake, ("J", "K"), "Ches J/K", "CHES-JK")

    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027",
        "name": "A new room",
        "code": "CHES-JK",
        "kind": SPACE_KIND_ROOM,
    }, follow_redirects=True)

    assert "already exists" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(code="CHES-JK").count() == 1


def test_the_add_panel_refuses_to_create_a_combination(app, chesapeake, admin, client):
    """A COMBO with no event breaks the permanence rule: a space row
    outlives the event only if it describes physical structure. Combinations
    are built by Combine spaces, which always scopes them."""
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027",
        "name": "Sneaky combo",
        "code": "SNEAK",
        "kind": SPACE_KIND_COMBO,
    }, follow_redirects=True)

    assert "combine spaces" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(code="SNEAK").count() == 0


def test_dissolving_frees_the_members_and_drops_the_assignment(app, chesapeake, admin, client):
    _login(client, "test:spaceadmin")
    combo = _combine(chesapeake, ("J", "K"), "Ches J/K", "CHES-JK")
    dept = Department(code="FESTOPS", name="FestOps", is_active=True)
    db.session.add(dept)
    db.session.flush()
    db.session.add(SpaceAssignment(space_id=combo.id,
                                   event_cycle_id=chesapeake["cycle"].id,
                                   department_id=dept.id))
    db.session.commit()
    combo_id = combo.id

    client.post(f"/spaces/combination/{combo_id}/dissolve",
                data={"event": "SMF2027"}, follow_redirects=True)

    assert db.session.get(Space, combo_id) is None
    assert db.session.query(SpaceCombinationMember).filter_by(
        combination_space_id=combo_id).count() == 0
    assert db.session.query(SpaceAssignment).filter_by(
        space_id=combo_id).count() == 0
    # The slices themselves are untouched.
    assert db.session.get(Space, chesapeake["slices"]["J"].id) is not None


def test_dissolving_from_another_events_page_404s(app, chesapeake, admin, client):
    """A combination belongs to one event. A crafted POST naming a
    different event at the same venue must not reach it."""
    _login(client, "test:spaceadmin")
    combo = _combine(chesapeake, ("J", "K"), "Ches J/K", "CHES-JK")
    other = EventCycle(code="SMF2028", name="Super MAGFest 2028",
                       is_active=True, venue_id=chesapeake["venue"].id)
    db.session.add(other)
    db.session.commit()

    resp = client.post(f"/spaces/combination/{combo.id}/dissolve",
                       data={"event": "SMF2028"})

    assert resp.status_code == 404
    assert db.session.get(Space, combo.id) is not None


@pytest.fixture
def two_events(app, chesapeake):
    later = EventCycle(code="SMF2028", name="Super MAGFest 2028",
                       is_active=True, venue_id=chesapeake["venue"].id)
    db.session.add(later)
    db.session.commit()
    chesapeake["later"] = later
    return chesapeake


def test_the_same_code_is_allowed_in_two_events(app, two_events):
    """Two events at one venue both name a combination CHES-JK. They are
    different spaces in different years."""
    for cycle in (two_events["cycle"], two_events["later"]):
        db.session.add(Space(venue_id=two_events["venue"].id,
                             name="Ches J/K", code="CHES-JK",
                             kind=SPACE_KIND_COMBO, event_cycle_id=cycle.id))
    db.session.commit()

    assert db.session.query(Space).filter_by(code="CHES-JK").count() == 2


def test_two_permanent_rooms_still_cannot_share_a_code(app, two_events):
    db.session.add(Space(venue_id=two_events["venue"].id, name="Duplicate",
                         code="CHES-JKL", kind=SPACE_KIND_ROOM))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_copying_a_layout_brings_combinations_and_aliases_not_assignments(app, two_events, admin, client):
    _login(client, "test:spaceadmin")
    source = two_events["cycle"]
    combo = _combine(two_events, ("J", "K"), "Ches J/K", "CHES-JK")

    db.session.add(SpaceEventOverride(
        space_id=two_events["slices"]["L"].id, event_cycle_id=source.id,
        alias="Master Control"))
    dept = Department(code="FESTOPS", name="FestOps", is_active=True)
    db.session.add(dept)
    db.session.flush()
    db.session.add(SpaceAssignment(space_id=combo.id,
                                   event_cycle_id=source.id,
                                   department_id=dept.id))
    db.session.commit()

    client.post("/spaces/copy-layout", data={
        "event": "SMF2028",
        "source_event": "SMF2027",
    }, follow_redirects=True)

    later = two_events["later"]
    copied = db.session.query(Space).filter_by(
        code="CHES-JK", event_cycle_id=later.id).one()
    assert len(copied.combination_members) == 2

    alias = db.session.query(SpaceEventOverride).filter_by(
        space_id=two_events["slices"]["L"].id, event_cycle_id=later.id).one()
    assert alias.alias == "Master Control"
    assert alias.is_available is True

    assert db.session.query(SpaceAssignment).filter_by(
        event_cycle_id=later.id).count() == 0


def test_an_availability_only_override_is_not_copied(app, two_events, admin, client):
    """A room out of service last year is not assumed to be out of service
    this year, and the override table stays sparse."""
    _login(client, "test:spaceadmin")
    db.session.add(SpaceEventOverride(
        space_id=two_events["slices"]["J"].id,
        event_cycle_id=two_events["cycle"].id,
        is_available=False, unavailable_reason="Renovation"))
    db.session.commit()

    client.post("/spaces/copy-layout", data={
        "event": "SMF2028",
        "source_event": "SMF2027",
    }, follow_redirects=True)

    assert db.session.query(SpaceEventOverride).filter_by(
        event_cycle_id=two_events["later"].id).count() == 0
