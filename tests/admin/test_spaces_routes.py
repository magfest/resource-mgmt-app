"""Spaces page behavior.

Every test asserts a specific row renders. A spaces test with no venue
seeded renders an empty table and passes while proving nothing.
"""
import re

import pytest

from app import db
from app.models import (
    Department, EventCycle, ROLE_SPACE_ADMIN, Space, SpaceAssignment,
    SpaceEventOverride, SPACE_KIND_FREEFORM, SPACE_KIND_ROOM,
    SPACE_KIND_SLICE, User, UserRole, Venue,
)
from app.routes.spaces.catalog_parse import MAX_AREA_SQFT, MAX_CODE_LENGTH


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


@pytest.fixture
def world(app):
    """One venue, one event, a combo with two slices, a pop-up, two depts."""
    admin = User(id="test:spaceadmin", email="space@test.local",
                 display_name="Space Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))

    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    other = EventCycle(code="SMF2028", name="Super MAGFest 2028",
                       is_active=True, venue_id=venue.id)
    db.session.add_all([cycle, other])

    reg = Department(code="REG", name="Registration", is_active=True)
    staff = Department(code="STAFFOPS", name="Staff Ops", is_active=True)
    db.session.add_all([reg, staff])
    db.session.flush()

    room = Space(venue_id=venue.id, name="Maryland Ballroom", code="MD",
                 kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()

    slice_a = Space(venue_id=venue.id, name="Maryland Ballroom A", code="MD-A",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    slice_b = Space(venue_id=venue.id, name="Maryland Ballroom B", code="MD-B",
                    kind=SPACE_KIND_SLICE, parent_id=room.id)
    closed = Space(venue_id=venue.id, name="Expo Hall E", code="EX-E",
                   kind=SPACE_KIND_ROOM)
    popup = Space(venue_id=venue.id, name="Merch Popup", code="POP-1",
                  kind=SPACE_KIND_FREEFORM, event_cycle_id=cycle.id,
                  location_note="Outside the Expo Hall B doors")
    db.session.add_all([slice_a, slice_b, closed, popup])
    db.session.flush()

    # MD-B is shared; MD-A is named Regdesk A this event; EX-E is closed.
    db.session.add_all([
        SpaceAssignment(space_id=slice_a.id, event_cycle_id=cycle.id,
                        department_id=reg.id),
        SpaceAssignment(space_id=slice_b.id, event_cycle_id=cycle.id,
                        department_id=reg.id),
        SpaceAssignment(space_id=slice_b.id, event_cycle_id=cycle.id,
                        department_id=staff.id),
        SpaceEventOverride(space_id=slice_a.id, event_cycle_id=cycle.id,
                           alias="Regdesk A"),
        SpaceEventOverride(space_id=closed.id, event_cycle_id=cycle.id,
                           is_available=False,
                           unavailable_reason="Out of service"),
    ])
    db.session.commit()
    return {"cycle": cycle, "other": other, "venue": venue}


def test_the_page_lists_the_venue_rooms(client, world):
    _login(client, "test:spaceadmin")
    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert "Maryland Ballroom A" in body
    assert "MD-B" in body


def test_the_event_alias_replaces_the_venue_name(client, world):
    _login(client, "test:spaceadmin")
    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert "Regdesk A" in body


def test_an_unavailable_space_renders_the_pill_and_reason(client, world):
    """EX-E carries an unavailable override in the world fixture. The
    existing coverage checks the database only; this checks the pill and
    the reason actually reach the page."""
    _login(client, "test:spaceadmin")
    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert "Unavailable" in body
    assert "Out of service" in body


def test_a_shared_space_is_marked(client, world):
    _login(client, "test:spaceadmin")
    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert "Shared" in body
    assert "Staff Ops" in body


def test_a_popup_appears_only_in_its_own_event(client, world):
    _login(client, "test:spaceadmin")

    mine = client.get("/spaces/?event=SMF2027").get_data(as_text=True)
    assert "Merch Popup" in mine

    later = client.get("/spaces/?event=SMF2028").get_data(as_text=True)
    assert "Merch Popup" not in later
    assert "Maryland Ballroom A" in later


def test_a_slice_survives_its_parent_being_filtered_out(client, world):
    """Archiving a combo must not silently drop its slices from the page."""
    _login(client, "test:spaceadmin")
    combo = db.session.query(Space).filter_by(code="MD").one()
    combo.is_active = False
    db.session.commit()

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert "Maryland Ballroom A" in body
    assert "Maryland Ballroom B" in body
    assert body.count("Maryland Ballroom A") == 1


def test_an_unavailable_space_is_not_counted_as_unassigned(app, world):
    from app.routes.spaces.helpers import build_space_rows, build_space_stats

    rows = build_space_rows(world["cycle"])
    stats = build_space_stats(rows)

    # EX-E is unaccounted for but out of service, so it must not inflate the
    # count. MD is accounted for here because both its slices are assigned.
    codes = {r["space"].code for r in rows
             if not r["is_accounted_for"] and r["is_available"]}
    assert "EX-E" not in codes
    assert stats["unassigned"] == len(codes)


def _add_untouched_room(world, room_code="CHES"):
    """Add a room with two unassigned slices, apart from MD.

    MD's own slices are pre-assigned in `world` for the sharing tests, so
    checking coverage on an untouched room needs a separate room.
    """
    venue = world["venue"]
    room = Space(venue_id=venue.id, name="Chesapeake", code=room_code,
                 kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    slice_a = Space(venue_id=venue.id, name="Chesapeake A",
                    code=f"{room_code}-A", kind=SPACE_KIND_SLICE,
                    parent_id=room.id)
    slice_b = Space(venue_id=venue.id, name="Chesapeake B",
                    code=f"{room_code}-B", kind=SPACE_KIND_SLICE,
                    parent_id=room.id)
    db.session.add_all([slice_a, slice_b])
    db.session.commit()
    return room, slice_a, slice_b


def test_assigning_a_room_accounts_for_its_slices(app, world):
    """The motivating case: assigning Chesapeake A/B/C whole must not
    leave A, B and C sitting in the Unassigned card. The slices carry no
    assignment of their own, so only the parent rule can account for them.
    """
    from app.routes.spaces.helpers import build_space_rows

    room, slice_a, slice_b = _add_untouched_room(world)
    reg = db.session.query(Department).filter_by(code="REG").one()
    db.session.add(SpaceAssignment(space_id=room.id,
                                   event_cycle_id=world["cycle"].id,
                                   department_id=reg.id))
    db.session.commit()

    by_code = {r["space"].code: r for r in build_space_rows(world["cycle"])}

    assert by_code["CHES"]["is_accounted_for"] is True
    assert by_code["CHES-A"]["is_accounted_for"] is True
    assert by_code["CHES-B"]["is_accounted_for"] is True


def test_assigning_a_slice_accounts_for_its_room(app, world):
    """A room split across its slices has been dealt with, even though the
    room row itself holds no assignment."""
    from app.routes.spaces.helpers import build_space_rows

    room, slice_a, slice_b = _add_untouched_room(world)
    reg = db.session.query(Department).filter_by(code="REG").one()
    db.session.add(SpaceAssignment(space_id=slice_a.id,
                                   event_cycle_id=world["cycle"].id,
                                   department_id=reg.id))
    db.session.commit()

    by_code = {r["space"].code: r for r in build_space_rows(world["cycle"])}

    assert by_code["CHES"]["is_accounted_for"] is True
    assert by_code["CHES-B"]["is_accounted_for"] is False


def test_an_untouched_room_is_still_unassigned(app, world):
    """The rule must not account for everything. Without this, the card
    would read zero and prove nothing."""
    from app.routes.spaces.helpers import build_space_rows

    _add_untouched_room(world)

    by_code = {r["space"].code: r for r in build_space_rows(world["cycle"])}

    assert by_code["CHES"]["is_accounted_for"] is False


def test_the_filter_haystack_carries_the_per_event_alias(client, world):
    """The heading shows the venue name and the alias rides in a badge, so
    the alias reaches the filter only through data-haystack. Without it,
    typing a room's event name matches nothing."""
    _login(client, "test:spaceadmin")
    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    haystacks = re.findall(r'data-haystack="([^"]*)"', body)
    assert haystacks, "no rows rendered, so the assertion below proves nothing"
    assert any("regdesk a" in h.lower() for h in haystacks)


def test_shared_and_assigned_counts(app, world):
    from app.routes.spaces.helpers import build_space_rows, build_space_stats

    stats = build_space_stats(build_space_rows(world["cycle"]))

    assert stats["assigned"] == 2   # MD-A and MD-B
    assert stats["shared"] == 1     # MD-B only


def test_an_event_with_no_venue_renders_without_the_toolbar(client, world):
    """A new event cycle before its venue is set is the ordinary first-run
    state. The page must explain itself rather than render a bare table."""
    _login(client, "test:spaceadmin")
    cycle = db.session.query(EventCycle).filter_by(code="SMF2028").one()
    cycle.venue_id = None
    db.session.commit()

    resp = client.get("/spaces/?event=SMF2028")
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    # The script block always references 'space-filter' by id, so check for
    # the input element itself rather than the bare id string.
    assert 'id="space-filter"' not in body
    assert "no venue set" in body.lower()


def test_adding_a_popup_puts_it_on_the_page(client, world):
    """The event page's Add space no longer takes a Kind; every space it
    creates is a FREEFORM pop-up. Page A owns permanent rooms."""
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027",
        "name": "Chesapeake Hall Popup",
        "code": "CHES",
        "dimensions": "40x60x20",
    }, follow_redirects=True)

    assert resp.status_code == 200
    assert "Chesapeake Hall Popup" in resp.get_data(as_text=True)
    created = db.session.query(Space).filter_by(code="CHES").one()
    assert created.kind == SPACE_KIND_FREEFORM
    assert created.event_cycle_id == world["cycle"].id


def test_a_duplicate_code_at_one_venue_is_refused(client, world):
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027",
        "name": "Another Maryland",
        "code": "MD-A",
    }, follow_redirects=True)

    body = resp.get_data(as_text=True)
    assert "already exists" in body
    assert db.session.query(Space).filter_by(code="MD-A").count() == 1


