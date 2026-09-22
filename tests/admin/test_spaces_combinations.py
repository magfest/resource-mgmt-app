"""Combining, per spec section 3.2: a flag on a slice's override, not a row.

A slice with SpaceEventOverride.combined_into_space_id set folds into
another slice of the same room, for one event. The first half of this file
sets the column directly and checks what Task 1 owns: the renderer and the
coverage rule that read it. The second half exercises the per-room grid
that writes it, added in Task 2.
"""
import html
import re

import pytest

from app import db
from app.models import (
    Department, EventCycle, ROLE_SPACE_ADMIN, Space, SpaceAssignment,
    SpaceEventOverride, SPACE_KIND_ROOM, SPACE_KIND_SLICE, User, UserRole,
    Venue,
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
    for letter, area in (("J", 1123), ("K", 1121), ("L", 1106), ("M", 900)):
        s = Space(venue_id=venue.id, name=f"Chesapeake {letter}",
                  code=f"CHES-{letter}", kind=SPACE_KIND_SLICE,
                  parent_id=room.id, area_sqft=area)
        db.session.add(s)
        slices[letter] = s
    db.session.commit()
    return {"venue": venue, "cycle": cycle, "room": room, "slices": slices}


def _fold(chesapeake, member_letter, primary_letter):
    """Point one slice's override at another slice in the same room."""
    override = SpaceEventOverride(
        space_id=chesapeake["slices"][member_letter].id,
        event_cycle_id=chesapeake["cycle"].id,
        combined_into_space_id=chesapeake["slices"][primary_letter].id,
    )
    db.session.add(override)
    db.session.commit()
    return override


def test_folding_a_slice_adds_no_row_to_its_room(app, chesapeake):
    before = {r["space"].code for r in build_space_rows(chesapeake["cycle"])}
    _fold(chesapeake, "K", "J")
    after = {r["space"].code for r in build_space_rows(chesapeake["cycle"])}

    assert after == before
    assert after == {"CHES-JKLM", "CHES-J", "CHES-K", "CHES-L", "CHES-M"}


def test_a_primary_row_lists_the_codes_pointing_at_it(app, chesapeake):
    _fold(chesapeake, "K", "J")

    by_code = {r["space"].code: r for r in build_space_rows(chesapeake["cycle"])}

    assert by_code["CHES-J"]["combined_codes"] == ["CHES-K"]
    assert by_code["CHES-K"]["combined_codes"] == []
    assert (by_code["CHES-K"]["combined_into_space_id"]
            == chesapeake["slices"]["J"].id)


def test_a_primarys_area_is_the_summed_group(app, chesapeake):
    """SMF2027's own floorplan: Annapolis 2, 3 and 4 are 1123, 1121 and
    1106, and the plan gives their total as 3350."""
    _fold(chesapeake, "K", "J")
    _fold(chesapeake, "L", "J")

    by_code = {r["space"].code: r for r in build_space_rows(chesapeake["cycle"])}

    assert by_code["CHES-J"]["combined_area_sqft"] == 3350
    assert by_code["CHES-M"]["combined_area_sqft"] == 900


def test_a_group_missing_one_area_reports_none(app, chesapeake):
    """A partial sum understates the group while reading as authoritative,
    and the number is used to size an event. Unknown until every member
    has an area.
    """
    chesapeake["slices"]["K"].area_sqft = None
    db.session.commit()
    _fold(chesapeake, "K", "J")

    by_code = {r["space"].code: r for r in build_space_rows(chesapeake["cycle"])}

    assert by_code["CHES-J"]["combined_area_sqft"] is None
    # The member still reports its own missing area, not the group's.
    assert by_code["CHES-K"]["combined_area_sqft"] is None
    # An untouched slice is unaffected.
    assert by_code["CHES-M"]["combined_area_sqft"] == 900


def test_one_room_holds_two_groupings_at_once(app, chesapeake):
    """Maryland's real shape: several primaries in one room, each with its
    own members."""
    _fold(chesapeake, "K", "J")
    _fold(chesapeake, "M", "L")

    by_code = {r["space"].code: r for r in build_space_rows(chesapeake["cycle"])}

    assert by_code["CHES-J"]["combined_codes"] == ["CHES-K"]
    assert by_code["CHES-L"]["combined_codes"] == ["CHES-M"]
    assert by_code["CHES-K"]["combined_into_space_id"] == chesapeake["slices"]["J"].id
    assert by_code["CHES-M"]["combined_into_space_id"] == chesapeake["slices"]["L"].id


def test_a_slice_folded_into_an_assigned_primary_is_accounted_for(app, chesapeake):
    """A slice folded into an assigned primary has been dealt with, even
    though the slice itself holds no assignment."""
    dept = Department(code="FESTOPS", name="FestOps", is_active=True)
    db.session.add(dept)
    db.session.flush()
    db.session.add(SpaceAssignment(space_id=chesapeake["slices"]["J"].id,
                                   event_cycle_id=chesapeake["cycle"].id,
                                   department_id=dept.id))
    _fold(chesapeake, "K", "J")

    by_code = {r["space"].code: r for r in build_space_rows(chesapeake["cycle"])}

    assert by_code["CHES-J"]["is_accounted_for"] is True
    assert by_code["CHES-K"]["is_accounted_for"] is True
    # L holds no fold and no assignment of its own.
    assert by_code["CHES-L"]["is_accounted_for"] is False


def test_a_rooms_composition_excludes_a_folded_slice(app, chesapeake):
    """"Made up of" lists the room's physical slices. Folding one into
    another does not remove either from the room's own children."""
    _fold(chesapeake, "K", "J")

    room = next(r for r in build_space_rows(chesapeake["cycle"])
               if r["space"].code == "CHES-JKLM")

    assert sorted(room["child_codes"]) == [
        "CHES-J", "CHES-K", "CHES-L", "CHES-M"]


def test_clearing_a_fold_returns_the_slice_to_standalone(app, chesapeake):
    """Combining is per-event and reversible: dropping the override, or
    clearing the column, returns the slice to standing alone."""
    override = _fold(chesapeake, "K", "J")
    override.combined_into_space_id = None
    db.session.commit()

    by_code = {r["space"].code: r for r in build_space_rows(chesapeake["cycle"])}

    assert by_code["CHES-K"]["combined_into_space_id"] is None
    assert by_code["CHES-J"]["combined_codes"] == []


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


def test_the_combine_and_dissolve_routes_are_gone(client, chesapeake, admin):
    """Combine spaces and Dissolve are retired with the COMBO row; the grid
    that replaces them is a separate task."""
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/combine", data={"event": "SMF2027"})
    assert resp.status_code == 404

    resp = client.post("/spaces/combination/1/dissolve",
                       data={"event": "SMF2027"})
    assert resp.status_code == 404


def test_the_page_renders_a_fold_with_no_combo_row(client, chesapeake, admin):
    _fold(chesapeake, "K", "J")
    _login(client, "test:spaceadmin")

    resp = client.get("/spaces/?event=SMF2027")

    assert resp.status_code == 200
    assert "COMBO" not in resp.get_data(as_text=True)


# ============================================================
# The combining grid (Task 2): one <select> per slice, inside its room.
# ============================================================

def _combine_room_url(chesapeake):
    return f"/spaces/room/{chesapeake['room'].id}/combine"


def _combine_form(chesapeake, **targets):
    """Build the combine grid's full POST payload.

    Every slice in the room is submitted every time, since the grid is one
    form for the whole room. targets maps a slice letter to the letter it
    should point at; a slice left out submits blank, an untouched select.
    """
    data = {"event": "SMF2027"}
    for letter, slice_ in chesapeake["slices"].items():
        target_letter = targets.get(letter)
        target = chesapeake["slices"][target_letter] if target_letter else None
        data[f"combined_into_{slice_.id}"] = str(target.id) if target else ""
    return data


def _selected_target(body, slice_id):
    """The id selected in one slice's <select>, or None."""
    select_match = re.search(
        r'<select name="combined_into_%d">(.*?)</select>' % slice_id,
        body, re.S)
    assert select_match, f"no select found for slice {slice_id}"
    option_match = re.search(r'<option value="(\d+)"\s+selected',
                             select_match.group(1))
    return int(option_match.group(1)) if option_match else None


def test_the_grid_shows_a_select_per_slice_listing_the_others(client, chesapeake, admin):
    _login(client, "test:spaceadmin")
    room_id = chesapeake["room"].id

    body = client.get(
        f"/spaces/?event=SMF2027&combine={room_id}").get_data(as_text=True)

    for slice_ in chesapeake["slices"].values():
        assert f'name="combined_into_{slice_.id}"' in body
    assert html.unescape(body).count("not combined") == 4
    # K's own select offers J, L and M, never itself.
    k_select = re.search(
        r'<select name="combined_into_%d">(.*?)</select>'
        % chesapeake["slices"]["K"].id, body, re.S).group(1)
    assert f'value="{chesapeake["slices"]["J"].id}"' in k_select
    assert f'value="{chesapeake["slices"]["K"].id}"' not in k_select


def test_the_grid_omits_the_room_itself_as_an_option(client, chesapeake, admin):
    """Scoped to one slice's own <select>, not the whole page: the page
    already carries other ids in other forms, which would pass this
    check vacuously."""
    _login(client, "test:spaceadmin")
    room_id = chesapeake["room"].id

    body = client.get(
        f"/spaces/?event=SMF2027&combine={room_id}").get_data(as_text=True)
    k_select = re.search(
        r'<select name="combined_into_%d">(.*?)</select>'
        % chesapeake["slices"]["K"].id, body, re.S).group(1)

    assert f'value="{room_id}"' not in k_select


def test_the_grid_form_carries_a_csrf_token(client, chesapeake, admin):
    """Scoped to the combine-room <form> itself, not the whole page: every
    row's own archive form already carries a csrf_token field, which would
    pass this check vacuously. A missing field here passes silently in
    this suite, because conftest disables CSRF, and returns 400 in a
    browser."""
    _login(client, "test:spaceadmin")
    room_id = chesapeake["room"].id

    body = client.get(
        f"/spaces/?event=SMF2027&combine={room_id}").get_data(as_text=True)
    grid_form = re.search(
        r'<form method="post"\s+action="/spaces/room/%d/combine">(.*?)</form>'
        % room_id, body, re.S)

    assert grid_form, "no combine-grid form found"
    assert 'name="csrf_token"' in grid_form.group(1)


def test_submitting_the_grid_writes_the_override_and_creates_it(client, chesapeake, admin):
    _login(client, "test:spaceadmin")

    resp = client.post(_combine_room_url(chesapeake),
                       data=_combine_form(chesapeake, K="J"),
                       follow_redirects=True)

    assert resp.status_code == 200
    override = db.session.query(SpaceEventOverride).filter_by(
        space_id=chesapeake["slices"]["K"].id,
        event_cycle_id=chesapeake["cycle"].id).one()
    assert override.combined_into_space_id == chesapeake["slices"]["J"].id


def test_submitting_the_grid_keeps_an_existing_overrides_alias(client, chesapeake, admin):
    """Folding a slice must not erase a name it already carries this
    event; only combined_into_space_id changes."""
    override = SpaceEventOverride(
        space_id=chesapeake["slices"]["K"].id,
        event_cycle_id=chesapeake["cycle"].id,
        alias="Overflow seating",
    )
    db.session.add(override)
    db.session.commit()
    _login(client, "test:spaceadmin")

    client.post(_combine_room_url(chesapeake),
               data=_combine_form(chesapeake, K="J"))

    db.session.refresh(override)
    assert override.combined_into_space_id == chesapeake["slices"]["J"].id
    assert override.alias == "Overflow seating"


def test_clearing_a_fold_drops_a_bare_override_row(client, chesapeake, admin):
    _fold(chesapeake, "K", "J")
    _login(client, "test:spaceadmin")

    client.post(_combine_room_url(chesapeake), data=_combine_form(chesapeake))

    assert db.session.query(SpaceEventOverride).filter_by(
        space_id=chesapeake["slices"]["K"].id,
        event_cycle_id=chesapeake["cycle"].id).count() == 0


def test_clearing_a_fold_keeps_an_override_that_still_carries_an_alias(client, chesapeake, admin):
    override = _fold(chesapeake, "K", "J")
    override.alias = "Overflow seating"
    db.session.commit()
    _login(client, "test:spaceadmin")

    client.post(_combine_room_url(chesapeake), data=_combine_form(chesapeake))

    db.session.refresh(override)
    assert override.combined_into_space_id is None
    assert override.alias == "Overflow seating"


def test_the_grid_refuses_a_target_in_a_different_room(client, chesapeake, admin):
    other_room = Space(venue_id=chesapeake["venue"].id, name="Baltimore",
                       code="BALT", kind=SPACE_KIND_ROOM)
    db.session.add(other_room)
    db.session.flush()
    other_slice = Space(venue_id=chesapeake["venue"].id, name="Baltimore 1",
                        code="BALT-1", kind=SPACE_KIND_SLICE,
                        parent_id=other_room.id)
    db.session.add(other_slice)
    db.session.commit()
    _login(client, "test:spaceadmin")

    data = _combine_form(chesapeake)
    data[f"combined_into_{chesapeake['slices']['K'].id}"] = str(other_slice.id)

    resp = client.post(_combine_room_url(chesapeake), data=data)

    assert resp.status_code == 200
    assert "Pick a slice in this room" in html.unescape(resp.get_data(as_text=True))
    assert db.session.query(SpaceEventOverride).count() == 0


def test_the_grid_refuses_a_target_that_is_not_a_slice(client, chesapeake, admin):
    """The room itself, not one of its slices, is posted as a target."""
    _login(client, "test:spaceadmin")

    data = _combine_form(chesapeake)
    data[f"combined_into_{chesapeake['slices']['K'].id}"] = str(chesapeake["room"].id)

    resp = client.post(_combine_room_url(chesapeake), data=data)

    assert resp.status_code == 200
    assert "Pick a slice in this room" in html.unescape(resp.get_data(as_text=True))
    assert db.session.query(SpaceEventOverride).count() == 0


def test_the_grid_refuses_a_chain_in_one_submission(client, chesapeake, admin):
    """K -> J and J -> L, submitted together: J cannot be both a target
    and itself point elsewhere. Refuses the whole submission."""
    _login(client, "test:spaceadmin")

    resp = client.post(_combine_room_url(chesapeake),
                       data=_combine_form(chesapeake, K="J", J="L"))

    assert resp.status_code == 200
    assert ("is already combined into another slice"
           in html.unescape(resp.get_data(as_text=True)))
    assert db.session.query(SpaceEventOverride).count() == 0


def test_the_grid_refuses_redirecting_an_existing_primary_to_a_third(client, chesapeake, admin):
    """K already points at J. Resubmitting with K unchanged but J now
    pointed at M must be refused, and nothing already saved may change."""
    _fold(chesapeake, "K", "J")
    _login(client, "test:spaceadmin")

    resp = client.post(_combine_room_url(chesapeake),
                       data=_combine_form(chesapeake, K="J", J="M"))

    assert resp.status_code == 200
    assert ("already combined into this one"
           in html.unescape(resp.get_data(as_text=True)))
    override = db.session.query(SpaceEventOverride).filter_by(
        space_id=chesapeake["slices"]["K"].id).one()
    assert override.combined_into_space_id == chesapeake["slices"]["J"].id
    assert db.session.query(SpaceEventOverride).filter_by(
        space_id=chesapeake["slices"]["J"].id).count() == 0


def test_a_rejected_submission_keeps_every_choice_and_marks_the_bad_rows(client, chesapeake, admin):
    """Refuse the whole submission and re-render with each offending row
    marked, keeping what was chosen."""
    _login(client, "test:spaceadmin")
    k_id = chesapeake["slices"]["K"].id
    j_id = chesapeake["slices"]["J"].id
    l_id = chesapeake["slices"]["L"].id
    m_id = chesapeake["slices"]["M"].id

    resp = client.post(_combine_room_url(chesapeake),
                       data=_combine_form(chesapeake, K="J", J="L", M="J"))

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert _selected_target(body, k_id) == j_id
    assert _selected_target(body, j_id) == l_id
    assert _selected_target(body, m_id) == j_id
    assert db.session.query(SpaceEventOverride).count() == 0
    # A structural marker, not display text: J's row (the one that gained
    # a second incoming pointer while itself pointing elsewhere) is flagged.
    assert f'data-combine-error="{j_id}"' in body


def test_an_oversized_target_id_is_refused_without_a_500(client, chesapeake, admin):
    from app.routes.spaces.catalog import MAX_SPACE_ID
    _login(client, "test:spaceadmin")

    data = _combine_form(chesapeake)
    data[f"combined_into_{chesapeake['slices']['K'].id}"] = str(MAX_SPACE_ID + 1)

    resp = client.post(_combine_room_url(chesapeake), data=data)

    assert resp.status_code == 200
    assert db.session.query(SpaceEventOverride).count() == 0


def test_an_oversized_room_id_in_the_url_404s(client, chesapeake, admin):
    from app.routes.spaces.catalog import MAX_SPACE_ID
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/room/{MAX_SPACE_ID + 1}/combine",
                       data={"event": "SMF2027"})

    assert resp.status_code == 404


