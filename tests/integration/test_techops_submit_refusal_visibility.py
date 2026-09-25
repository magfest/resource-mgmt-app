"""A refused SUBMIT must be visibly different from a save.

Regression coverage for the bug the owner hit: "Submit for TechOps Review"
refused a submit and returned the edit form, which looked identical to a
successful "Save Draft". These tests pin the fix's three visible signals
(the panel, the card marker, and the redirect vs. render split) against
the exact same posted content, so a mutation that makes a save and a
refused submit converge again fails here even if the plain validate()
suite still passes.
"""
import re

import pytest

from app import db
from app.models import (
    Department,
    EventCycle,
    Space,
    SpaceAssignment,
    User,
    UserRole,
    Venue,
    WorkItem,
    ROLE_SUPER_ADMIN,
    ROUTING_STRATEGY_CATEGORY,
    SPACE_KIND_ROOM,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.models import WorkType, WorkTypeConfig
from app.seeds.bootstrap import (
    seed_approval_groups,
    seed_techops_service_types,
    seed_work_types,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_admin():
    admin = User(id="test:admin", email="admin@test.local",
                display_name="Test Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))
    return admin


def _seed_techops_worktype():
    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    db.session.add(WorkTypeConfig(
        work_type_id=work_type.id, url_slug="techops", public_id_prefix="TEC",
        line_detail_type="techops", routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    ))
    return work_type


def _panel_html(body):
    """Slice out just the submit-error-panel's own markup.

    Used so a test asserting a message "is in the panel" cannot pass
    because that text also appears somewhere else on the page (a flashed
    message, a card field). The panel holds exactly one <ul>.
    """
    start = body.index("data-submit-error-panel")
    end = body.index("</ul>", start) + len("</ul>")
    return body[start:end]


def _card_class_attr(body, space_id):
    """Return the class list on one space card's own wrapper div.

    Matched by id, not by searching for the room's name: two cards can
    share a name prefix, and a substring match on the name would not
    prove the id-keyed marker is what put the class there.
    """
    match = re.search(
        r'<div class="([^"]*)"\s+id="space-card-%d"' % space_id, body)
    assert match, f"no space card wrapper found for space {space_id}"
    return match.group(1)


@pytest.fixture
def techops_portfolio(app):
    """One venue, one event, one department holding one assigned room."""
    _seed_admin()
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, sort_order=1,
                       venue_id=venue.id)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()

    _seed_techops_worktype()

    room = Space(venue_id=venue.id, name="Expo Hall E", code="EXPOE",
                kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(room)
    db.session.flush()
    db.session.add(SpaceAssignment(
        space_id=room.id, event_cycle_id=cycle.id, department_id=dept.id))
    db.session.commit()

    return {
        "cycle": cycle, "venue": venue, "department": dept, "room": room,
        "space_id": room.id,
        "new_request_url": f"/{cycle.code}/{dept.code}/techops/new",
        "latest_item": lambda: WorkItem.query.order_by(WorkItem.id.desc()).first(),
    }


@pytest.fixture
def two_named_rooms(app):
    """Two assigned rooms whose names share a prefix, one that will be
    left unanswered and one that will be fully answered "nothing needed".
    Named after the task's own example (Azalea 1 / Azalea 10) to prove a
    blocked-card marker keyed on space_id cannot land on the wrong card
    the way a substring match on the name could."""
    _seed_admin()
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, sort_order=1,
                       venue_id=venue.id)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()

    _seed_techops_worktype()

    # The blocked room's name is deliberately the longer one: its error
    # text ("Azalea 10: say whether...") contains "Azalea 1" as a literal
    # substring, which is exactly the direction a naive
    # `card.display_name in error_message` check would false-positive on.
    blocked_room = Space(venue_id=venue.id, name="Azalea 10", code="AZ10",
                         kind=SPACE_KIND_ROOM, is_active=True)
    clean_room = Space(venue_id=venue.id, name="Azalea 1", code="AZ1",
                       kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add_all([blocked_room, clean_room])
    db.session.flush()
    db.session.add_all([
        SpaceAssignment(space_id=blocked_room.id, event_cycle_id=cycle.id,
                        department_id=dept.id),
        SpaceAssignment(space_id=clean_room.id, event_cycle_id=cycle.id,
                        department_id=dept.id),
    ])
    db.session.commit()

    return {
        "cycle": cycle, "department": dept,
        "blocked_room": blocked_room, "clean_room": clean_room,
        "new_request_url": f"/{cycle.code}/{dept.code}/techops/new",
    }


def _full_answer_payload(room_id):
    """A NEEDS card answered completely enough to pass validate()."""
    return {
        f"space_{room_id}_answer": "NEEDS",
        f"space_{room_id}_WIFI_enabled": "1",
        f"space_{room_id}_WIFI_description": "Staff laptops",
    }


def test_a_refused_submit_renders_the_panel_and_the_item_stays_draft(
        app, client, techops_portfolio):
    """The reported bug: SUBMIT with WiFi unanswered used to land back on
    an unmarked form indistinguishable from a save."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]

    # First save a real DRAFT, so there is a persisted item whose status
    # can be checked after the refused submit.
    client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
    })
    item = techops_portfolio["latest_item"]()
    assert item.status == WORK_ITEM_STATUS_DRAFT
    edit_url = f"/{techops_portfolio['cycle'].code}/{techops_portfolio['department'].code}/techops/item/{item.public_id}/edit"

    response = client.post(edit_url, data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        # WiFi left unanswered: validate() refuses this on submit only.
    })
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-submit-error-panel' in body

    db.session.refresh(item)
    assert item.status == WORK_ITEM_STATUS_DRAFT


def test_a_successful_submit_renders_no_panel_and_the_item_is_submitted(
        app, client, techops_portfolio):
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    payload = {
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        **_full_answer_payload(room.id),
    }
    response = client.post(techops_portfolio["new_request_url"], data=payload)
    # A successful submit redirects to the detail page; it never renders
    # the form (or the panel) at all, unlike a refused submit's 200.
    assert response.status_code == 302
    assert "/edit" not in response.headers["Location"]

    item = techops_portfolio["latest_item"]()
    assert item.status == WORK_ITEM_STATUS_SUBMITTED


def test_a_plain_draft_save_with_the_same_incomplete_content_renders_no_panel(
        app, client, techops_portfolio):
    """The heart of the bug: identical space content (answered NEEDS,
    WiFi never touched) that validate() would refuse on submit must save
    cleanly as a draft, with no panel and no error-shaped 200 render."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    payload = {
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        # WiFi left unanswered, same as the refused-submit test above.
    }
    response = client.post(techops_portfolio["new_request_url"], data=payload)
    # A draft save redirects; it does not fall into the render-with-errors
    # branch at all, so there is no page here to carry a panel on.
    assert response.status_code == 302
    assert "/edit" in response.headers["Location"]

    followed = client.get(response.headers["Location"])
    assert 'data-submit-error-panel' not in followed.get_data(as_text=True)

    item = techops_portfolio["latest_item"]()
    assert item.status == WORK_ITEM_STATUS_DRAFT


def test_the_panel_names_the_blocking_space_within_its_own_markup(
        app, client, techops_portfolio):
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
    })
    body = response.get_data(as_text=True)
    panel = _panel_html(body)
    assert "Expo Hall E" in panel
    assert "say whether this space needs WiFi" in panel
    # The entry links to the card it blocks, by id, not by name.
    assert f'href="#space-card-{room.id}"' in panel