def test_a_freeform_space_is_scoped_to_the_active_event(client, world):
    _login(client, "test:spaceadmin")

    client.post("/spaces/space/new", data={
        "event": "SMF2027",
        "name": "Photo Booth",
        "code": "POP-2",
        "location_note": "By the north escalators",
    }, follow_redirects=True)

    created = db.session.query(Space).filter_by(code="POP-2").one()
    assert created.event_cycle_id == world["cycle"].id
    assert created.kind == SPACE_KIND_FREEFORM


def test_the_add_popup_form_offers_no_kind_or_parent_picker(client, world):
    """Spec section 5: this page has no Add space for a permanent room or
    slice, so its form must not offer Kind or a parent room."""
    _login(client, "test:spaceadmin")

    body = client.get("/spaces/?event=SMF2027&add=1").get_data(as_text=True)

    assert 'name="kind"' not in body
    assert 'name="parent_id"' not in body
    assert 'name="location_note"' in body


def test_a_popup_name_colliding_with_a_permanent_space_is_refused(client, world):
    """Two rows reading the same name on one event page is what confuses
    an operator; see _popup_name_taken's asymmetry note."""
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027",
        "name": "Maryland Ballroom",
        "code": "POP-MD",
    }, follow_redirects=True)

    body = resp.get_data(as_text=True)
    assert "already exists" in body
    assert db.session.query(Space).filter_by(code="POP-MD").count() == 0


