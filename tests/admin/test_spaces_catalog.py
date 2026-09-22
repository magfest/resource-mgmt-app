"""The venue catalog page.

This page is wave one: one person enters a venue's rooms and slices once.
It knows nothing about events, departments or combining.
"""
import html
import re

import pytest

from app import db
from app.models import (
    EventCycle, ROLE_SPACE_ADMIN, Space, SPACE_KIND_ROOM, SPACE_KIND_SLICE,
    User, UserRole, Venue,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


@pytest.fixture
def catalog(app):
    """One venue, one room with two slices, one standalone room."""
    admin = User(id="test:spaceadmin", email="space@test.local",
                 display_name="Space Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))

    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    room = Space(venue_id=venue.id, name="Chesapeake G/H/I",
                 code="CHE-GHI", kind=SPACE_KIND_ROOM,
                 dimensions="29x86x14", area_sqft=2453)
    solo = Space(venue_id=venue.id, name="Fort Washington Boardroom",
                 code="FTW", kind=SPACE_KIND_ROOM)
    db.session.add_all([room, solo])
    db.session.flush()

    for letter in ("G", "H"):
        db.session.add(Space(venue_id=venue.id, name=f"Chesapeake {letter}",
                             code=f"CHE-{letter}", kind=SPACE_KIND_SLICE,
                             parent_id=room.id))
    db.session.commit()
    return {"venue": venue, "room": room, "solo": solo}


def test_the_catalog_lists_the_venues_rooms(client, catalog):
    _login(client, "test:spaceadmin")
    slice_g = db.session.query(Space).filter_by(code="CHE-G").one()
    body = client.get(
        f"/spaces/venues/{catalog['venue'].id}/").get_data(as_text=True)

    assert "Chesapeake G/H/I" in body
    assert "Fort Washington Boardroom" in body
    # "CHE-G" alone is a substring of the parent room's own code CHE-GHI, so
    # it would pass even if slice rows never rendered. Assert on the slice
    # row's own id instead.
    assert f'data-space-id="{slice_g.id}"' in body


def test_slices_render_beneath_their_room(app, catalog):
    from app.routes.spaces.catalog import build_catalog_rows

    rows = build_catalog_rows(catalog["venue"])
    codes = [r["space"].code for r in rows]

    assert codes.index("CHE-GHI") < codes.index("CHE-G")
    assert [r["depth"] for r in rows if r["space"].code == "CHE-G"] == [1]
    assert [r["depth"] for r in rows if r["space"].code == "FTW"] == [0]


def test_a_room_lists_the_codes_it_is_made_of(app, catalog):
    from app.routes.spaces.catalog import build_catalog_rows

    rows = build_catalog_rows(catalog["venue"])
    room = next(r for r in rows if r["space"].code == "CHE-GHI")

    assert sorted(room["child_codes"]) == ["CHE-G", "CHE-H"]
    assert room["has_children"] is True


def test_the_catalog_carries_no_event_controls(client, catalog):
    """Wave one has no events. A department or alias control here means the
    two pages have started merging again. Checked on the plain page and on
    the two panels that could grow such a control: Add space and the row
    editor."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    urls = [
        f"/spaces/venues/{venue.id}/",
        f"/spaces/venues/{venue.id}/?add=1",
        f"/spaces/venues/{venue.id}/?edit={catalog['solo'].id}",
    ]

    for url in urls:
        body = client.get(url).get_data(as_text=True)
        # Assert on the controls themselves. The shared top nav renders the
        # word "department" nine times, so a bare substring check would
        # fail for a reason that has nothing to do with this page.
        assert 'name="department_ids"' not in body, url
        assert 'name="alias"' not in body, url
        assert "called at this event" not in body, url


def test_a_venue_code_must_be_three_letters(client, catalog):
    _login(client, "test:spaceadmin")

    resp = client.post("/spaces/venues/new", data={
        "code": "GAYLORD",
        "name": "Too long",
    }, follow_redirects=True)

    assert "three letters" in resp.get_data(as_text=True).lower()
    assert db.session.query(Venue).filter_by(code="GAYLORD").count() == 0


def test_adding_a_room_puts_it_in_the_catalog(client, catalog):
    """Assert on the created row, not the flash message alone: a flash
    renders on the redirect target whether or not the row itself did."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Cherry Blossom Ballroom",
        "code": "CBB",
        "dimensions": "116x71x20",
        "area_sqft": "7957",
    }, follow_redirects=True)

    assert resp.status_code == 200
    space = db.session.query(Space).filter_by(code="CBB").one()
    assert space.name == "Cherry Blossom Ballroom"
    assert f'data-space-id="{space.id}"' in resp.get_data(as_text=True)


def test_a_space_with_no_parent_is_a_room(client, catalog):
    """Kind is not its own field. Left on the parent picker's default of
    "none, this is a room", the space is a room."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Solo Room",
        "code": "SOLO",
    }, follow_redirects=True)

    space = db.session.query(Space).filter_by(code="SOLO").one()
    assert space.kind == SPACE_KIND_ROOM
    assert space.parent_id is None


def test_a_space_with_a_parent_is_a_slice(client, catalog):
    """Choosing a parent room on the picker makes the space a slice of it;
    there is no separate kind choice to contradict that."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    room = catalog["room"]

    client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Chesapeake I",
        "code": "CHE-I",
        "parent_id": str(room.id),
    }, follow_redirects=True)

    space = db.session.query(Space).filter_by(code="CHE-I").one()
    assert space.kind == SPACE_KIND_SLICE
    assert space.parent_id == room.id


def test_a_rejected_add_keeps_what_was_typed(client, catalog):
    """A validation failure re-renders the panel instead of redirecting, so
    a redirect never gets the chance to drop the submission."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Duplicate Code Room",
        "code": "CHE-GHI",  # already used by catalog["room"]
    })

    body = resp.get_data(as_text=True)
    # Jinja escapes the quotes around the code, so match the decoded text.
    assert "code 'CHE-GHI' already exists" in html.unescape(body)
    assert 'id="add-space"' in body
    assert 'value="Duplicate Code Room"' in body
    assert db.session.query(Space).filter_by(name="Duplicate Code Room").count() == 0


def test_a_rejected_save_keeps_what_was_typed(client, catalog):
    """The save re-render is the more fragile of the two: it depends on
    edit_id and on the row surviving the show_archived filter, not only on
    form_values being truthy."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    space = db.session.query(Space).filter_by(code="CHE-G").one()

    resp = client.post(f"/spaces/venues/{venue.id}/space/{space.id}", data={
        "name": "Renamed But Rejected",
        "code": "CHE-H",  # already used by the other slice
    })

    body = resp.get_data(as_text=True)
    assert "code 'CHE-H' already exists" in html.unescape(body)
    assert f'id="edit-name-{space.id}"' in body
    assert 'value="Renamed But Rejected"' in body
    db.session.refresh(space)
    assert space.name != "Renamed But Rejected"


def test_a_duplicate_name_is_rejected(client, catalog):
    """Two rooms at one venue cannot share a name, mirroring the code rule."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Fort Washington Boardroom",  # already used by catalog["solo"]
        "code": "DUPNAME",
    })

    body = resp.get_data(as_text=True)
    assert ("named 'Fort Washington Boardroom' already exists"
            in html.unescape(body))
    assert 'id="add-space"' in body
    assert 'value="DUPNAME"' in body
    assert db.session.query(Space).filter_by(code="DUPNAME").count() == 0


def test_a_duplicate_name_is_rejected_case_and_whitespace_insensitive(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "  fort   washington    boardroom  ",
        "code": "DUPNAME2",
    })

    assert "already exists" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(code="DUPNAME2").count() == 0


def test_an_archived_spaces_name_still_blocks(client, catalog):
    """Restoring an archived space would otherwise collide with a name
    reused while it was hidden."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    solo = catalog["solo"]

    solo.is_active = False
    db.session.commit()

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Fort Washington Boardroom",
        "code": "DUPNAME3",
    })

    assert "already exists" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(code="DUPNAME3").count() == 0


def test_the_same_name_is_allowed_at_a_different_venue(client, catalog):
    _login(client, "test:spaceadmin")
    other_venue = Venue(code="OTH", name="Other Venue")
    db.session.add(other_venue)
    db.session.commit()

    resp = client.post(f"/spaces/venues/{other_venue.id}/space/new", data={
        "name": "Fort Washington Boardroom",
        "code": "FTW2",
    }, follow_redirects=True)

    assert resp.status_code == 200
    assert db.session.query(Space).filter_by(
        venue_id=other_venue.id, code="FTW2").count() == 1