def test_combining_posts_only_to_a_room_never_a_slice(client, chesapeake, admin):
    """Posting a slice's own id where a room id belongs 404s, the same
    depth test every parent check in this package repeats."""
    _login(client, "test:spaceadmin")
    slice_id = chesapeake["slices"]["J"].id

    resp = client.post(f"/spaces/room/{slice_id}/combine",
                       data={"event": "SMF2027"})

    assert resp.status_code == 404


def test_the_combine_route_requires_space_admin(client, chesapeake):
    resp = client.post(_combine_room_url(chesapeake),
                       data=_combine_form(chesapeake, K="J"))

    assert resp.status_code in (302, 403)
    assert db.session.query(SpaceEventOverride).count() == 0


def test_copy_layout_carries_a_permanent_folds_id_forward(client, chesapeake, admin):
    """A permanent slice keeps its id across events, so its fold needs no
    remapping; copy_layout must still carry the fold itself."""
    other = EventCycle(code="SMF2028", name="Super MAGFest 2028",
                       is_active=True, venue_id=chesapeake["venue"].id)
    db.session.add(other)
    db.session.commit()
    _fold(chesapeake, "K", "J")
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/copy-layout", data={
        "event": "SMF2028", "source_event": "SMF2027",
    }, follow_redirects=True)

    assert resp.status_code == 200
    copied = db.session.query(SpaceEventOverride).filter_by(
        space_id=chesapeake["slices"]["K"].id, event_cycle_id=other.id).one()
    assert copied.combined_into_space_id == chesapeake["slices"]["J"].id