def test_a_popup_name_colliding_with_another_popup_is_refused(client, world):
    _login(client, "test:spaceadmin")

    client.post("/spaces/space/new", data={
        "event": "SMF2027", "name": "Merch Table", "code": "MERCH-A",
    }, follow_redirects=True)
    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027", "name": "Merch Table", "code": "MERCH-B",
    }, follow_redirects=True)

    body = resp.get_data(as_text=True)
    assert "already exists" in body
    assert db.session.query(Space).filter_by(code="MERCH-B").count() == 0


def test_an_over_length_popup_code_is_refused(client, world):
    """create_space never bounded code length before; a code this long
    used to be written silently, only failing on Postgres."""
    _login(client, "test:spaceadmin")
    long_code = "A" * (MAX_CODE_LENGTH + 1)

    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027", "name": "Merch Overflow", "code": long_code,
    }, follow_redirects=True)

    assert "too long" in resp.get_data(as_text=True)
    assert db.session.query(Space).filter_by(code=long_code).count() == 0


def test_a_popup_area_over_the_bound_is_refused(client, world):
    """area_sqft is a plain Integer; Postgres's int4 rejects a value past
    MAX_AREA_SQFT and SQLite silently stores it. Bound it in Python."""
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027", "name": "Huge Popup", "code": "POP-HUGE",
        "area_sqft": str(MAX_AREA_SQFT + 1),
    }, follow_redirects=True)

    assert "square feet or fewer" in resp.get_data(as_text=True)
    assert db.session.query(Space).filter_by(code="POP-HUGE").count() == 0


def test_an_oversized_parent_id_on_popup_create_does_not_500(client, world):
    """The pop-up form no longer takes a parent, so parent_id in the POST
    body (a stale bookmark, a crafted request) must be ignored outright
    rather than parsed with a bare int() and handed to db.session.get."""
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/space/new", data={
        "event": "SMF2027", "name": "Photo Booth 2", "code": "POP-PB2",
        "parent_id": str(2 ** 63),
    }, follow_redirects=True)

    assert resp.status_code == 200
    created = db.session.query(Space).filter_by(code="POP-PB2").one()
    assert created.parent_id is None


def _md_b(world):
    return db.session.query(Space).filter_by(code="MD-B").one()


def test_saving_plain_values_writes_no_override_row(client, world):
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD").one()

    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD",
        "name": space.name,
        "alias": "",
        "is_available": "1",
        "unavailable_reason": "",
    }, follow_redirects=True)

    assert db.session.query(SpaceEventOverride).filter_by(
        space_id=space.id, event_cycle_id=world["cycle"].id).count() == 0


def test_setting_an_alias_creates_the_override(client, world):
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD").one()

    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD",
        "name": space.name,
        "alias": "Consoles",
        "is_available": "1",
    }, follow_redirects=True)

    override = db.session.query(SpaceEventOverride).filter_by(
        space_id=space.id, event_cycle_id=world["cycle"].id).one()
    assert override.alias == "Consoles"


def test_clearing_an_alias_removes_the_override(client, world):
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-A").one()

    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD-A",
        "name": space.name,
        "alias": "",
        "is_available": "1",
    }, follow_redirects=True)

    assert db.session.query(SpaceEventOverride).filter_by(
        space_id=space.id, event_cycle_id=world["cycle"].id).count() == 0