def test_saving_a_space_without_changing_its_name_is_allowed(client, catalog):
    """The name check must exclude the space being saved, or every save
    would collide with itself."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    space = db.session.query(Space).filter_by(code="CHE-G").one()

    resp = client.post(f"/spaces/venues/{venue.id}/space/{space.id}", data={
        "name": space.name,
        "code": space.code,
        "dimensions": "29x28x14",
        "area_sqft": "811",
    }, follow_redirects=True)

    assert resp.status_code == 200
    db.session.refresh(space)
    assert space.area_sqft == 811
    assert "already exists" not in resp.get_data(as_text=True).lower()


def test_a_negative_area_is_rejected(client, catalog):
    """min="0" is client-side only and does not survive a direct POST.
    Postgres's int4 floors at -2147483648; SQLite would silently store
    past it, same divergence the upper bound already guards for."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Negative Area Room",
        "code": "NEG",
        "area_sqft": "-9999999999999",
    })

    assert "zero or greater" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(code="NEG").count() == 0


def test_a_non_integer_parent_id_is_rejected(client, catalog):
    """request.form.get(..., type=int) turns a bad value into None and
    would silently create a room instead of rejecting it. The route reads
    the raw string first so a garbled parent_id fails instead of being
    ignored."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Bad Parent Id",
        "code": "BADP",
        "parent_id": "not-an-int",
    })

    assert "pick a parent room" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(code="BADP").count() == 0


def test_a_parent_must_be_an_active_permanent_room_at_this_venue(client, catalog):
    """catalog_add_space's parent check is the only server-side validation
    kind derivation rests on now that there is no Kind field to contradict
    it. Exercise every way a posted parent_id can fail that check."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    slice_g = db.session.query(Space).filter_by(code="CHE-G").one()

    other_venue = Venue(code="OTH", name="Other Venue")
    db.session.add(other_venue)
    db.session.flush()
    other_room = Space(venue_id=other_venue.id, name="Other Room",
                       code="OTH-A", kind=SPACE_KIND_ROOM)

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    popup_room = Space(venue_id=venue.id, name="Popup Room", code="POP-R",
                       kind=SPACE_KIND_ROOM, event_cycle_id=cycle.id)

    archived_room = Space(venue_id=venue.id, name="Archived Room",
                          code="ARCH", kind=SPACE_KIND_ROOM, is_active=False)

    db.session.add_all([other_room, popup_room, archived_room])
    db.session.commit()

    bad_parents = {
        "a slice, not a room": slice_g.id,
        "a room at another venue": other_room.id,
        "an event-scoped room": popup_room.id,
        "an archived room": archived_room.id,
        "a nonexistent id": 999999,
    }
    for label, parent_id in bad_parents.items():
        code = f"BAD{parent_id}"
        resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
            "name": f"Rejected via {label}",
            "code": code,
            "parent_id": str(parent_id),
        })
        body = resp.get_data(as_text=True).lower()
        assert "pick a parent room" in body, label
        assert db.session.query(Space).filter_by(code=code).count() == 0, label


def test_a_name_can_be_corrected(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    space = db.session.query(Space).filter_by(code="CHE-G").one()

    client.post(f"/spaces/venues/{venue.id}/space/{space.id}", data={
        "name": "Chesapeake G corrected",
        "code": "CHE-G",
        "dimensions": "29x28x14",
        "area_sqft": "811",
    }, follow_redirects=True)

    db.session.refresh(space)
    assert space.name == "Chesapeake G corrected"


def test_archiving_hides_a_space_until_show_archived(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    solo = catalog["solo"]

    client.post(f"/spaces/venues/{venue.id}/space/{solo.id}/archive",
                follow_redirects=True)
    body = client.get(f"/spaces/venues/{venue.id}/").get_data(as_text=True)
    assert "Fort Washington Boardroom" not in body

    shown = client.get(
        f"/spaces/venues/{venue.id}/?show_archived=1").get_data(as_text=True)
    assert "Fort Washington Boardroom" in shown


def test_the_edit_link_reaches_restore_under_show_archived(client, catalog):
    """Restore lives only inside the editor. If the row's own Edit link
    drops show_archived, following it lands on a page where the archived
    row, and its editor, never render at all."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    solo = catalog["solo"]

    client.post(f"/spaces/venues/{venue.id}/space/{solo.id}/archive",
                follow_redirects=True)

    listing = client.get(
        f"/spaces/venues/{venue.id}/?show_archived=1").get_data(as_text=True)
    match = re.search(rf'href="([^"]*\bedit={solo.id}\b[^"]*)"', listing)
    assert match, "no Edit link found for the archived row"
    # A browser decodes the &amp; entity before it re-requests the href;
    # the raw HTML source still has it literally, so decode it here too.
    edit_href = html.unescape(match.group(1)).split("#")[0]

    edited = client.get(edit_href).get_data(as_text=True)
    restore_url = f"/spaces/venues/{venue.id}/space/{solo.id}/restore"
    assert restore_url in edited


def test_the_archive_control_is_not_on_the_row(client, catalog):
    """Archive is rare and hard to undo. It sits inside the editor, not
    beside the button people press constantly."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    archive_url = f"/spaces/venues/{venue.id}/space/{catalog['solo'].id}/archive"

    closed = client.get(f"/spaces/venues/{venue.id}/").get_data(as_text=True)
    assert archive_url not in closed

    opened = client.get(
        f"/spaces/venues/{venue.id}/?edit={catalog['solo'].id}").get_data(as_text=True)
    assert archive_url in opened


def test_an_event_scoped_space_does_not_appear_in_the_catalog(client, catalog):
    """A pop-up belongs to one event and is managed on the event page, not
    here. Regression guard: this filter was missing once before."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    db.session.add(Space(venue_id=venue.id, name="Merch Popup", code="POP-1",
                         kind=SPACE_KIND_ROOM, event_cycle_id=cycle.id))
    db.session.commit()

    body = client.get(f"/spaces/venues/{venue.id}/").get_data(as_text=True)
    # A positive control: an empty catalog (routes broken, template blank)
    # would also pass the negative assertion below for the wrong reason.
    assert "Chesapeake G/H/I" in body
    assert "Merch Popup" not in body


def test_an_archived_room_is_not_offered_as_a_parent(client, catalog):
    """The parent picker must never offer an archived room, whatever the
    show_archived toggle displays in the table. Regression guard: this was
    a real bug, fixed once before."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    room = catalog["room"]
    solo = catalog["solo"]

    room.is_active = False
    db.session.commit()

    body = client.get(
        f"/spaces/venues/{venue.id}/?show_archived=1&add=1").get_data(as_text=True)

    assert "Chesapeake G/H/I" in body  # shown in the table, archived pill and all
    # A positive control: a picker rendering with no options at all would
    # also pass the negative assertion below for the wrong reason.
    assert f'<option value="{solo.id}">' in body
    assert f'<option value="{room.id}">' not in body


def test_a_rejected_save_does_not_apply_the_code_change(client, catalog):
    """The save route validates every field before it assigns any. Assigning
    first left the rejected page showing a code the operator never saved.
    """
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    solo = db.session.query(Space).filter_by(code="FTW").one()

    body = client.post(
        f"/spaces/venues/{venue.id}/space/{solo.id}",
        data={"name": "Chesapeake G/H/I", "code": "FTW-NEW",
              "dimensions": "", "area_sqft": ""},
        follow_redirects=True).get_data(as_text=True)

    assert "already exists" in body
    # The rejected form keeps what was typed, so FTW-NEW is on the page by
    # design. What must not happen is the space carrying it.
    assert 'value="FTW-NEW"' in body
    db.session.expire_all()
    assert db.session.query(Space).filter_by(id=solo.id).one().code == "FTW"


def test_an_event_popups_name_does_not_block_a_permanent_room(client, catalog):
    """A pop-up belongs to one event and the catalog cannot show it, so it
    must not reserve a name against a page that could never explain why.
    """
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    cycle = EventCycle(code="SMF2026", name="Super MAGFest 2026",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    db.session.add(Space(venue_id=venue.id, name="Green Room", code="POP-GR",
                         kind=SPACE_KIND_ROOM, event_cycle_id=cycle.id))
    db.session.commit()

    client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Green Room",
        "code": "GRN",
    })

    created = db.session.query(Space).filter_by(code="GRN").one()
    assert created.name == "Green Room"
    assert created.event_cycle_id is None


def test_adding_a_space_without_a_code_generates_one(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Azalea 1",
        "kind": SPACE_KIND_ROOM,
    }, follow_redirects=True)

    assert db.session.query(Space).filter_by(code="AZA-P-1").count() == 1


def test_adding_a_digit_led_space_without_a_code_generates_one(client, catalog):
    """Regression guard: the old letters-only prefix rule threw the digits
    away and generated an empty code for a room like this one."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "302 Boardroom",
    }, follow_redirects=True)

    assert db.session.query(Space).filter_by(code="302-P").count() == 1


def test_adding_a_space_whose_name_generates_no_code_is_rejected(client, catalog):
    """A name with no ASCII letter or digit, a CJK name here, generates
    nothing. Unlike the paste path there is no preview to catch this, so
    the add form must reject it directly rather than write an empty code."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "会議室",
    })

    body = resp.get_data(as_text=True)
    assert "no code could be generated" in html.unescape(body).lower()
    assert db.session.query(Space).filter_by(name="会議室").count() == 0