# ============================================================
# Spec section 12's two named scenarios, exercised through the real route.
# ============================================================

@pytest.fixture
def maryland(app):
    """Maryland Ballroom: ten slices, matching spec 5.1/5.2's own mockup."""
    venue = Venue(code="GAYLORD_NAT", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()

    room = Space(venue_id=venue.id, name="Maryland Ballroom", code="MD",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()

    slices = {}
    for letter in ("A", "B", "C", "D", "1", "2", "3", "4", "5", "6"):
        s = Space(venue_id=venue.id, name=f"Maryland {letter}",
                  code=f"MD-{letter}", kind=SPACE_KIND_SLICE,
                  parent_id=room.id, area_sqft=500)
        db.session.add(s)
        slices[letter] = s
    db.session.commit()

    admin = User(id="test:spaceadmin", email="space@test.local",
                display_name="Space Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))
    db.session.commit()
    return {"venue": venue, "cycle": cycle, "room": room, "slices": slices}


def test_maryland_ballroom_renders_ten_slices_and_no_invented_rows(client, maryland):
    """Plan Task 2, Done when: Maryland Ballroom renders ten slices, three
    groupings, and no invented rows."""
    _login(client, "test:spaceadmin")
    room_id = maryland["room"].id
    ids = {letter: s.id for letter, s in maryland["slices"].items()}

    data = {"event": "SMF2027"}
    for slice_ in maryland["slices"].values():
        data[f"combined_into_{slice_.id}"] = ""
    for member in ("B", "D", "1", "2"):
        data[f"combined_into_{ids[member]}"] = str(ids["A"])
    data[f"combined_into_{ids['4']}"] = str(ids["3"])

    resp = client.post(f"/spaces/room/{room_id}/combine", data=data,
                       follow_redirects=True)
    assert resp.status_code == 200

    rows = build_space_rows(maryland["cycle"])
    slice_rows = [r for r in rows if r["space"].kind == SPACE_KIND_SLICE]
    assert len(slice_rows) == 10

    by_code = {r["space"].code: r for r in rows}
    assert sorted(by_code["MD-A"]["combined_codes"]) == [
        "MD-1", "MD-2", "MD-B", "MD-D"]
    assert by_code["MD-3"]["combined_codes"] == ["MD-4"]
    assert by_code["MD-C"]["combined_codes"] == []
    assert by_code["MD-5"]["combined_codes"] == []
    assert by_code["MD-6"]["combined_codes"] == []

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)
    slice_row_ids = re.findall(
        r'data-space-id="(\d+)"[^>]*data-parent-id="%d"' % room_id, body)
    assert len(slice_row_ids) == 10
    assert "<code>MD-C</code>" in body  # a standalone slice keeps its code


@pytest.fixture
def woodrow(app):
    """Woodrow Wilson: A and B stand alone, D points at C. Spec 3.3."""
    venue = Venue(code="GAYLORD_NAT", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()

    room = Space(venue_id=venue.id, name="Woodrow Wilson", code="WW",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()

    slices = {}
    for letter, area in (("A", 1200), ("B", 900), ("C", 2000), ("D", 1809)):
        s = Space(venue_id=venue.id, name=f"Woodrow Wilson {letter}",
                  code=f"WW-{letter}", kind=SPACE_KIND_SLICE,
                  parent_id=room.id, area_sqft=area)
        db.session.add(s)
        slices[letter] = s
    db.session.commit()

    admin = User(id="test:spaceadmin", email="space@test.local",
                display_name="Space Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))
    db.session.commit()
    return {"venue": venue, "cycle": cycle, "room": room, "slices": slices}


def test_woodrow_wilson_renders_a_b_and_c_plus_d_with_summed_area(client, woodrow):
    """Spec section 12: Woodrow Wilson renders A, B and C+D, with C
    showing the summed area."""
    _login(client, "test:spaceadmin")
    room_id = woodrow["room"].id
    ids = {letter: s.id for letter, s in woodrow["slices"].items()}

    data = {"event": "SMF2027"}
    for slice_ in woodrow["slices"].values():
        data[f"combined_into_{slice_.id}"] = ""
    data[f"combined_into_{ids['D']}"] = str(ids["C"])

    resp = client.post(f"/spaces/room/{room_id}/combine", data=data,
                       follow_redirects=True)
    assert resp.status_code == 200

    rows = build_space_rows(woodrow["cycle"])
    by_code = {r["space"].code: r for r in rows}
    assert by_code["WW-C"]["combined_area_sqft"] == 3809
    assert by_code["WW-C"]["combined_codes"] == ["WW-D"]
    assert by_code["WW-D"]["combined_into_space_id"] == ids["C"]
    assert by_code["WW-A"]["combined_into_space_id"] is None
    assert by_code["WW-A"]["combined_codes"] == []
    assert by_code["WW-B"]["combined_into_space_id"] is None

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)
    slice_row_ids = re.findall(
        r'data-space-id="(\d+)"[^>]*data-parent-id="%d"' % room_id, body)
    assert len(slice_row_ids) == 4
    # Whitespace-normalized: Jinja's own line breaks around a tag are not
    # part of what the operator reads as one phrase.
    text = re.sub(r"\s+", " ", html.unescape(body))
    assert "combined with WW-C" in text
    assert "+ WW-D" in text
    assert "3809 sq ft" in text
    assert "<code>WW-D</code>" not in body  # a folded slice shows no code