def test_assignments_are_added_and_removed_to_match_the_form(client, world):
    _login(client, "test:spaceadmin")
    space = _md_b(world)
    reg = db.session.query(Department).filter_by(code="REG").one()

    # MD-B starts shared between REG and STAFFOPS. Submit only REG.
    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD-B",
        "name": space.name,
        "is_available": "1",
        "department_ids": [str(reg.id)],
    }, follow_redirects=True)

    remaining = db.session.query(SpaceAssignment).filter_by(
        space_id=space.id, event_cycle_id=world["cycle"].id).all()
    assert [a.department_id for a in remaining] == [reg.id]


def test_assigning_a_combo_leaves_its_slices_alone(client, world):
    """Overlap handling is out of scope. Without this guard a maintainer
    reads the behavior as a bug and 'fixes' it."""
    _login(client, "test:spaceadmin")
    combo = db.session.query(Space).filter_by(code="MD").one()
    reg = db.session.query(Department).filter_by(code="REG").one()
    slice_a = db.session.query(Space).filter_by(code="MD-A").one()

    client.post(f"/spaces/space/{combo.id}", data={
        "event": "SMF2027",
        "code": "MD",
        "name": combo.name,
        "is_available": "1",
        "department_ids": [str(reg.id)],
    }, follow_redirects=True)

    # MD-A keeps exactly the one assignment the fixture gave it.
    assigned = db.session.query(SpaceAssignment).filter_by(
        space_id=slice_a.id, event_cycle_id=world["cycle"].id).all()
    assert len(assigned) == 1


def test_marking_a_space_unavailable_stores_the_reason(client, world):
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD").one()

    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD",
        "name": space.name,
        "unavailable_reason": "Renovation",
    }, follow_redirects=True)

    override = db.session.query(SpaceEventOverride).filter_by(
        space_id=space.id, event_cycle_id=world["cycle"].id).one()
    assert override.is_available is False
    assert override.unavailable_reason == "Renovation"


def test_a_reason_does_not_survive_the_space_becoming_available(client, world):
    """A reason left on an available space is stale data that misleads
    whoever reads the row next."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="EX-E").one()

    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "EX-E",
        "name": space.name,
        "alias": "Main Registration",
        "is_available": "1",
        "unavailable_reason": "Out of service",
    }, follow_redirects=True)

    override = db.session.query(SpaceEventOverride).filter_by(
        space_id=space.id, event_cycle_id=world["cycle"].id).one()
    assert override.is_available is True
    assert override.unavailable_reason is None


def test_an_unchanged_assignment_keeps_its_row(client, world):
    """Delete-all-then-reinsert would churn row ids and audit history, so
    an assignment nobody touched must survive with the same id."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-B").one()
    reg = db.session.query(Department).filter_by(code="REG").one()
    original_id = db.session.query(SpaceAssignment).filter_by(
        space_id=space.id, department_id=reg.id).one().id

    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD-B",
        "name": space.name,
        "is_available": "1",
        "department_ids": [str(reg.id)],
    }, follow_redirects=True)

    kept = db.session.query(SpaceAssignment).filter_by(
        space_id=space.id, department_id=reg.id).one()
    assert kept.id == original_id


def test_saving_a_space_from_another_venue_is_refused(client, world):
    """A crafted POST for a space at an unrelated venue must not write
    rows the event's own list page can never show."""
    _login(client, "test:spaceadmin")
    other_venue = Venue(code="OTHER_VENUE", name="Other Venue")
    db.session.add(other_venue)
    db.session.flush()
    stray = Space(venue_id=other_venue.id, name="Stray Room", code="STRAY",
                  kind=SPACE_KIND_ROOM)
    db.session.add(stray)
    db.session.commit()

    reg = db.session.query(Department).filter_by(code="REG").one()
    resp = client.post(f"/spaces/space/{stray.id}", data={
        "event": "SMF2027",
        "code": "STRAY",
        "is_available": "1",
        "department_ids": [str(reg.id)],
    })

    assert resp.status_code == 404
    assert db.session.query(SpaceAssignment).filter_by(
        space_id=stray.id).count() == 0


def test_archiving_a_space_from_another_venue_is_refused(client, world):
    """The venue guard on save_space has no sibling on archive/restore; a
    crafted POST could archive a room that is not on this event's page."""
    _login(client, "test:spaceadmin")
    other_venue = Venue(code="OTHER_VENUE", name="Other Venue")
    db.session.add(other_venue)
    db.session.flush()
    stray = Space(venue_id=other_venue.id, name="Stray Room", code="STRAY",
                  kind=SPACE_KIND_ROOM)
    db.session.add(stray)
    db.session.commit()

    resp = client.post(f"/spaces/space/{stray.id}/archive",
                       data={"event": "SMF2027"})

    assert resp.status_code == 404
    db.session.refresh(stray)
    assert stray.is_active is True