def test_a_preview_writes_nothing(client, catalog):
    """The preview is the gate. Nothing exists until the confirm."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    before = db.session.query(Space).count()

    resp = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Cherry Blossom Ballroom\tCBB\t116x71x20\t7957\t",
    }, follow_redirects=True)

    body = resp.get_data(as_text=True)
    assert "Cherry Blossom Ballroom" in body
    assert db.session.query(Space).count() == before


def test_a_preview_names_the_problem_row(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Good room\tGRM\t\t\t\nBad room\tBRM\t\tabc\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert "not a whole number" in html.unescape(body)


def test_a_preview_rejects_a_row_missing_the_code_and_parent_columns(client, catalog):
    """Regression guard for the sheet that exposed this bug: a
    three-column paste (Name, Dimensions, Area, no Code, no Parent code)
    must not preview clean with a dimension string sitting in the code
    field."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Potomac 1\t30X20X12\t600",
    }, follow_redirects=True).get_data(as_text=True)

    assert "all 5 are needed" in html.unescape(body)


def test_a_preview_recognizes_a_header_by_any_cell(client, catalog):
    """The header that slipped past before this fix: "Meeting Room" alone
    names no recognized column, but "DIMENSIONS (LXWXH)" and "Area
    (sq.ft)" do, and either is enough to skip the row."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": ("Meeting Room\tDIMENSIONS (LXWXH)\tArea (sq.ft)\n"
                  "Potomac 1\t30X20X12\t600"),
    }, follow_redirects=True).get_data(as_text=True)

    assert "Potomac 1" in body
    assert "Meeting Room" not in body


def test_a_preview_rejects_a_code_the_venue_already_holds(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Another one\tCHE-G\t\t\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert "already exists at this venue" in html.unescape(body)


def test_a_preview_rejects_a_negative_area(client, catalog):
    """Mirrors _read_space_form's bound: SQLite would store this silently,
    Postgres does not."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Negative Area Room\tNEG\t\t-5\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert "zero or greater" in html.unescape(body).lower()


def test_a_preview_rejects_an_area_over_the_postgres_int4_bound(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Huge Area Room\tHUGE\t\t99999999999999\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert "square feet" in html.unescape(body).lower()


def test_a_preview_rejects_a_code_over_32_characters(client, catalog):
    """Mirrors the add/edit form's flash_if_too_long(code, limit=32):
    Space.code is String(32) and SQLite does not enforce it."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    long_code = "X" * 33

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": f"Long Code Room\t{long_code}\t\t\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert "longer than 32 characters" in html.unescape(body)


def test_a_preview_rejects_a_parent_code_that_is_not_a_legal_parent(client, catalog):
    """Mirrors test_a_parent_must_be_an_active_permanent_room_at_this_venue
    for the add form: a slice, an archived room, and an event-scoped room
    are all real codes at this venue but none is a legal parent."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    popup_room = Space(venue_id=venue.id, name="Popup Room", code="POP-R",
                       kind=SPACE_KIND_ROOM, event_cycle_id=cycle.id)
    archived_room = Space(venue_id=venue.id, name="Archived Room",
                          code="ARCH", kind=SPACE_KIND_ROOM, is_active=False)
    db.session.add_all([popup_room, archived_room])
    db.session.commit()

    for label, bad_code in (("a slice", "CHE-G"), ("an event pop-up", "POP-R"),
                            ("an archived room", "ARCH")):
        body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
            "pasted": f"New Slice\tNEWSL\t\t\t{bad_code}",
        }, follow_redirects=True).get_data(as_text=True)
        assert ("room this venue keeps between events"
                in html.unescape(body).lower()), label


def test_a_preview_rejects_a_self_parented_row(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Solo Room\tSOLO\t\t\tSOLO",
    }, follow_redirects=True).get_data(as_text=True)

    assert "own parent" in html.unescape(body).lower()


def test_a_preview_rejects_a_name_the_venue_already_holds(client, catalog):
    """The venue-wide name rule the add/edit form enforces applies to a
    paste too; this is the same gap the parser tests close, exercised
    through the route."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Fort Washington Boardroom\tFTW2\t\t\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert ("named 'Fort Washington Boardroom' already exists"
            in html.unescape(body))


def test_a_preview_rejects_a_name_repeated_in_the_paste(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Room one\tR1\t\t\t\nRoom one\tR2\t\t\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert "already used earlier in this paste" in html.unescape(body)


def test_a_preview_ignores_an_event_popups_name(client, catalog):
    """A pop-up belongs to one event and the catalog cannot show it, so it
    must not block a paste against a page that could never explain why.
    Mirrors test_an_event_popups_name_does_not_block_a_permanent_room for
    the add form."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    cycle = EventCycle(code="SMF2026", name="Super MAGFest 2026",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    db.session.add(Space(venue_id=venue.id, name="Green Room", code="POP-GR",
                         kind=SPACE_KIND_ROOM, event_cycle_id=cycle.id))
    db.session.commit()

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Green Room\tGRN\t\t\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert "already exists" not in html.unescape(body).lower()


def test_a_preview_past_a_popups_name_still_renders_the_row(client, catalog):
    """Companion to the test above: a negative assertion alone would pass
    against a broken or empty preview. Prove the row actually rendered,
    error-free, using the structural markers _catalog_preview.html sets."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    cycle = EventCycle(code="SMF2026", name="Super MAGFest 2026",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    db.session.add(Space(venue_id=venue.id, name="Green Room", code="POP-GR",
                         kind=SPACE_KIND_ROOM, event_cycle_id=cycle.id))
    db.session.commit()

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Green Room\tGRN\t\t\t",
    }, follow_redirects=True).get_data(as_text=True)

    assert 'data-preview-row="0"' in body
    assert 'data-preview-error' not in body
    assert 'value="Green Room"' in body


def test_a_preview_with_nothing_to_show_keeps_show_archived(client, catalog):
    """Regression guard: a bare venue_catalog redirect drops the toggle and
    strands the operator on a page an archived row cannot render under."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(
        f"/spaces/venues/{venue.id}/paste/preview?show_archived=1",
        data={"pasted": ""})

    assert resp.status_code == 302
    assert "show_archived=1" in resp.headers["Location"]


def test_the_paste_panel_shows_columns_as_a_template_line(client, catalog):
    """The five columns must read as a template to match, not prose to
    parse; a paragraph naming them is exactly what a real user pasted
    past when their sheet had only three columns."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.get(
        f"/spaces/venues/{venue.id}/?paste=1").get_data(as_text=True)

    assert "<code>Name | Code | Dimensions | Area | Parent code</code>" in body


def test_the_paste_and_preview_forms_carry_a_csrf_token(client, catalog):
    """A missing csrf_token field passes silently in this suite, because
    conftest disables CSRF, and returns 400 in a browser. Assert the field
    is present rather than trusting the suite to catch its absence."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    paste_panel = client.get(
        f"/spaces/venues/{venue.id}/?paste=1").get_data(as_text=True)
    assert 'id="paste-rows"' in paste_panel
    assert 'name="csrf_token"' in paste_panel

    preview = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Cherry Blossom Ballroom\tCBB\t\t\t",
    }).get_data(as_text=True)
    assert 'name="csrf_token"' in preview


def test_every_preview_field_is_editable(client, catalog):
    """The preview is the escape hatch for a generated code and every other
    field; a read-only cell would remove that escape hatch."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Cherry Blossom Ballroom\t\t116x71x20\t7957\t",
    }).get_data(as_text=True)

    assert "readonly" not in body
    assert "code generated" in body


def test_a_preview_row_keeps_columns_past_the_tracked_five(client, catalog):
    """A real venue sheet carries a capacity column this app excludes on
    purpose. The extra column must not shift Parent code into Dimensions
    or otherwise corrupt the row."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": "Solo Room\tSOLO\t29x28x14\t811\t\t250 capacity",
    }, follow_redirects=True).get_data(as_text=True)

    assert 'value="29x28x14"' in body
    assert 'value="811"' in body


# --- catalog_paste_confirm ---------------------------------------------


def _confirm_payload(rows):
    """Build catalog_paste_confirm's form payload for a list of row dicts.

    A row dict sets "create": False to leave its checkbox unticked;
    every other key mirrors _catalog_preview.html's field names.
    """
    data = {"row_count": str(len(rows))}
    for i, r in enumerate(rows):
        if r.get("create", True):
            data[f"create_{i}"] = "1"
        data[f"name_{i}"] = r.get("name", "")
        data[f"code_{i}"] = r.get("code", "")
        data[f"dimensions_{i}"] = r.get("dimensions", "")
        data[f"area_{i}"] = r.get("area", "")
        data[f"parent_{i}"] = r.get("parent", "")
    return data