def test_blocked_card_marker_is_keyed_by_space_id_not_name_substring(
        app, client, two_named_rooms):
    """Azalea 1 is left unanswered (blocking); Azalea 10 is fully answered
    "nothing needed". A name-substring implementation would risk matching
    Azalea 1's error text against Azalea 10's card too, since "Azalea 1"
    is a substring of "Azalea 10"."""
    _login(client, "test:admin")
    blocked = two_named_rooms["blocked_room"]
    clean = two_named_rooms["clean_room"]
    response = client.post(two_named_rooms["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": [str(blocked.id), str(clean.id)],
        f"space_{blocked.id}_answer": "NEEDS",
        # blocked.id's WiFi is left unanswered: this is the one refusal.
        f"space_{clean.id}_answer": "NOTHING",
    })
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "space-card-blocked" in _card_class_attr(body, blocked.id)
    assert "space-card-blocked" not in _card_class_attr(body, clean.id)


def test_a_draft_save_refused_for_an_unrelated_reason_still_shows_no_panel(
        app, client, techops_portfolio):
    """parse_form's structural errors (unlike validate()'s per-space
    checks) run on every action, draft included, so this is the one case
    that actually exercises the show_error_panel gate on a draft save
    rather than relying on validate()'s early return to keep `errors`
    empty. Without the action check, this save would show the panel too."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    payload = {
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
    }
    # One drop past MAX_DROPS_PER_SPACE (25): a parse-time overflow error
    # that fires regardless of action, unlike validate()'s completeness
    # checks.
    for n in range(1, 27):
        payload[f"space_{room.id}_ETHERNET_drop_{n}_location"] = f"Drop {n}"
        payload[f"space_{room.id}_ETHERNET_drop_{n}_usage"] = "Something"
    response = client.post(techops_portfolio["new_request_url"], data=payload)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "at most 25 ethernet drops" in body
    assert 'data-submit-error-panel' not in body


def test_a_refused_submit_shows_each_problem_exactly_once(app, client, techops_portfolio):
    """The reported duplication bug: a refused submit used to flash every
    validate() error above the panel that already listed it, so the owner
    saw "nothing has been requested for it" once as a red flash and once
    as the panel's only bullet. Counted, not just checked present, since
    `in` would pass even with the duplicate still there.
    """
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "0",
        f"space_{room.id}_WIFI_declined_reason": "No coverage needed",
        # No drops, no phone lines: the space answers NEEDS but requests
        # nothing, the owner's actual reported case.
    })
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.count("nothing has been requested for it") == 1
    assert "flash-message" not in body


def test_a_needs_services_space_with_nothing_requested_names_and_marks_its_card(
        app, client, techops_portfolio):
    """The owner's reported case: a space answered "Needs services",
    declined WiFi with a reason, no drops, no phone lines. That produces
    zero lines, but the space WAS answered, so the error must name the
    room and mark its card, not point at the generic "answer at least one
    space" message, which is for a request where nothing was answered."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "0",
        f"space_{room.id}_WIFI_declined_reason": "No coverage needed",
    })
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _panel_html(body)
    assert "Expo Hall E" in panel
    assert "nothing has been requested for it" in panel
    assert "This request would create no lines" not in body
    assert "space-card-blocked" in _card_class_attr(body, room.id)


def test_a_refused_submit_still_preserves_typed_values(app, client, techops_portfolio):
    """redisplay_cards() must keep doing its job once the refusal also
    renders a panel: the panel is additive, not a replacement for the
    existing preserve-on-failure behavior."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_ETHERNET_drop_1_location": "Just-typed drop location",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Just-typed drop usage",
        f"space_{room.id}_notes": "Just-typed per-space notes",
        # WiFi left unanswered: guarantees the submit is refused.
    })
    assert response.status_code == 200
    import html
    body = html.unescape(response.get_data(as_text=True))
    assert "Just-typed drop location" in body
    assert "Just-typed drop usage" in body
    assert "Just-typed per-space notes" in body