def test_archiving_a_space_hides_it_and_restoring_brings_it_back(client, world):
    """Restore is asserted against the default view, not show_archived.
    The archived view skips the is_active filter, so it would pass even if
    restore never wrote anything."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="EX-E").one()

    client.post(f"/spaces/space/{space.id}/archive",
                data={"event": "SMF2027"}, follow_redirects=True)
    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)
    assert "Expo Hall E" not in body

    archived_view = client.get(
        "/spaces/?event=SMF2027&show_archived=1").get_data(as_text=True)
    assert "Expo Hall E" in archived_view

    client.post(f"/spaces/space/{space.id}/restore",
                data={"event": "SMF2027"}, follow_redirects=True)
    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)
    assert "Expo Hall E" in body


def test_an_archived_row_is_marked_in_the_archived_view(client, world):
    """Archived and unavailable are different states. Without a pill the
    archived view renders them identically."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="EX-E").one()
    space.is_active = False
    db.session.commit()

    body = client.get(
        "/spaces/?event=SMF2027&show_archived=1").get_data(as_text=True)

    assert "Archived" in body


def test_archive_is_not_offered_on_the_row_itself(client, world):
    """Spec section 5: the event page has no per-row Archive. It is rare
    and hard to undo, so it moved one click further away, inside the
    editor, matching Page A's own catalog row."""
    _login(client, "test:spaceadmin")

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert "/archive" not in body
    assert "/restore" not in body


def test_archive_control_is_inside_the_row_editor(client, world):
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD").one()

    body = client.get(
        f"/spaces/?event=SMF2027&edit={space.id}").get_data(as_text=True)

    assert f'/spaces/space/{space.id}/archive"' in body