def test_confirming_creates_the_rows(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "Azalea 1", "code": "AZA-1", "area": "1064"},
                           {"name": "Azalea 2", "code": "AZA-2", "area": "1064"},
                       ]))

    assert resp.status_code == 302
    assert db.session.query(Space).filter_by(code="AZA-1").one().area_sqft == 1064
    assert db.session.query(Space).filter_by(code="AZA-2").count() == 1


def test_a_parent_pasted_after_its_child_still_links(client, catalog):
    """People paste in sheet order, not dependency order. Mirrors
    test_a_parent_may_appear_after_its_child in test_catalog_parse.py,
    exercised through the route rather than the parser directly."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    client.post(f"/spaces/venues/{venue.id}/paste/confirm",
               data=_confirm_payload([
                   {"name": "Camellia 3", "code": "CAM-3", "parent": "CAM-34"},
                   {"name": "Camellia 3&4", "code": "CAM-34"},
               ]))

    child = db.session.query(Space).filter_by(code="CAM-3").one()
    parent = db.session.query(Space).filter_by(code="CAM-34").one()
    assert child.parent_id == parent.id
    assert child.kind == SPACE_KIND_SLICE
    assert parent.kind == SPACE_KIND_ROOM


def test_an_unticked_row_is_not_created(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    client.post(f"/spaces/venues/{venue.id}/paste/confirm",
               data=_confirm_payload([
                   {"name": "Wanted", "code": "WNT"},
                   {"name": "Skipped", "code": "SKP", "create": False},
               ]))

    assert db.session.query(Space).filter_by(code="WNT").count() == 1
    assert db.session.query(Space).filter_by(code="SKP").count() == 0


def test_one_bad_row_creates_nothing(client, catalog):
    """All or nothing, counted explicitly rather than by checking one
    code's absence: a partial batch is exactly the half-entered venue the
    all-or-nothing rule exists to prevent."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    before = db.session.query(Space).count()

    resp = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "Fine One", "code": "FIN1"},
                           {"name": "Fine Two", "code": "FIN2"},
                           # Collides with the fixture's existing slice.
                           {"name": "Clashing Row", "code": "CHE-G"},
                       ]))

    assert resp.status_code == 200
    assert db.session.query(Space).count() == before


def test_a_failed_confirm_does_not_redirect(client, catalog):
    """A rejected confirm re-renders the preview so nothing typed is lost;
    a redirect would strand the edits on the request that produced them."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "Bad Area Room", "code": "BAR",
                            "area": "-5"},
                       ]))

    assert resp.status_code == 200
    assert resp.headers.get("Location") is None


def test_a_failed_confirm_reports_every_rows_problem_and_keeps_what_was_typed(
        client, catalog):
    """Every problem is reported in the same pass, not one redirect round
    trip per row; fixing a seventeen-row paste one error at a time is not
    a workflow. Also proves the good row's own edits survive the
    rejection alongside the bad ones."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "A Fine Room", "code": "FINE"},
                           {"name": "X" * 129, "code": "TLNAME"},
                           {"name": "Long Code Room", "code": "X" * 33},
                           {"name": "Negative Area Room", "code": "NEG",
                            "area": "-5"},
                           {"name": "Self Parented", "code": "SELF",
                            "parent": "SELF"},
                           # CHE-G is a real code at this venue but a
                           # slice, not a room; not a legal parent.
                           {"name": "Illegal Parent Row", "code": "ILLP",
                            "parent": "CHE-G"},
                       ]))

    raw_body = resp.get_data(as_text=True)
    body = html.unescape(raw_body)
    assert resp.status_code == 200
    assert f"longer than {128} characters" in body
    assert f"longer than {32} characters" in body
    assert "zero or greater" in body.lower()
    assert "own parent" in body.lower()
    assert "room this venue keeps between events" in body.lower()
    # Five of six rows have a problem; the structural marker, not just
    # the message text, must appear exactly that many times.
    assert raw_body.count('data-preview-error="1"') == 5
    # The row with no problem keeps what was typed too.
    assert 'value="A Fine Room"' in raw_body
    assert db.session.query(Space).filter_by(code="FINE").count() == 0


def test_a_failed_confirm_rejects_duplicate_codes_and_names(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           # Collides with the fixture's existing code.
                           {"name": "Existing Code Clash", "code": "CHE-G"},
                           # Collides with the fixture's existing name.
                           {"name": "Fort Washington Boardroom", "code": "NEWC"},
                           {"name": "Batch Dup A", "code": "TWICE"},
                           {"name": "Batch Dup B", "code": "TWICE"},
                           {"name": "Repeat Name", "code": "RN1"},
                           {"name": "Repeat Name", "code": "RN2"},
                       ]))

    body = html.unescape(resp.get_data(as_text=True))
    assert "Code CHE-G already exists at this venue" in body
    assert "named 'Fort Washington Boardroom' already exists" in body
    assert "Code TWICE is already used earlier in this paste" in body
    assert "Name 'Repeat Name' is already used earlier in this paste" in body
    assert db.session.query(Space).filter_by(code="RN1").count() == 0


def test_a_confirm_enforces_parent_legality(client, catalog):
    """Mirrors test_a_preview_rejects_a_parent_code_that_is_not_a_legal_parent:
    a slice, an archived room, and an event-scoped room all have real
    codes at this venue but none may parent another row."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    popup_room = Space(venue_id=venue.id, name="Popup Room", code="POP-R",
                       kind=SPACE_KIND_ROOM, event_cycle_id=cycle.id)
    archived_room = Space(venue_id=venue.id, name="Archived Room",
                          code="ARCH", kind=SPACE_KIND_ROOM, is_active=False)
    db.session.add_all([popup_room, archived_room])
    db.session.commit()

    for label, bad_code in (("a slice", "CHE-G"), ("an event pop-up", "POP-R"),
                            ("an archived room", "ARCH")):
        code = f"NEWSL{bad_code}"
        resp = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                           data=_confirm_payload([
                               {"name": "New Slice", "code": code,
                                "parent": bad_code},
                           ]))
        body = html.unescape(resp.get_data(as_text=True)).lower()
        assert "room this venue keeps between events" in body, label
        assert db.session.query(Space).filter_by(code=code).count() == 0, label


def test_confirm_back_keeps_show_archived(client, catalog):
    """back must be _venue_catalog_url(venue, show_archived) on every
    redirect this route can take: no rows selected, a clean success, and
    the None-parent guard. A bare url_for drops the toggle the preview
    form appended."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    no_rows = client.post(
        f"/spaces/venues/{venue.id}/paste/confirm?show_archived=1",
        data={"row_count": "0"})
    assert no_rows.status_code == 302
    assert "show_archived=1" in no_rows.headers["Location"]

    success = client.post(
        f"/spaces/venues/{venue.id}/paste/confirm?show_archived=1",
        data=_confirm_payload([
            {"name": "Archived Toggle Room", "code": "ATR"},
        ]))
    assert success.status_code == 302
    assert "show_archived=1" in success.headers["Location"]

    # The None-parent guard is otherwise unreachable: validate_parent
    # already confirmed FTW resolves, so only a concurrent delete between
    # that check and the creation pass's own lookup reaches it. Forced
    # here by making that one lookup miss, without touching the earlier
    # queries validate_parent relies on.
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    real_query = db.session.query

    def query_stub(*args, **kwargs):
        query = real_query(*args, **kwargs)
        if args and args[0] is Space:
            query = query.filter(Space.code != "FTW")
        return query

    with patch.object(catalog_module.db.session, "query",
                      side_effect=query_stub):
        raced = client.post(
            f"/spaces/venues/{venue.id}/paste/confirm?show_archived=1",
            data=_confirm_payload([
                {"name": "Raced Child", "code": "RACEC", "parent": "FTW"},
            ]))

    assert raced.status_code == 302
    assert "show_archived=1" in raced.headers["Location"]
    assert db.session.query(Space).filter_by(code="RACEC").count() == 0


def test_a_pasted_row_can_be_parented_to_an_existing_room_outside_the_batch(
        client, catalog):
    """The parent lookup falls back to a database query when the parent's
    code was not itself created in this batch. Exercises that fallback,
    the line the stale brief dereferenced unguarded."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    room = catalog["solo"]  # FTW, already committed by the fixture

    client.post(f"/spaces/venues/{venue.id}/paste/confirm",
               data=_confirm_payload([
                   {"name": "Fort Washington West", "code": "FTW-W",
                    "parent": "FTW"},
               ]))

    child = db.session.query(Space).filter_by(code="FTW-W").one()
    assert child.parent_id == room.id
    assert child.kind == SPACE_KIND_SLICE


def test_a_realistic_twenty_row_paste_goes_through_preview_and_confirm(
        client, catalog):
    """A full round trip through both routes: a parent pasted after its
    children, a generated code corrected in the preview before confirming,
    and one row left unticked that must not be created."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    rows = [
        ("Azalea 1", "AZA-1", "", "1000", ""),
        ("Azalea 2", "AZA-2", "", "1000", ""),
        ("Azalea 3", "AZA-3", "", "1000", ""),
        ("Azalea 4", "AZA-4", "", "1000", ""),
        ("Camellia 1", "CAM-1", "", "", "CAM-ALL"),
        ("Camellia 2", "CAM-2", "", "", "CAM-ALL"),
        ("Camellia 3", "CAM-3", "", "", "CAM-ALL"),
        ("Camellia 4", "CAM-4", "", "", "CAM-ALL"),
        ("Dogwood A", "DOG-A", "", "", ""),
        ("Dogwood B", "DOG-B", "", "", ""),
        # Code left blank; the parser generates one and this row edits it
        # in the preview before confirming.
        ("Magnolia Suite", "", "", "", ""),
        ("National Harbor 1", "NH-1", "", "", ""),
        ("National Harbor 2", "NH-2", "", "", ""),
        ("National Harbor 3", "NH-3", "", "", ""),
        ("National Harbor 4", "NH-4", "", "", ""),
        ("National Harbor 5", "NH-5", "", "", ""),
        ("Potomac Ballroom A", "POT-A", "", "", ""),
        ("Potomac Ballroom B", "POT-B", "", "", ""),
        # Left unticked at confirm time.
        ("Woodrow Wilson Boardroom", "WW-B", "", "", ""),
        # This venue's room for the Camellia slices above, pasted after
        # all four of them.
        ("Camellia (All)", "CAM-ALL", "", "", ""),
    ]
    assert len(rows) == 20
    pasted = "\n".join("\t".join(cell for cell in row) for row in rows)

    preview_body = client.post(
        f"/spaces/venues/{venue.id}/paste/preview",
        data={"pasted": pasted}).get_data(as_text=True)

    assert "data-preview-error" not in preview_body
    match = re.search(r'name="code_10" value="([^"]*)"', preview_body)
    assert match, "no generated code found for the Magnolia Suite row"
    generated_code = match.group(1)
    assert generated_code
    edited_code = "MAGB"
    assert edited_code != generated_code

    codes_before = {
        c for (c,) in db.session.query(Space.code)
        .filter(Space.venue_id == venue.id).all()
    }

    data = {"row_count": "20"}
    for i, (name, code, dimensions, area, parent) in enumerate(rows):
        if i != 18:  # everything but Woodrow Wilson Boardroom is ticked
            data[f"create_{i}"] = "1"
        data[f"name_{i}"] = name
        data[f"code_{i}"] = edited_code if i == 10 else code
        data[f"dimensions_{i}"] = dimensions
        data[f"area_{i}"] = area
        data[f"parent_{i}"] = parent

    resp = client.post(f"/spaces/venues/{venue.id}/paste/confirm", data=data)
    assert resp.status_code == 302

    assert db.session.query(Space).filter_by(code="WW-B").count() == 0
    assert db.session.query(Space).filter_by(code=generated_code).count() == 0
    magnolia = db.session.query(Space).filter_by(code=edited_code).one()
    assert magnolia.name == "Magnolia Suite"

    parent_room = db.session.query(Space).filter_by(code="CAM-ALL").one()
    assert parent_room.kind == SPACE_KIND_ROOM
    children = db.session.query(Space).filter(
        Space.code.in_(["CAM-1", "CAM-2", "CAM-3", "CAM-4"])).all()
    assert len(children) == 4
    for child in children:
        assert child.parent_id == parent_room.id
        assert child.kind == SPACE_KIND_SLICE

    created_codes = {
        c for (c,) in db.session.query(Space.code)
        .filter(Space.venue_id == venue.id).all()
    }
    expected = ({row[1] for row in rows if row[1]} - {"WW-B"}) | {edited_code}
    # Equality, not containment: an extra row created alongside the
    # expected nineteen is as much a failure as a missing one.
    assert created_codes - codes_before == expected


def test_a_rejected_confirms_unticked_box_stays_unticked(client, catalog):
    """The regression `checked` exists to prevent: deriving the checkbox
    from r.error alone re-ticks a row the operator chose to skip, since a
    skipped row carries no error of its own."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "Forces Rerender", "code": "X" * 33},
                           {"name": "Skipped Row", "code": "SKP",
                            "create": False},
                       ])).get_data(as_text=True)

    skipped_box = re.search(r'<input type="checkbox" name="create_1"[^>]*>',
                            body)
    assert skipped_box, "no checkbox found for the skipped row"
    assert "checked" not in skipped_box.group(0)
    # Positive control: the still-ticked, still-failing row's box must
    # still read checked, or the assertion above passes against a
    # template that never renders the attribute at all.
    forced_box = re.search(r'<input type="checkbox" name="create_0"[^>]*>',
                           body)
    assert forced_box
    assert "checked" in forced_box.group(0)


def test_a_ticked_rows_bad_area_text_survives_a_rejected_confirm(client, catalog):
    """The raw text must survive the rejection; clearing the box beside
    its own "not a whole number" message tells the operator their value
    was both wrong and gone."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "Bad Area Room", "code": "BAR",
                            "area": "1,200"},
                       ])).get_data(as_text=True)

    assert 'value="1,200"' in body
    assert "not a whole number" in html.unescape(body)
    assert db.session.query(Space).filter_by(code="BAR").count() == 0


def test_an_unticked_rows_bad_area_text_survives_a_rejected_confirm(
        client, catalog):
    """The route used to parse the unticked row's area anyway and store
    the result, clearing a cell it deliberately never validates. The raw
    text must render unchanged when some other row forces a re-render."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "Skipped Bad Area", "code": "SKPB",
                            "area": "not-a-number", "create": False},
                           {"name": "Forces Rerender", "code": "X" * 33},
                       ])).get_data(as_text=True)

    assert 'value="not-a-number"' in body


def test_an_unticked_rows_bad_area_does_not_block_the_batch(client, catalog):
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "Wanted Room", "code": "WANT"},
                           {"name": "Skipped Bad Area", "code": "SKPB",
                            "area": "not-a-number", "create": False},
                       ]))

    assert resp.status_code == 302
    assert db.session.query(Space).filter_by(code="WANT").count() == 1
    assert db.session.query(Space).filter_by(code="SKPB").count() == 0


def test_the_button_and_summary_count_what_pressing_it_would_attempt(
        client, catalog):
    """A rejected confirm can carry a still-ticked, still-erroring row.
    Pressing Create submits every ticked box, not just the error-free
    ones, so the count shown must include it."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/confirm",
                       data=_confirm_payload([
                           {"name": "Good Row", "code": "GOOD"},
                           {"name": "Bad Row", "code": "X" * 33},
                           {"name": "Skipped Row", "code": "SKP",
                            "create": False},
                       ])).get_data(as_text=True)

    normalized = " ".join(body.split())
    assert "2 rows to create, 1 problem." in normalized
    assert ">Create 2 spaces</button>" in body


def test_confirm_bounds_row_count(client, catalog):
    """row_count is form-derived and otherwise unbounded; refused outright
    rather than spending a request on per-field lookups no venue needs."""
    from app.routes.spaces.catalog import MAX_PASTE_ROWS

    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    before = db.session.query(Space).count()

    resp = client.post(
        f"/spaces/venues/{venue.id}/paste/confirm",
        data={"row_count": str(MAX_PASTE_ROWS + 1)},
        follow_redirects=True)

    assert "more rows than one paste holds" in resp.get_data(as_text=True)
    assert db.session.query(Space).count() == before


def test_only_a_whole_number_reaches_the_area_column(client, catalog):
    """area_sqft is written to an Integer column and area_text is only
    redisplayed. SQLite would store "1,200" there and Postgres would raise,
    so the two must not share a field.
    """
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    client.post(f"/spaces/venues/{venue.id}/paste/confirm", data={
        "row_count": "2",
        "create_0": "1", "name_0": "Azalea 1", "code_0": "AZA-P-1",
        "dimensions_0": "", "area_0": "1064", "parent_0": "",
        "name_1": "Skipped", "code_1": "SKIP", "dimensions_1": "",
        "area_1": "1,200", "parent_1": "",
    }, follow_redirects=True)

    db.session.expire_all()
    made = db.session.query(Space).filter(Space.venue_id == venue.id).all()
    assert all(s.area_sqft is None or isinstance(s.area_sqft, int)
               for s in made)
    assert db.session.query(Space).filter_by(code="AZA-P-1").one().area_sqft == 1064
    assert db.session.query(Space).filter_by(code="SKIP").count() == 0


# ---------------------------------------------------------------------------
# Orphan slice-coded rooms: the warning banner and the repair screen.
# ---------------------------------------------------------------------------

def _add_room(venue, name, code, parent_id=None, kind=None):
    space = Space(venue_id=venue.id, name=name, code=code,
                 kind=kind or SPACE_KIND_ROOM, parent_id=parent_id)
    db.session.add(space)
    db.session.flush()
    return space