def test_an_archived_row_still_offers_a_way_back_to_restore_it(client, world):
    """Archive moving off the row means Edit is the only path back to the
    editor's Restore button. Edit cannot stay hidden for an archived row,
    or moving Archive would strand every archived space."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="EX-E").one()
    space.is_active = False
    db.session.commit()

    list_body = client.get(
        "/spaces/?event=SMF2027&show_archived=1").get_data(as_text=True)
    assert f"edit={space.id}" in list_body

    edit_body = client.get(
        f"/spaces/?event=SMF2027&show_archived=1&edit={space.id}"
    ).get_data(as_text=True)
    assert "Restore" in edit_body


def test_a_space_admin_can_add_a_venue(client, world):
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/venues/new", data={
        "code": "MWH",
        "name": "MAGWest Hall",
    }, follow_redirects=True)

    assert resp.status_code == 200
    assert "MAGWest Hall" in resp.get_data(as_text=True)
    assert db.session.query(Venue).filter_by(code="MWH").count() == 1


def test_a_space_admin_can_rename_a_venue(client, world):
    """The posted code is deliberately wrong. A venue code is fixed after
    creation, and update_venue must never read one."""
    _login(client, "test:spaceadmin")
    venue = db.session.query(Venue).filter_by(code="GLN").one()

    resp = client.post(f"/spaces/venues/{venue.id}", data={
        "name": "Gaylord National Resort",
        "notes": "Primary Super MAGFest venue",
        "is_active": "1",
        "code": "SOMETHING_ELSE",
    }, follow_redirects=True)

    assert resp.status_code == 200
    db.session.refresh(venue)
    assert venue.name == "Gaylord National Resort"
    assert venue.code == "GLN"


def test_a_duplicate_venue_code_is_refused(client, world):
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/venues/new", data={
        "code": "GLN",
        "name": "A second Gaylord",
    }, follow_redirects=True)

    assert "already exists" in resp.get_data(as_text=True)
    assert db.session.query(Venue).filter_by(code="GLN").count() == 1


def test_a_spaces_post_succeeds_with_csrf_enabled():
    """Every other test in this file runs with WTF_CSRF_ENABLED off (see
    tests/conftest.py), which is why 756 tests passed while all five spaces
    forms were missing their token. This builds its own app with CSRF on,
    instead of using the shared `app`/`client` fixtures, so it actually
    exercises the check those fixtures turn off.
    """
    from app import create_app

    test_app = create_app()
    test_app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "DEV_LOGIN_ENABLED": False,
        "SECRET_KEY": "test-secret-key",
    })

    with test_app.app_context():
        db.create_all()
        try:
            admin = User(id="test:spaceadmin-csrf", email="space-csrf@test.local",
                        display_name="Space Admin", is_active=True)
            db.session.add(admin)
            db.session.flush()
            db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))

            venue = Venue(code="CSRF_VENUE", name="CSRF Venue")
            db.session.add(venue)
            db.session.flush()

            cycle = EventCycle(code="CSRFEVT", name="CSRF Event",
                               is_active=True, is_default=True, venue_id=venue.id)
            db.session.add(cycle)
            db.session.commit()

            test_client = test_app.test_client()
            with test_client.session_transaction() as sess:
                sess["active_user_id"] = admin.id

            page = test_client.get("/spaces/?event=CSRFEVT&add=1")
            token = re.search(
                r'name="csrf_token" value="([^"]+)"',
                page.get_data(as_text=True),
            ).group(1)

            resp = test_client.post("/spaces/space/new", data={
                "csrf_token": token,
                "event": "CSRFEVT",
                "name": "CSRF Room",
                "code": "CSRF-1",
            })

            assert resp.status_code != 400
            assert db.session.query(Space).filter_by(code="CSRF-1").count() == 1
        finally:
            db.session.remove()
            db.drop_all()


def test_a_catalog_post_succeeds_with_csrf_enabled():
    """The catalog's write routes live in catalog.py, which the test above
    does not reach. A token missing from the catalog forms would return 400
    in a browser and pass the whole suite, which is how it shipped once.
    Covers all three of the catalog's write routes: the save and archive
    forms only render under ?edit=<id> and are easy to miss. Flask-WTF
    tokens are session-bound, so one token works for all three posts.
    """
    from app import create_app

    test_app = create_app()
    test_app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "DEV_LOGIN_ENABLED": False,
        "SECRET_KEY": "test-secret-key",
    })

    with test_app.app_context():
        db.create_all()
        try:
            admin = User(id="test:catadmin-csrf", email="cat-csrf@test.local",
                         display_name="Space Admin", is_active=True)
            db.session.add(admin)
            db.session.flush()
            db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))

            venue = Venue(code="CAT", name="CSRF Catalog Venue")
            db.session.add(venue)
            db.session.commit()

            test_client = test_app.test_client()
            with test_client.session_transaction() as sess:
                sess["active_user_id"] = admin.id

            page = test_client.get(f"/spaces/venues/{venue.id}/?add=1")
            token = re.search(
                r'name="csrf_token" value="([^"]+)"',
                page.get_data(as_text=True),
            ).group(1)

            add_resp = test_client.post(
                f"/spaces/venues/{venue.id}/space/new", data={
                    "csrf_token": token,
                    "name": "CSRF Catalog Room",
                    "code": "CAT-1",
                })
            assert add_resp.status_code == 302
            space = db.session.query(Space).filter_by(code="CAT-1").one()

            edit_page = test_client.get(
                f"/spaces/venues/{venue.id}/?edit={space.id}")
            edit_token = re.search(
                r'name="csrf_token" value="([^"]+)"',
                edit_page.get_data(as_text=True),
            ).group(1)

            save_resp = test_client.post(
                f"/spaces/venues/{venue.id}/space/{space.id}", data={
                    "csrf_token": edit_token,
                    "name": "CSRF Catalog Room, Renamed",
                    "code": "CAT-1",
                })
            assert save_resp.status_code == 302
            db.session.refresh(space)
            assert space.name == "CSRF Catalog Room, Renamed"

            archive_resp = test_client.post(
                f"/spaces/venues/{venue.id}/space/{space.id}/archive",
                data={"csrf_token": edit_token})
            assert archive_resp.status_code == 302
            db.session.refresh(space)
            assert space.is_active is False
        finally:
            db.session.remove()
            db.drop_all()


def test_a_catalog_paste_confirm_succeeds_with_csrf_enabled():
    """catalog_paste_confirm lives in catalog.py alongside the three
    routes the test above covers, but is its own POST route and a missing
    token there would pass the whole suite the same way it did before.
    """
    from app import create_app

    test_app = create_app()
    test_app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "DEV_LOGIN_ENABLED": False,
        "SECRET_KEY": "test-secret-key",
    })

    with test_app.app_context():
        db.create_all()
        try:
            admin = User(id="test:catconfirm-csrf", email="catconfirm-csrf@test.local",
                         display_name="Space Admin", is_active=True)
            db.session.add(admin)
            db.session.flush()
            db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))

            venue = Venue(code="CATC", name="CSRF Confirm Venue")
            db.session.add(venue)
            db.session.commit()

            test_client = test_app.test_client()
            with test_client.session_transaction() as sess:
                sess["active_user_id"] = admin.id

            page = test_client.get(f"/spaces/venues/{venue.id}/?paste=1")
            token = re.search(
                r'name="csrf_token" value="([^"]+)"',
                page.get_data(as_text=True),
            ).group(1)

            confirm_resp = test_client.post(
                f"/spaces/venues/{venue.id}/paste/confirm", data={
                    "csrf_token": token,
                    "row_count": "1",
                    "create_0": "1",
                    "name_0": "CSRF Confirm Room",
                    "code_0": "CSRFC-1",
                })
            assert confirm_resp.status_code != 400
            assert db.session.query(Space).filter_by(code="CSRFC-1").count() == 1
        finally:
            db.session.remove()
            db.drop_all()


def test_a_repair_slices_apply_succeeds_with_csrf_enabled():
    """catalog_repair_slices_apply is its own POST route, added alongside
    catalog_paste_confirm; a missing token here would pass the whole
    suite and return 400 in a browser, the same way it did once before.
    """
    from app import create_app

    test_app = create_app()
    test_app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "DEV_LOGIN_ENABLED": False,
        "SECRET_KEY": "test-secret-key",
    })

    with test_app.app_context():
        db.create_all()
        try:
            admin = User(id="test:repaircsrf", email="repair-csrf@test.local",
                         display_name="Space Admin", is_active=True)
            db.session.add(admin)
            db.session.flush()
            db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))

            venue = Venue(code="RPR", name="CSRF Repair Venue")
            db.session.add(venue)
            db.session.flush()

            room = Space(venue_id=venue.id, name="Maryland Ballroom",
                        code="MDB-P", kind=SPACE_KIND_ROOM)
            orphan = Space(venue_id=venue.id, name="Maryland 1",
                           code="MDB-S-1", kind=SPACE_KIND_ROOM)
            db.session.add_all([room, orphan])
            db.session.commit()

            test_client = test_app.test_client()
            with test_client.session_transaction() as sess:
                sess["active_user_id"] = admin.id

            page = test_client.get(f"/spaces/venues/{venue.id}/repair-slices")
            token = re.search(
                r'name="csrf_token" value="([^"]+)"',
                page.get_data(as_text=True),
            ).group(1)

            apply_resp = test_client.post(
                f"/spaces/venues/{venue.id}/repair-slices", data={
                    "csrf_token": token,
                    f"apply_{orphan.id}": "1",
                    f"parent_{orphan.id}": str(room.id),
                })
            assert apply_resp.status_code != 400
            db.session.refresh(orphan)
            assert orphan.parent_id == room.id
            assert orphan.kind == SPACE_KIND_SLICE
        finally:
            db.session.remove()
            db.drop_all()


def test_child_rows_carry_their_parent_for_collapsing(client, world):
    """Collapse is client-side, so the server's job is to say which row
    belongs to which. Without JavaScript every row stays visible."""
    _login(client, "test:spaceadmin")
    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    combo = db.session.query(Space).filter_by(code="MD").one()
    slice_a = db.session.query(Space).filter_by(code="MD-A").one()

    assert f'data-space-id="{combo.id}"' in body
    assert f'data-parent-id="{combo.id}"' in body
    assert f'data-space-id="{slice_a.id}"' in body


def test_an_orphaned_slice_carries_no_parent_for_collapsing(client, world):
    """A slice whose room was archived renders at top level. A parent id
    would let collapse hide a row sitting under no visible room."""
    _login(client, "test:spaceadmin")
    room = db.session.query(Space).filter_by(code="MD").one()
    slice_a = db.session.query(Space).filter_by(code="MD-A").one()
    room.is_active = False
    db.session.commit()

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert f'data-space-id="{slice_a.id}"' in body
    assert f'data-parent-id="{room.id}"' not in body


def test_a_space_name_can_be_corrected(client, world):
    """Section 8 of the spec asks for 145 rows of hand entry. A typo that
    can only be fixed by archiving and re-entering is a blocker."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-A").one()

    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD-A",
        "name": "Maryland Ballroom A",
        "dimensions": "99x54x28.5",
        "area_sqft": "5427",
        "is_available": "1",
    }, follow_redirects=True)

    db.session.refresh(space)
    assert space.name == "Maryland Ballroom A"
    assert space.dimensions == "99x54x28.5"
    assert space.area_sqft == 5427