def test_the_banner_is_absent_with_no_orphans(client, catalog):
    """The fixture's own rooms and slices are all correctly parented, so
    nothing here should cry wolf."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.get(
        f"/spaces/venues/{venue.id}/").get_data(as_text=True)

    assert 'id="orphan-slice-warning"' not in body


def test_a_standalone_room_with_an_unmarked_code_does_not_flag(client, catalog):
    """A standalone meeting room with no parent and an unmarked code is
    legitimate and must not appear in the warning, even though it has no
    parent and no children, the same shape as an orphan slice."""
    venue = catalog["venue"]
    room = _add_room(venue, "Boardroom", "BRD")
    db.session.commit()
    _login(client, "test:spaceadmin")

    body = client.get(
        f"/spaces/venues/{venue.id}/").get_data(as_text=True)

    assert 'id="orphan-slice-warning"' not in body
    assert f'data-space-id="{room.id}"' in body


def test_the_banner_flags_a_slice_coded_room_with_no_parent(client, catalog):
    venue = catalog["venue"]
    _add_room(venue, "Potomac Ballroom", "PTB-S")
    db.session.commit()
    _login(client, "test:spaceadmin")

    body = client.get(
        f"/spaces/venues/{venue.id}/").get_data(as_text=True)

    assert 'id="orphan-slice-warning"' in body
    assert 'data-orphan-count="1"' in body
    assert f"/spaces/venues/{venue.id}/repair-slices" in body


def test_the_repair_screen_suggests_the_implied_parent(client, catalog):
    """MDB-S-1's implied room code is MDB-P; an active room with that
    exact code at this venue is offered pre-selected."""
    venue = catalog["venue"]
    room = _add_room(venue, "Maryland Ballroom", "MDB-P")
    orphan = _add_room(venue, "Maryland 1", "MDB-S-1")
    db.session.commit()
    _login(client, "test:spaceadmin")

    body = client.get(
        f"/spaces/venues/{venue.id}/repair-slices").get_data(as_text=True)

    assert f'data-repair-row="{orphan.id}"' in body
    match = re.search(
        rf'<option value="{room.id}"[^>]*>', body)
    assert match is not None
    assert "selected" in match.group(0)


def test_a_code_with_no_hint_still_gets_a_repairable_row(client, catalog):
    """CHE-S parses as slice-coded fine and implies CHE-P; this venue
    just has no room with that code (its room is CHE-GHI). A suggestion
    that resolves to nothing must not strand the space; it still gets a
    row with an open picker."""
    venue = catalog["venue"]
    orphan = _add_room(venue, "Chesapeake South", "CHE-S")
    db.session.commit()
    _login(client, "test:spaceadmin")

    body = client.get(
        f"/spaces/venues/{venue.id}/repair-slices").get_data(as_text=True)

    assert f'data-repair-row="{orphan.id}"' in body
    assert f'id="repair-parent-{orphan.id}"' in body


def test_repairing_sets_parent_and_kind(client, catalog):
    venue = catalog["venue"]
    room = _add_room(venue, "Maryland Ballroom", "MDB-P")
    orphan = _add_room(venue, "Maryland 1", "MDB-S-1")
    db.session.commit()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{orphan.id}": "1",
        f"parent_{orphan.id}": str(room.id),
    })

    assert resp.status_code == 302
    db.session.refresh(orphan)
    assert orphan.parent_id == room.id
    assert orphan.kind == SPACE_KIND_SLICE


def test_an_unticked_repair_row_is_untouched(client, catalog):
    venue = catalog["venue"]
    room = _add_room(venue, "Maryland Ballroom", "MDB-P")
    wanted = _add_room(venue, "Maryland 1", "MDB-S-1")
    skipped = _add_room(venue, "Maryland 2", "MDB-S-2")
    db.session.commit()
    _login(client, "test:spaceadmin")

    client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{wanted.id}": "1",
        f"parent_{wanted.id}": str(room.id),
        f"parent_{skipped.id}": str(room.id),
    })

    db.session.refresh(skipped)
    assert skipped.parent_id is None
    assert skipped.kind == SPACE_KIND_ROOM


def test_one_bad_repair_row_writes_nothing(client, catalog):
    """All or nothing, matching catalog_paste_confirm: a bad row must not
    let the good one in the same batch through."""
    venue = catalog["venue"]
    room = _add_room(venue, "Maryland Ballroom", "MDB-P")
    good = _add_room(venue, "Maryland 1", "MDB-S-1")
    bad = _add_room(venue, "Potomac 1", "PTB-S-1")
    db.session.commit()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{good.id}": "1",
        f"parent_{good.id}": str(room.id),
        f"apply_{bad.id}": "1",
        f"parent_{bad.id}": "",
    })

    assert resp.status_code == 200
    db.session.refresh(good)
    db.session.refresh(bad)
    assert good.parent_id is None
    assert good.kind == SPACE_KIND_ROOM
    assert bad.parent_id is None
    body = resp.get_data(as_text=True)
    assert f'data-repair-row="{bad.id}"' in body
    assert 'data-repair-error="1"' in body


def test_repair_apply_enforces_the_parent_rule_on_a_direct_post(client, catalog):
    """A direct POST bypassing the picker must not be able to parent a
    space to a slice; the tree here is two levels."""
    venue = catalog["venue"]
    orphan = _add_room(venue, "Potomac 1", "PTB-S-1")
    slice_g = db.session.query(Space).filter_by(code="CHE-G").one()
    db.session.commit()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{orphan.id}": "1",
        f"parent_{orphan.id}": str(slice_g.id),
    })

    assert resp.status_code == 200
    db.session.refresh(orphan)
    assert orphan.parent_id is None
    assert orphan.kind == SPACE_KIND_ROOM
    body = html.unescape(resp.get_data(as_text=True))
    assert "Pick a parent room at this venue" in body


def test_repair_apply_refuses_a_space_that_already_has_children(client, catalog):
    """A room with children cannot become a slice; that would make a
    three-level tree this app does not model."""
    venue = catalog["venue"]
    room = _add_room(venue, "Maryland Ballroom", "MDB-P")
    parent_orphan = _add_room(venue, "Chesapeake South", "CHE-S")
    _add_room(venue, "Chesapeake South 1", "CHE-S-1", parent_id=parent_orphan.id,
             kind=SPACE_KIND_SLICE)
    db.session.commit()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{parent_orphan.id}": "1",
        f"parent_{parent_orphan.id}": str(room.id),
    })

    assert resp.status_code == 200
    db.session.refresh(parent_orphan)
    assert parent_orphan.parent_id is None
    assert parent_orphan.kind == SPACE_KIND_ROOM
    body = html.unescape(resp.get_data(as_text=True))
    assert "already has spaces under it" in body


def test_repair_screen_carries_the_csrf_token(client, catalog):
    """Same regression this app has already shipped once: a missing
    token passes this suite (CSRF is disabled here) and returns 400 in a
    browser. The screen renders its form, and the token with it, even
    with nothing flagged to repair."""
    venue = catalog["venue"]
    _login(client, "test:spaceadmin")

    body = client.get(
        f"/spaces/venues/{venue.id}/repair-slices").get_data(as_text=True)

    assert 'name="csrf_token"' in body


def test_two_ticked_orphans_naming_each_other_as_parent_are_refused(
        client, catalog):
    """A row being converted in this batch cannot be another row's
    parent; both are about to become slices, and a slice parenting a
    slice would form a cycle. Neither may write."""
    venue = catalog["venue"]
    a = _add_room(venue, "Orphan A", "AAA-S")
    b = _add_room(venue, "Orphan B", "BBB-S")
    db.session.commit()
    before = db.session.query(Space).count()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{a.id}": "1", f"parent_{a.id}": str(b.id),
        f"apply_{b.id}": "1", f"parent_{b.id}": str(a.id),
    })

    assert resp.status_code == 200
    assert db.session.query(Space).count() == before
    db.session.refresh(a)
    db.session.refresh(b)
    assert a.kind == SPACE_KIND_ROOM and a.parent_id is None
    assert b.kind == SPACE_KIND_ROOM and b.parent_id is None
    body = html.unescape(resp.get_data(as_text=True))
    assert "itself being turned into a slice" in body


def test_one_orphan_named_as_another_ticked_orphans_parent_is_refused(
        client, catalog):
    """Same rule, one direction only: A names ticked orphan B as its
    parent, and B does not name A back. Still refused, because B is
    still being converted in this batch."""
    venue = catalog["venue"]
    a = _add_room(venue, "Orphan A", "AAA-S")
    b = _add_room(venue, "Orphan B", "BBB-S")
    db.session.commit()
    before = db.session.query(Space).count()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{a.id}": "1", f"parent_{a.id}": str(b.id),
        f"apply_{b.id}": "1", f"parent_{b.id}": "",
    })

    assert resp.status_code == 200
    assert db.session.query(Space).count() == before
    db.session.refresh(a)
    assert a.kind == SPACE_KIND_ROOM and a.parent_id is None
    body = html.unescape(resp.get_data(as_text=True))
    assert "itself being turned into a slice" in body


def test_naming_an_unticked_orphan_as_parent_succeeds(client, catalog):
    """The same pairing as above, but B is left unticked: B stays a
    room, untouched, so it is a legal parent for A. This is the case
    that proves the rule is about batch membership, not the code
    marker; B's code still reads as a slice."""
    venue = catalog["venue"]
    a = _add_room(venue, "Orphan A", "AAA-S")
    b = _add_room(venue, "Orphan B", "BBB-S")
    db.session.commit()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{a.id}": "1", f"parent_{a.id}": str(b.id),
        f"parent_{b.id}": "",
    })

    assert resp.status_code == 302
    db.session.refresh(a)
    db.session.refresh(b)
    assert a.kind == SPACE_KIND_SLICE
    assert a.parent_id == b.id
    assert b.kind == SPACE_KIND_ROOM
    assert b.parent_id is None