def test_a_blank_name_tells_the_user_it_was_refused(client, world):
    """Silently keeping the old name and flashing success sends someone
    away believing a typo was fixed."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-A").one()
    original = space.name

    resp = client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD-A",
        "name": "   ",
        "is_available": "1",
    }, follow_redirects=True)

    db.session.refresh(space)
    assert space.name == original
    assert "needs a name" in resp.get_data(as_text=True).lower()


def test_an_over_length_name_is_refused(client, world):
    """Space.name is String(128). SQLite does not enforce that, so
    without this check the failure appears only on Postgres."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-A").one()
    original = space.name

    resp = client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD-A",
        "name": "x" * 129,
        "is_available": "1",
    }, follow_redirects=True)

    db.session.refresh(space)
    assert space.name == original
    assert "too long" in resp.get_data(as_text=True).lower()


def test_the_alias_label_says_what_the_field_is_for(client, world):
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-A").one()

    body = client.get(
        f"/spaces/?event=SMF2027&edit={space.id}").get_data(as_text=True)

    assert "called at this event" in body
    assert "Name this event" not in body


def test_the_department_picker_summarises_and_filters(client, world):
    """70 departments make an unfiltered checkbox list unusable, and the
    current assignment has to read without scrolling past all of them."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-B").one()

    body = client.get(
        f"/spaces/?event=SMF2027&edit={space.id}").get_data(as_text=True)

    assert 'data-dept-picker' in body
    assert 'id="dept-select-' in body
    # MD-B is shared between Registration and Staff Ops in the fixture, and
    # both must appear in the summary line above the picker.
    assert "Registration, Staff Ops" in body or "Staff Ops, Registration" in body


def test_the_department_picker_renders_a_select_not_checkboxes(client, world):
    """Spec 5.3: the server renders a plain <select multiple>, not a
    checkbox per department. Scripting off still assigns from it."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-B").one()
    reg = db.session.query(Department).filter_by(code="REG").one()
    staff = db.session.query(Department).filter_by(code="STAFFOPS").one()

    body = client.get(
        f"/spaces/?event=SMF2027&edit={space.id}").get_data(as_text=True)

    assert '<select multiple name="department_ids"' in body
    assert 'type="checkbox" name="department_ids"' not in body
    assert f'<option value="{reg.id}" selected>' in body
    assert f'<option value="{staff.id}" selected>' in body


def test_the_department_select_lists_every_department_without_a_row_each(client, world):
    """The 70 must never render as 70 rows; the select just carries every
    department as an option, however many there are."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD-B").one()
    db.session.add_all([
        Department(code=f"D{i}", name=f"Dept {i}", is_active=True)
        for i in range(68)
    ])
    db.session.commit()
    total_depts = db.session.query(Department).filter_by(is_active=True).count()
    assert total_depts == 70

    body = client.get(
        f"/spaces/?event=SMF2027&edit={space.id}").get_data(as_text=True)

    match = re.search(
        r'<select multiple name="department_ids"[^>]*>(.*?)</select>',
        body, re.DOTALL)
    assert match, "no department select found"
    assert len(re.findall(r'<option', match.group(1))) == total_depts
    assert 'type="checkbox" name="department_ids"' not in body


def test_department_ids_post_still_assigns_through_the_select(client, world):
    """The posted field name is unchanged by the select swap, so a plain
    POST of department_ids (what the select submits with scripting off,
    and what the chip script's select stays in sync with) still assigns."""
    _login(client, "test:spaceadmin")
    space = db.session.query(Space).filter_by(code="MD").one()
    reg = db.session.query(Department).filter_by(code="REG").one()

    client.post(f"/spaces/space/{space.id}", data={
        "event": "SMF2027",
        "code": "MD",
        "name": space.name,
        "is_available": "1",
        "department_ids": [str(reg.id)],
    }, follow_redirects=True)

    assigned = db.session.query(SpaceAssignment).filter_by(
        space_id=space.id, event_cycle_id=world["cycle"].id).all()
    assert [a.department_id for a in assigned] == [reg.id]


def test_saving_a_space_from_event_a_with_event_b_is_refused(client, world):
    """The pop-up belongs to SMF2027. A stale tab posting event=SMF2028
    must not write a 2028 row against a 2027 space; neither page can show
    it afterward."""
    _login(client, "test:spaceadmin")
    popup = db.session.query(Space).filter_by(code="POP-1").one()
    reg = db.session.query(Department).filter_by(code="REG").one()

    resp = client.post(f"/spaces/space/{popup.id}", data={
        "event": "SMF2028",
        "code": "POP-1",
        "name": popup.name,
        "is_available": "1",
        "department_ids": [str(reg.id)],
    })

    assert resp.status_code == 404
    assert db.session.query(SpaceAssignment).filter_by(
        space_id=popup.id, event_cycle_id=world["other"].id).count() == 0
    assert db.session.query(SpaceEventOverride).filter_by(
        space_id=popup.id, event_cycle_id=world["other"].id).count() == 0


def _row_unassigned(body, space_id):
    """Pull data-unassigned off the <tr> for one space id."""
    match = re.search(r'data-space-id="%d"[^>]*data-unassigned="(\d)"'
                      % space_id, body)
    assert match, f"no row found for space {space_id}"
    return match.group(1)


def test_the_unassigned_attribute_matches_the_accounted_for_rule(client, world):
    """The card counts is_accounted_for; the row attribute used to check
    `not departments` instead. Assigning a room whole is the case that
    split them: the card read 0 while a slice still showed unassigned."""
    _login(client, "test:spaceadmin")
    room, slice_a, slice_b = _add_untouched_room(world)
    reg = db.session.query(Department).filter_by(code="REG").one()
    db.session.add(SpaceAssignment(space_id=room.id,
                                   event_cycle_id=world["cycle"].id,
                                   department_id=reg.id))
    db.session.commit()

    body = client.get("/spaces/?event=SMF2027").get_data(as_text=True)

    assert _row_unassigned(body, slice_a.id) == "0"
    assert _row_unassigned(body, slice_b.id) == "0"


def test_copying_a_layout_brings_popups_and_aliases_not_assignments(client, world):
    """Assignments never copy: a copied one makes the page look finished
    and hides the rooms whose owner should have changed this year."""
    _login(client, "test:spaceadmin")

    client.post("/spaces/copy-layout", data={
        "event": "SMF2028",
        "source_event": "SMF2027",
    }, follow_redirects=True)

    popup = db.session.query(Space).filter_by(
        code="POP-1", event_cycle_id=world["other"].id).one()
    assert popup.kind == SPACE_KIND_FREEFORM

    slice_a = db.session.query(Space).filter_by(code="MD-A").one()
    alias = db.session.query(SpaceEventOverride).filter_by(
        space_id=slice_a.id, event_cycle_id=world["other"].id).one()
    assert alias.alias == "Regdesk A"
    assert alias.is_available is True

    assert db.session.query(SpaceAssignment).filter_by(
        event_cycle_id=world["other"].id).count() == 0


def test_an_availability_only_override_is_not_copied(client, world):
    """A room out of service last year is not assumed to be out of service
    this year, and the override table stays sparse."""
    _login(client, "test:spaceadmin")

    client.post("/spaces/copy-layout", data={
        "event": "SMF2028",
        "source_event": "SMF2027",
    }, follow_redirects=True)

    closed = db.session.query(Space).filter_by(code="EX-E").one()
    assert db.session.query(SpaceEventOverride).filter_by(
        space_id=closed.id, event_cycle_id=world["other"].id).count() == 0