def test_repair_apply_refuses_a_parent_at_another_venue(client, catalog):
    """Space.code is unique only within a venue
    (ix_spaces_code_permanent); two venues can each hold a room coded
    MDB-P. A direct POST naming the other venue's room by id must be
    refused, not accepted because the codes happen to compare equal."""
    venue = catalog["venue"]
    other_venue = Venue(code="OTH", name="Other Venue")
    db.session.add(other_venue)
    db.session.flush()
    _add_room(venue, "Maryland Ballroom", "MDB-P")
    foreign_room = _add_room(other_venue, "Maryland Ballroom", "MDB-P")
    orphan = _add_room(venue, "Maryland 1", "MDB-S-1")
    db.session.commit()
    before = db.session.query(Space).filter_by(venue_id=venue.id).count()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{orphan.id}": "1",
        f"parent_{orphan.id}": str(foreign_room.id),
    })

    assert resp.status_code == 200
    assert (db.session.query(Space).filter_by(venue_id=venue.id).count()
            == before)
    db.session.refresh(orphan)
    assert orphan.parent_id is None
    assert orphan.kind == SPACE_KIND_ROOM
    body = html.unescape(resp.get_data(as_text=True))
    assert "Pick a parent room at this venue" in body


def test_repair_apply_refuses_a_space_with_an_event_scoped_child(
        client, catalog):
    """The event page can parent a pop-up to any room at the venue,
    including an unrepaired orphan; that child is event-scoped, not
    permanent. It must still block the orphan from becoming a slice, the
    same as a permanent child, since a three-level tree is not one this
    app models."""
    venue = catalog["venue"]
    room = _add_room(venue, "Maryland Ballroom", "MDB-P")
    orphan = _add_room(venue, "Chesapeake South", "CHE-S")
    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, venue_id=venue.id)
    db.session.add(cycle)
    db.session.flush()
    db.session.add(Space(venue_id=venue.id, name="Merch Popup",
                         code="POP-1", kind=SPACE_KIND_ROOM,
                         parent_id=orphan.id, event_cycle_id=cycle.id))
    db.session.commit()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{orphan.id}": "1",
        f"parent_{orphan.id}": str(room.id),
    })

    assert resp.status_code == 200
    db.session.refresh(orphan)
    assert orphan.parent_id is None
    assert orphan.kind == SPACE_KIND_ROOM
    body = html.unescape(resp.get_data(as_text=True))
    assert "already has spaces under it" in body


def test_repair_apply_refuses_a_non_ascii_digit_parent_id(client, catalog):
    """'²'.isdigit() is True (it is Unicode superscript two), but
    int('²') raises ValueError. A crafted parent value like this
    must be refused, not 500."""
    venue = catalog["venue"]
    orphan = _add_room(venue, "Potomac 1", "PTB-S-1")
    db.session.commit()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{orphan.id}": "1",
        f"parent_{orphan.id}": "²",
    })

    assert resp.status_code == 200
    db.session.refresh(orphan)
    assert orphan.parent_id is None
    body = html.unescape(resp.get_data(as_text=True))
    assert "Pick a parent room to repair this space" in body


def test_repair_apply_refuses_an_oversized_parent_id(client, catalog):
    """'9' * 25 passes str.isdigit() and int() parses it without error,
    Python integers are arbitrary precision, but Space.id is a plain
    Integer. Reaching db.session.get with a value this size raises
    OverflowError on SQLite; it must be refused first instead."""
    venue = catalog["venue"]
    orphan = _add_room(venue, "Potomac 1", "PTB-S-1")
    db.session.commit()
    _login(client, "test:spaceadmin")

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{orphan.id}": "1",
        f"parent_{orphan.id}": "9" * 25,
    })

    assert resp.status_code == 200
    db.session.refresh(orphan)
    assert orphan.parent_id is None
    body = html.unescape(resp.get_data(as_text=True))
    assert "Pick a parent room to repair this space" in body

def test_the_add_form_refuses_an_oversized_parent_id(client, catalog):
    """int() has no upper bound, so an oversized parent_id survived the
    parse and raised inside db.session.get. Space.id is Postgres int4.
    """
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Oversized Parent Room", "code": "OVR",
        "parent_id": "9" * 25,
    })

    assert resp.status_code == 200
    assert "Pick a parent room at this venue" in html.unescape(
        resp.get_data(as_text=True))
    assert db.session.query(Space).filter_by(code="OVR").count() == 0


# ---------------------------------------------------------------------------
# Whole-subsystem review fixes
# ---------------------------------------------------------------------------


def test_a_room_that_already_has_a_parent_is_refused_as_a_parent(client, catalog):
    """The event page can write a permanent kind=ROOM row that already has
    a parent; kind alone is not the depth test, or that row could parent
    another one here and produce a three-level tree build_catalog_rows
    cannot render. Exercises the add-form picker, the add-form POST, the
    paste preview, and the repair screen's picker."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    room = catalog["room"]

    # Simulates what the event page's create_space route can write: a
    # permanent ROOM row nested under another room.
    nested_room = Space(venue_id=venue.id, name="Nested Room", code="NEST-P",
                        kind=SPACE_KIND_ROOM, parent_id=room.id)
    orphan = _add_room(venue, "Potomac 1", "PTB-S-1")
    db.session.add(nested_room)
    db.session.commit()

    add_panel = client.get(
        f"/spaces/venues/{venue.id}/?add=1").get_data(as_text=True)
    assert f'<option value="{nested_room.id}">' not in add_panel

    resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
        "name": "Grandchild", "code": "GRAND",
        "parent_id": str(nested_room.id),
    })
    assert "pick a parent room" in resp.get_data(as_text=True).lower()
    assert db.session.query(Space).filter_by(code="GRAND").count() == 0

    preview_body = client.post(f"/spaces/venues/{venue.id}/paste/preview", data={
        "pasted": f"New Slice\tNEWSL\t\t\t{nested_room.code}",
    }).get_data(as_text=True)
    assert ("room this venue keeps between events"
            in html.unescape(preview_body).lower())

    repair_body = client.get(
        f"/spaces/venues/{venue.id}/repair-slices").get_data(as_text=True)
    assert f'data-repair-row="{orphan.id}"' in repair_body
    assert f'<option value="{nested_room.id}"' not in repair_body


def test_the_repair_screens_get_lists_a_child_bearing_row_unticked_with_its_problem(
        client, catalog):
    """_find_flagged_spaces does not look at children, so a flagged room
    that already has one was listed pre-ticked with an empty Problem
    cell; the POST then refused the whole batch, including a clean
    repair ticked beside it. The GET must show the same problem the POST
    would raise, unticked, and a clean row beside it must still succeed."""
    venue = catalog["venue"]
    md_room = _add_room(venue, "Maryland Ballroom", "MDB-P")
    parent_orphan = _add_room(venue, "Maryland South", "MDB-S")
    _add_room(venue, "Maryland South 1", "MDB-S1",
             parent_id=parent_orphan.id, kind=SPACE_KIND_SLICE)
    clean_orphan = _add_room(venue, "Potomac 1", "PTB-S-1")
    db.session.commit()
    _login(client, "test:spaceadmin")

    body = client.get(
        f"/spaces/venues/{venue.id}/repair-slices").get_data(as_text=True)

    row_match = re.search(
        rf'<tr data-repair-row="{parent_orphan.id}".*?</tr>',
        body, re.DOTALL)
    assert row_match, "no row found for the child-bearing orphan"
    checkbox = re.search(r'<input type="checkbox"[^>]*>', row_match.group(0))
    assert "checked" not in checkbox.group(0)
    assert "already has spaces under it" in html.unescape(row_match.group(0))

    resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
        f"apply_{clean_orphan.id}": "1",
        f"parent_{clean_orphan.id}": str(md_room.id),
    })

    assert resp.status_code == 302
    db.session.refresh(clean_orphan)
    assert clean_orphan.kind == SPACE_KIND_SLICE
    assert clean_orphan.parent_id == md_room.id


def test_preview_bounds_row_count(client, catalog):
    """The confirm-only limit let an oversized paste preview in full and
    lose every edit only once the operator pressed Create; the preview
    must refuse before parsing or rendering any of it."""
    from app.routes.spaces.catalog import MAX_PASTE_ROWS

    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    pasted = "\n".join(f"Room {i}\tR{i}\t\t\t"
                       for i in range(MAX_PASTE_ROWS + 1))
    resp = client.post(f"/spaces/venues/{venue.id}/paste/preview",
                       data={"pasted": pasted}, follow_redirects=True)

    assert "more rows than one paste holds" in resp.get_data(as_text=True)
    assert "data-preview-row" not in resp.get_data(as_text=True)


def test_a_generated_codes_marker_survives_a_rejected_confirm(client, catalog):
    """catalog_paste_confirm hardcoded generated_code=False on every row it
    re-renders, so a generated code's "code generated" marker vanished the
    moment some other row in the same batch forced a re-render."""
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    body = client.post(f"/spaces/venues/{venue.id}/paste/confirm", data={
        "row_count": "2",
        "create_0": "1", "name_0": "Cherry Blossom Ballroom",
        "code_0": "CBB", "dimensions_0": "", "area_0": "", "parent_0": "",
        "generated_0": "1",
        "create_1": "1", "name_1": "Bad Row", "code_1": "BRM",
        "dimensions_1": "", "area_1": "abc", "parent_1": "",
        "generated_1": "0",
    }).get_data(as_text=True)

    assert "code generated" in body


def test_venue_lookup_accepts_the_int4_bound_as_a_normal_miss(client, catalog):
    """MAX_SPACE_ID is Postgres's int4 ceiling for Venue.id. A route param
    at exactly that value must still reach db.session.get and 404 as an
    ordinary miss, not be pre-refused the way one past it is."""
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    _login(client, "test:spaceadmin")
    real_get = catalog_module.db.session.get

    with patch.object(catalog_module.db.session, "get",
                      side_effect=real_get) as mock_get:
        resp = client.get("/spaces/venues/2147483647/")

    assert resp.status_code == 404
    assert (Venue, 2147483647) in [c.args for c in mock_get.call_args_list]


def test_venue_lookup_refuses_one_past_the_int4_bound_without_querying(
        client, catalog):
    """One past MAX_SPACE_ID must be refused before db.session.get: Venue.id
    is Postgres int4, and Postgres would reject this value outright rather
    than miss on it, unlike SQLite, which would just 404 cleanly."""
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    _login(client, "test:spaceadmin")
    real_get = catalog_module.db.session.get

    with patch.object(catalog_module.db.session, "get",
                      side_effect=real_get) as mock_get:
        resp = client.get("/spaces/venues/2147483648/")

    assert resp.status_code == 404
    calls = [c.args for c in mock_get.call_args_list]
    assert not any(args and args[0] is Venue and args[1] == 2147483648
                  for args in calls)


def test_space_lookup_accepts_the_int4_bound_as_a_normal_miss(client, catalog):
    """Companion to the venue lookup boundary test, for _get_catalog_space
    and Space.id."""
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    real_get = catalog_module.db.session.get

    with patch.object(catalog_module.db.session, "get",
                      side_effect=real_get) as mock_get:
        resp = client.post(
            f"/spaces/venues/{venue.id}/space/2147483647",
            data={"name": "X", "code": "X"})

    assert resp.status_code == 404
    assert (Space, 2147483647) in [c.args for c in mock_get.call_args_list]


def test_space_lookup_refuses_one_past_the_int4_bound_without_querying(
        client, catalog):
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    real_get = catalog_module.db.session.get

    with patch.object(catalog_module.db.session, "get",
                      side_effect=real_get) as mock_get:
        resp = client.post(
            f"/spaces/venues/{venue.id}/space/2147483648",
            data={"name": "X", "code": "X"})

    assert resp.status_code == 404
    calls = [c.args for c in mock_get.call_args_list]
    assert not any(args and args[0] is Space and args[1] == 2147483648
                  for args in calls)


def test_add_forms_parent_id_accepts_the_int4_bound_as_a_normal_miss(
        client, catalog):
    """Companion to test_the_add_form_refuses_an_oversized_parent_id:
    "9" * 25 only fails because SQLite raises near 2**63. Nothing else
    proved the guard sits at int4 specifically, so at exactly
    MAX_SPACE_ID the lookup must still run and miss normally."""
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    real_get = catalog_module.db.session.get

    with patch.object(catalog_module.db.session, "get",
                      side_effect=real_get) as mock_get:
        resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
            "name": "Boundary Parent Room", "code": "BND",
            "parent_id": "2147483647",
        })

    assert "pick a parent room" in resp.get_data(as_text=True).lower()
    assert (Space, 2147483647) in [c.args for c in mock_get.call_args_list]
    assert db.session.query(Space).filter_by(code="BND").count() == 0


def test_add_forms_parent_id_refuses_one_past_the_int4_bound_without_querying(
        client, catalog):
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    _login(client, "test:spaceadmin")
    venue = catalog["venue"]
    real_get = catalog_module.db.session.get

    with patch.object(catalog_module.db.session, "get",
                      side_effect=real_get) as mock_get:
        resp = client.post(f"/spaces/venues/{venue.id}/space/new", data={
            "name": "Over Bound Parent Room", "code": "OVB",
            "parent_id": "2147483648",
        })

    assert "pick a parent room" in resp.get_data(as_text=True).lower()
    calls = [c.args for c in mock_get.call_args_list]
    assert not any(args and args[0] is Space and args[1] == 2147483648
                  for args in calls)
    assert db.session.query(Space).filter_by(code="OVB").count() == 0


def test_repair_apply_accepts_the_int4_bound_as_a_normal_miss(client, catalog):
    """Companion to test_repair_apply_refuses_an_oversized_parent_id, the
    other of the two oversized-id tests that only proved the SQLite
    2**63 crash boundary, not the int4 boundary the guard encodes."""
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    venue = catalog["venue"]
    orphan = _add_room(venue, "Potomac 1", "PTB-S-1")
    db.session.commit()
    _login(client, "test:spaceadmin")
    real_get = catalog_module.db.session.get

    with patch.object(catalog_module.db.session, "get",
                      side_effect=real_get) as mock_get:
        resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
            f"apply_{orphan.id}": "1",
            f"parent_{orphan.id}": "2147483647",
        })

    assert resp.status_code == 200
    body = html.unescape(resp.get_data(as_text=True))
    assert "Pick a parent room to repair this space" in body
    assert (Space, 2147483647) in [c.args for c in mock_get.call_args_list]


def test_repair_apply_refuses_one_past_the_int4_bound_without_querying(
        client, catalog):
    from unittest.mock import patch
    from app.routes.spaces import catalog as catalog_module

    venue = catalog["venue"]
    orphan = _add_room(venue, "Potomac 1", "PTB-S-1")
    db.session.commit()
    _login(client, "test:spaceadmin")
    real_get = catalog_module.db.session.get

    with patch.object(catalog_module.db.session, "get",
                      side_effect=real_get) as mock_get:
        resp = client.post(f"/spaces/venues/{venue.id}/repair-slices", data={
            f"apply_{orphan.id}": "1",
            f"parent_{orphan.id}": "2147483648",
        })

    assert resp.status_code == 200
    body = html.unescape(resp.get_data(as_text=True))
    assert "Pick a parent room to repair this space" in body
    calls = [c.args for c in mock_get.call_args_list]
    assert not any(args and args[0] is Space and args[1] == 2147483648
                  for args in calls)


def test_the_repair_screen_never_suggests_a_nested_room(client, catalog):
    """The suggestion is the last place that asked kind alone. A parent
    must also sit at the top of the tree, or the repair proposes a
    three-level result the apply route then refuses.
    """
    _login(client, "test:spaceadmin")
    venue = catalog["venue"]

    top = Space(venue_id=venue.id, name="Azalea Hall", code="AZA-P",
                kind=SPACE_KIND_ROOM)
    db.session.add(top)
    db.session.flush()
    # A permanent ROOM that already has a parent. The event page can write
    # one, so the catalog has to cope with it.
    nested = Space(venue_id=venue.id, name="Azalea Wing", code="AZB-P",
                   kind=SPACE_KIND_ROOM, parent_id=top.id)
    orphan = Space(venue_id=venue.id, name="Azalea Wing 1", code="AZB-S-1",
                   kind=SPACE_KIND_ROOM)
    db.session.add_all([nested, orphan])
    db.session.commit()

    body = client.get(
        f"/spaces/venues/{venue.id}/repair-slices").get_data(as_text=True)

    # The picker already excludes a nested room, so asserting its option
    # is absent proves nothing. What the suggestion controls is the tick:
    # a row arrives ticked only when a legal parent was found for it.
    assert f'name="apply_{orphan.id}"' in body
    checkbox = re.search(
        rf'name="apply_{orphan.id}" value="1"\s*(checked)?>', body)
    assert checkbox is not None
    assert checkbox.group(1) is None, "suggested a room that cannot parent"
