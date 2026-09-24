"""The room-first request form, end to end through the routes."""
import html
import re

import pytest

from app import db
from app.models import (
    Department,
    EventCycle,
    Space,
    SpaceAssignment,
    SpaceEventOverride,
    TechOpsRequestSpace,
    TechOpsServiceType,
    User,
    UserRole,
    Venue,
    WorkItem,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROLE_SUPER_ADMIN,
    ROUTING_STRATEGY_CATEGORY,
    SPACE_KIND_ROOM,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.techops.form_utils import (
    ACTION_SUBMIT, replace_lines, replace_spaces,
)
from app.routes.work.techops.line_grain import (
    PhoneHandset,
    PhoneLine,
    RequestAnswers,
    SpaceAnswer,
)
from app.seeds.bootstrap import (
    seed_approval_groups,
    seed_techops_service_types,
    seed_work_types,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _extract_js_function(body, name):
    """Return one named JS function's source, brace-balanced, from a
    rendered page. Isolates the assertion to that function's own body
    rather than the whole page, so a rename or an unrelated string
    elsewhere on the page cannot make the assertion pass by accident."""
    start = body.index(f"function {name}(")
    i = body.index("{", start)
    depth = 0
    for i in range(i, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                break
    return body[start:i + 1]


def _preview_rows(body):
    """Parse rendered order-preview rows into (number, service, space,
    spec) tuples, in document order. Used to check row order and which
    column holds which value, not just that some row exists somewhere."""
    row_html = re.findall(r'<tr data-preview-line[^>]*>(.*?)</tr>', body, re.S)
    rows = []
    for row in row_html:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        rows.append(tuple(cell.strip() for cell in cells))
    return rows


def _seed_admin():
    admin = User(id="test:admin", email="admin@test.local",
                display_name="Test Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))
    return admin


def _seed_techops_worktype():
    """Seed the TechOps catalog and this test DB's WorkTypeConfig.

    conftest's db.create_all() skips Alembic data migrations, so the
    service type / approval group catalog rows exist nowhere else in a
    test run; every fixture in this file needs them.
    """
    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    db.session.add(WorkTypeConfig(
        work_type_id=work_type.id, url_slug="techops", public_id_prefix="TEC",
        line_detail_type="techops", routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    ))
    return work_type


@pytest.fixture
def techops_portfolio(app):
    """One venue, one event, one department holding one assigned room,
    ready to GET the New TechOps Request form."""
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
        "preview_url": f"/{cycle.code}/{dept.code}/techops/preview-lines",
        "latest_item": lambda: WorkItem.query.order_by(WorkItem.id.desc()).first(),
    }


@pytest.fixture
def other_venue_space(app, techops_portfolio):
    """A space at a different venue than techops_portfolio's. The picker
    never offers it; only a crafted POST can name it."""
    other_venue = Venue(code="HIL", name="A Different Hotel")
    db.session.add(other_venue)
    db.session.flush()
    space = Space(venue_id=other_venue.id, name="Room Elsewhere", code="ELSEWHERE",
                 kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(space)
    db.session.commit()
    return space


@pytest.fixture
def shared_space_portfolio(app):
    """One room assigned to both the requesting department and Staff Ops."""
    _seed_admin()
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, sort_order=1,
                       venue_id=venue.id)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    other_dept = Department(code="STAFFOPS", name="Staff Ops", is_active=True)
    db.session.add_all([cycle, dept, other_dept])
    db.session.flush()

    _seed_techops_worktype()

    room = Space(venue_id=venue.id, name="Shared Room", code="SHARED",
                kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(room)
    db.session.flush()
    db.session.add_all([
        SpaceAssignment(space_id=room.id, event_cycle_id=cycle.id, department_id=dept.id),
        SpaceAssignment(space_id=room.id, event_cycle_id=cycle.id, department_id=other_dept.id),
    ])
    db.session.commit()

    return {
        "cycle": cycle, "department": dept, "other_department": other_dept,
        "room": room,
        "new_request_url": f"/{cycle.code}/{dept.code}/techops/new",
    }


@pytest.fixture
def request_with_picked_space(app):
    """A DRAFT request that already answered a room its department does
    not hold, for the edit page's "not currently assigned" flag."""
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

    work_type = _seed_techops_worktype()

    room = Space(venue_id=venue.id, name="Unassigned Room", code="UNASN",
                kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(room)
    db.session.flush()
    # No SpaceAssignment row: the department picked a room it does not
    # hold, which the card must flag rather than silently drop.

    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id="test:admin",
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="SMF2027-TESTDEPT-TEC-1",
        created_by_user_id="test:admin",
    )
    db.session.add(work_item)
    db.session.flush()
    db.session.add(TechOpsRequestSpace(
        work_item_id=work_item.id, space_id=room.id, answer="NEEDS"))
    db.session.commit()

    return {
        "cycle": cycle, "department": dept, "work_item": work_item,
        "venue": venue, "picked_room": room,
        "edit_url": f"/{cycle.code}/{dept.code}/techops/item/{work_item.public_id}/edit",
    }


def test_the_form_renders_exactly_one_card_for_the_assigned_space(app, client,
                                                                  techops_portfolio):
    """Renamed from a name that promised a count this body never checked."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert "Expo Hall E" in body
    assert body.count('name="space_ids"') == 1
    # Both conditional blocks are in the DOM (nothing here requires
    # scripting), but a brand-new, unanswered card shows neither: item 1
    # of the post-review fixes. Server-rendered, not script-hidden.
    assert "Hardwired ethernet" in body
    assert re.search(r'data-needs-block\s+style="display:\s*none;"', body)
    assert re.search(r'data-nothing-needed-block\s+style="display:\s*none;"', body)


def test_a_needs_card_with_nothing_filled_in_does_not_summarize_as_nothing_needed(
        app, client, techops_portfolio):
    """Owner feedback round 2, item 1 (bug). A collapsed card whose radio
    says "Needs services" must not show the summary "Nothing needed" — the
    two contradicted each other before this fix, since summarizeCard's
    fallback ignored which radio was checked and always named the
    NOTHING branch. No JS runtime exists in this suite (see the live-
    preview-script test above for the established pattern of asserting on
    the shipped function's own isolated source), so this checks the
    isolated summarizeCard body for the specific branch the fix added
    rather than the page's prose. Breaks if summarizeCard's fallback
    reverts to the bare ternary `parts.length ? parts.join(' - ') :
    'Nothing needed'`, or if the NEEDS/NOTHING check is removed."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    fn_src = _extract_js_function(body, "summarizeCard")

    assert "parts.length ? parts.join(' - ') : 'Nothing needed'" not in fn_src
    assert '[data-space-answer-radio][value="NEEDS"]:checked' in fn_src
    assert '[data-space-answer-radio][value="NOTHING"]:checked' in fn_src
    # The fallback branch order: NOTHING wins on "Nothing needed", NEEDS
    # gets its own distinct string, and both must return before the
    # unconditional old fallback would ever run.
    assert "if (nothingChecked) return 'Nothing needed';" in fn_src
    assert "if (needsChecked) return" in fn_src
    needs_return = fn_src.split("if (needsChecked) return")[1].split(";")[0]
    assert "Nothing needed" not in needs_return


def test_every_form_on_the_page_carries_a_csrf_token(app, client,
                                                     techops_portfolio):
    """conftest sets WTF_CSRF_ENABLED False, so a missing token passes the
    whole suite and returns 400 in a browser. Count them instead."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert body.count("<form") == body.count('name="csrf_token"')


def test_a_shared_space_names_the_other_department(app, client,
                                                   shared_space_portfolio):
    _login(client, "test:admin")
    body = html.unescape(
        client.get(shared_space_portfolio["new_request_url"]).get_data(as_text=True))
    assert "also assigned to Staff Ops" in body


def test_an_unassigned_space_on_the_request_is_flagged(app, client,
                                                       request_with_picked_space):
    _login(client, "test:admin")
    body = html.unescape(
        client.get(request_with_picked_space["edit_url"]).get_data(as_text=True))
    assert "not currently assigned" in body.lower()


def test_posting_a_space_at_another_venue_is_rejected(app, client,
                                                      techops_portfolio,
                                                      other_venue_space):
    """Bypasses the UI entirely. The picker never offers this space."""
    _login(client, "test:admin")
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "SUBMIT",
        "space_ids": str(other_venue_space.id),
        f"space_{other_venue_space.id}_answer": "NEEDS",
    }, follow_redirects=True)
    body = html.unescape(response.get_data(as_text=True))
    assert "not available at this event" in body


def test_an_event_with_no_venue_tells_the_requester_to_contact_hotels(
        app, client):
    """offerable_spaces() returns [] for a venue-less event (spaces.py:37-38).
    The form must explain that instead of rendering an empty, unexplained
    picker (spaces.py:33-35, owned by this task)."""
    _seed_admin()
    cycle = EventCycle(code="NOVENUE", name="No Venue Yet",
                       is_active=True, is_default=True, sort_order=1)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()
    _seed_techops_worktype()
    db.session.commit()

    _login(client, "test:admin")
    body = client.get(f"/{cycle.code}/{dept.code}/techops/new").get_data(as_text=True)
    # Not a bare "Hotels" check: the dev view-as header's approver picker
    # already contains "Approver: Hotels Team" regardless of this feature.
    assert "Hotels request channel" in body


@pytest.fixture
def two_rooms_one_with_an_owned_line(app):
    """A DRAFT request with two assigned rooms; Room A already has a phone
    line that owns its own number. Room B's card is the one that should
    offer to share it."""
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

    work_type = _seed_techops_worktype()

    room_a = Space(venue_id=venue.id, name="Room A", code="ROOMA",
                  kind=SPACE_KIND_ROOM, is_active=True)
    room_b = Space(venue_id=venue.id, name="Room B", code="ROOMB",
                  kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add_all([room_a, room_b])
    db.session.flush()
    db.session.add_all([
        SpaceAssignment(space_id=room_a.id, event_cycle_id=cycle.id, department_id=dept.id),
        SpaceAssignment(space_id=room_b.id, event_cycle_id=cycle.id, department_id=dept.id),
    ])

    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id="test:admin",
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="SMF2027-TESTDEPT-TEC-1",
        created_by_user_id="test:admin",
    )
    db.session.add(work_item)
    db.session.flush()

    answers = RequestAnswers(
        primary_contact_name="Ada", primary_contact_email="ada@magfest.org",
        additional_notes="", no_services_needed=False, action="save_draft",
        spaces=(
            SpaceAnswer(space_id=room_a.id, display_name="Room A", answer="NEEDS",
                       phone_lines=(PhoneLine(index=1, source="NEW", purpose="VOICE",
                                              internal_only=False, usage="Front desk",
                                              caller_id_name="ROOMA", handsets=()),)),
            SpaceAnswer(space_id=room_b.id, display_name="Room B", answer="NEEDS"),
        ),
    )
    replace_spaces(work_item, answers)
    replace_lines(work_item, answers)
    db.session.commit()

    return {
        "cycle": cycle, "department": dept, "work_item": work_item,
        "room_a": room_a, "room_b": room_b,
        "edit_url": f"/{cycle.code}/{dept.code}/techops/item/{work_item.public_id}/edit",
    }


def test_a_phone_line_can_be_pointed_at_another_rooms_number(
        app, client, two_rooms_one_with_an_owned_line):
    """A label assertion here would pass if "Room A" merely appears
    somewhere else on the page (it does, in Room A's own card header), so
    this checks the option value that a submit actually reads."""
    _login(client, "test:admin")
    room_a = two_rooms_one_with_an_owned_line["room_a"]
    body = client.get(two_rooms_one_with_an_owned_line["edit_url"]).get_data(as_text=True)
    assert f'value="{room_a.id}:1"' in body


@pytest.fixture
def request_with_a_two_handset_line(app, techops_portfolio):
    """A DRAFT request whose one room has a phone line with two placed
    handsets, for checking the handset repeating group renders existing
    rows plus exactly one blank."""
    dept = techops_portfolio["department"]
    cycle = techops_portfolio["cycle"]
    room = techops_portfolio["room"]

    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id="test:admin",
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="SMF2027-TESTDEPT-TEC-1",
        created_by_user_id="test:admin",
    )
    db.session.add(work_item)
    db.session.flush()

    answers = RequestAnswers(
        primary_contact_name="Ada", primary_contact_email="ada@magfest.org",
        additional_notes="", no_services_needed=False, action="save_draft",
        spaces=(
            SpaceAnswer(space_id=room.id, display_name="Expo Hall E", answer="NEEDS",
                       phone_lines=(PhoneLine(
                           index=1, source="NEW", purpose="VOICE", internal_only=False,
                           usage="Front desk", caller_id_name="EXPOE",
                           handsets=(PhoneHandset(location="Position 1"),
                                    PhoneHandset(location="Position 2"))),)),
        ),
    )
    replace_spaces(work_item, answers)
    replace_lines(work_item, answers)
    db.session.commit()

    return {
        "room": room,
        "edit_url": f"/{cycle.code}/{dept.code}/techops/item/{work_item.public_id}/edit",
    }


def test_handsets_render_existing_rows_plus_one_blank_with_no_count_field(
        app, client, request_with_a_two_handset_line):
    """handset_count was a read-only field the parser never read; with
    scripting off a requester could never use it to add a second handset.
    A handset row still renders existing rows plus one blank by design
    (out of scope for item 2: what is inside a phone line stays untouched,
    unlike the phone line and ethernet-drop containers one level up)."""
    _login(client, "test:admin")
    room = request_with_a_two_handset_line["room"]
    body = client.get(request_with_a_two_handset_line["edit_url"]).get_data(as_text=True)

    assert f'name="space_{room.id}_PHONE_line_1_handset_count"' not in body
    for n in (1, 2, 3):
        assert f'name="space_{room.id}_PHONE_line_1_handset_{n}_location"' in body
    assert f'name="space_{room.id}_PHONE_line_1_handset_4_location"' not in body


def test_a_department_wide_radio_channel_survives_a_validation_failure(
        app, client, techops_portfolio):
    """The same bug class redisplay_cards() fixes for space cards, applied
    to section 3: a validation failure must not silently drop a radio
    channel the requester already typed."""
    _login(client, "test:admin")
    response = client.post(techops_portfolio["new_request_url"], data={
        # No primary_contact_name: guaranteed validation failure.
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "service_RADIO_CHANNEL_instance_1_location": "Tech-9",
        "service_RADIO_CHANNEL_instance_1_usage": "Just-typed channel usage",
    })
    body = response.get_data(as_text=True)
    assert "Tech-9" in body
    assert "Just-typed channel usage" in body


def test_no_clone_placeholder_escapes_its_template(app, client, techops_portfolio):
    """A repeating-group clone carries __IDX__ in its field names until the
    script rewrites it. Inside a <template> the browser never submits it.
    Outside one, it posts a literal "__IDX__" field that the parser cannot
    read, and the requester's row vanishes with nothing failing.
    """
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    outside = re.sub(r"<template\b.*?</template>", "", body, flags=re.S)
    assert not re.findall(r'name="([^"]*__IDX__[^"]*)"', outside)
    # Guard against the assertion passing because the markers are gone.
    assert re.findall(r'name="([^"]*__IDX__[^"]*)"', body)


def test_a_failed_submit_preserves_every_kind_of_just_typed_space_field(
        app, client, techops_portfolio):
    """redisplay_cards() had zero coverage of its own: a mutation making it
    return `cards` unchanged passed the full suite. Post one of each field
    kind it's responsible for and check each survives a validation
    failure (missing contact name guarantees one)."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "1",
        f"space_{room.id}_WIFI_description": "Just-typed WiFi description",
        f"space_{room.id}_ETHERNET_drop_1_location": "Just-typed drop location",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Just-typed drop usage",
        f"space_{room.id}_PHONE_line_1_purpose": "VOICE",
        f"space_{room.id}_PHONE_line_1_caller_id_name": "typedcid",
        f"space_{room.id}_PHONE_line_1_handset_1_location": "Just-typed handset location",
        f"space_{room.id}_notes": "Just-typed per-space notes",
    })
    body = html.unescape(response.get_data(as_text=True))

    assert "Just-typed WiFi description" in body
    assert "Just-typed drop usage" in body
    assert "Just-typed handset location" in body
    assert "Just-typed per-space notes" in body
    # caller_id_name is upper-cased at parse time.
    assert "TYPEDCID" in body
    # Exactly the typed drop, no trailing blank row (item 2): a second,
    # never-typed drop row here is the regression the owner reported.
    assert f'name="space_{room.id}_ETHERNET_drop_1_location"' in body
    assert f'name="space_{room.id}_ETHERNET_drop_2_location"' not in body


def test_wifi_radios_show_what_was_just_submitted_not_the_forced_default(
        app, client, techops_portfolio):
    """Add a drop, leave WiFi unanswered, submit: validate() correctly
    rejects it ("say why it needs no WiFi, or tick WiFi"), and both radios
    must redisplay unchecked, matching what was actually posted.
    `wifi_forced` explains why a decline would need a reason (the
    callout), it must not also pick an answer the requester never gave."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_ETHERNET_drop_1_location": "Back wall",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Switch uplink",
        # WIFI_enabled deliberately omitted: neither radio was posted.
    })
    body = response.get_data(as_text=True)
    assert "no WiFi, or tick WiFi" in body  # validate()'s error landed
    radios = re.findall(
        rf'<input[^>]*name="space_{room.id}_WIFI_enabled"[^>]*>', body)
    assert len(radios) == 2
    assert not any("checked" in r for r in radios)


def test_wifi_description_field_shows_only_for_an_explicit_needed_answer(
        app, client, techops_portfolio):
    """Item 3's first reveal rule: "Who and what is on this WiFi?" shows
    only when "WiFi needed" is chosen, not for a decline and not while
    unanswered. Missing contact name guarantees the redisplay path."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "1",
    })
    body = response.get_data(as_text=True)
    assert not re.search(
        r'data-wifi-description-field\s+style="display:\s*none;"', body)


def test_wifi_description_field_hidden_for_an_unanswered_wifi_question(
        app, client, techops_portfolio):
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        # WIFI_enabled omitted: unanswered.
    })
    body = response.get_data(as_text=True)
    assert re.search(
        r'data-wifi-description-field\s+style="display:\s*none;"', body)


def test_wifi_decline_reason_field_shows_on_a_plain_decline_with_no_gear(
        app, client, techops_portfolio):
    """Round-3 fix item 2: the reason field used to show only when the
    decline was on a forced space (recomputed from the DOM), which meant
    it flickered in and out as the requester typed into an unrelated
    ethernet drop. It now shows whenever "No WiFi needed here" is picked,
    whether or not a reason is required; validate() still enforces the
    requirement (invariant 2), not the field's visibility."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "0",
    })
    body = response.get_data(as_text=True)
    assert not re.search(
        r'data-wifi-declined-field\s+style="display:\s*none;"', body)


def test_wifi_decline_reason_field_shows_when_the_space_is_forced(
        app, client, techops_portfolio):
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "0",
        f"space_{room.id}_ETHERNET_drop_1_location": "Back wall",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Switch uplink",
    })
    body = response.get_data(as_text=True)
    assert not re.search(
        r'data-wifi-declined-field\s+style="display:\s*none;"', body)


def test_the_forced_wifi_banner_no_longer_claims_wifi_was_auto_included(
        app, client, techops_portfolio):
    """Round-3 fix item 4: the banner used to read "Included automatically,
    based on the selections made," which stopped being true once WiFi
    started requiring an explicit answer (no line is ever auto-added). It
    still shows for a forced space (gear present), reworded to say what is
    actually true: an answer is required, and a decline needs a reason."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_ETHERNET_drop_1_location": "Back wall",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Switch uplink",
    })
    body = response.get_data(as_text=True)
    assert "Included automatically" not in body
    assert "This space has wired gear" in body


def test_wifi_decline_reason_field_hidden_when_wifi_is_needed_not_declined(
        app, client, techops_portfolio):
    """The field is gated on the decline radio, not just "answered": a
    "WiFi needed" answer must not also show the decline-reason field."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "1",
    })
    body = response.get_data(as_text=True)
    assert re.search(
        r'data-wifi-declined-field\s+style="display:\s*none;"', body)


def test_submit_rejects_a_needs_card_with_no_wifi_answer_on_a_forced_space(
        app, client, techops_portfolio):
    """Item 4, end to end: a forced space that never answers WiFi is
    refused by name, folded into the same message as an explicit decline
    with no reason."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_ETHERNET_drop_1_location": "Back wall",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Switch uplink",
    })
    body = html.unescape(response.get_data(as_text=True))
    assert "Expo Hall E: this space has wired gear, so say why it needs no WiFi, or tick WiFi." in body
    assert response.status_code == 200
    assert WorkItem.query.count() == 0


def test_submit_rejects_a_needs_card_with_no_wifi_answer_and_no_gear(
        app, client, techops_portfolio):
    """Item 4's core rule: every "Needs services" card must answer WiFi,
    not only a forced one."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
    })
    body = html.unescape(response.get_data(as_text=True))
    assert "Expo Hall E: say whether this space needs WiFi." in body
    assert response.status_code == 200
    assert WorkItem.query.count() == 0


def test_the_answered_spaces_counter_is_gone(app, client, techops_portfolio):
    """Owner feedback round 2, item 5: the "N of M spaces answered" rule
    was judged too fuzzy to maintain and was deleted outright, not just
    hidden. Locks down that a reader does not quietly bring the counter
    markup back while leaving spaces.py's helper deleted, or vice versa."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert "spaces answered" not in body
    assert "data-space-counter" not in body


def test_no_with_scripting_off_caption_remains_under_any_add_button(
        app, client, techops_portfolio):
    """Item 5: the scripting-off design (a caption under every "+ Add
    another..." button, section 5's own fallback Save Draft button) was
    the maintainer's own deviation from the approved design, not the
    owner's, and is gone. The plain <select> space picker and the space
    card radios are the fallbacks that remain. Checked against the
    caption's own wording, not the phrase "scripting off" generally,
    since a code comment elsewhere in the page legitimately uses it to
    explain the item-1 default's no-script behavior."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert "save the draft first" not in body.lower()


# ---- Task 10: the space picker ----

@pytest.fixture
def unassigned_offerable_space(app, techops_portfolio):
    """A second space at techops_portfolio's venue the department does not
    hold. offerable_spaces() includes it; space_cards() does not — it is
    exactly what the picker exists to offer."""
    venue = techops_portfolio["venue"]
    space = Space(venue_id=venue.id, name="Woodrow Wilson C", code="WWC",
                 kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(space)
    db.session.commit()
    return space


def test_the_picker_offers_a_space_the_department_does_not_hold(
        app, client, techops_portfolio, unassigned_offerable_space):
    """Assert on the option's value, not just the name appearing somewhere
    on the page: the card for the assigned room also renders text, and a
    weaker assertion would pass even if the picker offered nothing."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert 'id="space-picker-select"' in body
    picker = body.split('id="space-picker-select"')[1].split("</select>")[0]
    assert f'value="{unassigned_offerable_space.id}"' in picker
    assert "Woodrow Wilson C" in picker
    # Not shown as its own card: the picker is the only place it appears.
    assert f'data-space-card="{unassigned_offerable_space.id}"' not in body
    assert f'name="space_{unassigned_offerable_space.id}_answer"' not in body


def test_the_picker_does_not_offer_a_space_already_on_the_request(
        app, client, request_with_picked_space):
    """Needs at least one space in each state (already on the request, and
    genuinely offerable) or an empty picker and a correctly filtered one
    are indistinguishable."""
    venue = request_with_picked_space["venue"]
    picked_room = request_with_picked_space["picked_room"]
    other = Space(venue_id=venue.id, name="Marriott Ballroom", code="MARB",
                 kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(other)
    db.session.commit()

    _login(client, "test:admin")
    body = client.get(request_with_picked_space["edit_url"]).get_data(as_text=True)
    assert 'id="space-picker-select"' in body
    picker = body.split('id="space-picker-select"')[1].split("</select>")[0]
    assert f'value="{other.id}"' in picker
    assert f'value="{picked_room.id}"' not in picker


def test_the_picker_excludes_a_space_at_another_venue(
        app, client, techops_portfolio, unassigned_offerable_space,
        other_venue_space):
    """other_venue_space is active and would otherwise look offerable;
    only offerable_spaces() (venue-scoped) should ever reach the picker.
    unassigned_offerable_space guarantees the picker actually renders, so
    this can't pass on an empty picker the way an unconditional check on
    an absent control would."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert 'id="space-picker-select"' in body
    picker = body.split('id="space-picker-select"')[1].split("</select>")[0]
    assert f'value="{other_venue_space.id}"' not in picker


def test_an_event_with_no_venue_offers_no_picker(app, client):
    """Task 9 already covers the "Hotels request channel" message for a
    venue-less event; Task 10 must not also render an empty, unexplained
    picker control beside it."""
    _seed_admin()
    cycle = EventCycle(code="NOVENUE", name="No Venue Yet",
                       is_active=True, is_default=True, sort_order=1)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()
    _seed_techops_worktype()
    db.session.commit()

    _login(client, "test:admin")
    body = client.get(f"/{cycle.code}/{dept.code}/techops/new").get_data(as_text=True)
    assert "Hotels request channel" in body
    assert 'id="space-picker-select"' not in body


def test_a_venue_with_no_spaces_at_all_explains_the_picker_is_missing(app, client):
    """offerable_spaces() returns [] for a venue with a zero-space catalog,
    distinct from a venue-less event (spaces.py has both cases)."""
    _seed_admin()
    venue = Venue(code="EMPTY", name="Empty Venue")
    db.session.add(venue)
    db.session.flush()
    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, sort_order=1,
                       venue_id=venue.id)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()
    _seed_techops_worktype()
    db.session.commit()

    _login(client, "test:admin")
    body = client.get(f"/{cycle.code}/{dept.code}/techops/new").get_data(as_text=True)
    assert "Allocation Map" in body
    assert 'id="space-picker-select"' not in body


def test_the_picker_label_has_an_empty_state_with_no_cards_above_it(
        app, client):
    """Item 5: "A room or space not listed above" only makes sense once a
    card is listed above it. A department with nothing assigned or held
    yet, at a venue that does have spaces, sees the picker as the whole
    section, so the label must not point at a list that is not there."""
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
    space = Space(venue_id=venue.id, name="Expo Hall E", code="EXPOE",
                 kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(space)
    db.session.commit()
    # No SpaceAssignment: this department holds nothing yet, so
    # space_cards() returns no cards even though the venue has a space.

    _login(client, "test:admin")
    body = client.get(f"/{cycle.code}/{dept.code}/techops/new").get_data(as_text=True)
    assert f'data-space-card="{space.id}"' not in body
    assert 'id="space-picker-select"' in body
    assert "Choose a room or space to add" in body
    assert "A room or space not listed above" not in body


def test_adding_a_space_on_a_new_request_redirects_to_the_edit_page_losslessly(
        app, client, techops_portfolio, unassigned_offerable_space):
    """Fix round 1, finding 2: create.py's add_space branch now redirects
    to the edit route (a render-in-place 403s on refresh, since the draft
    this POST creates makes techops_request_new's own can_create_primary
    check fail). The redirect is only safe because the picked space is
    now persisted (answer nullable, fix round 1 finding 1) rather than
    rebuilt in memory, so this proves the round trip with sentinel values
    across several field kinds rather than reasoning about it."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Sentinel Contact Name",
        "primary_contact_email": "sentinel-contact@magfest.org",
        "additional_notes": "Sentinel additional notes text",
        "action": "add_space",
        "space_ids": [str(room.id), str(unassigned_offerable_space.id)],
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_notes": "Sentinel per-space notes",
        f"space_{room.id}_WIFI_enabled": "1",
        f"space_{room.id}_WIFI_description": "Sentinel wifi description",
        f"space_{room.id}_ETHERNET_drop_1_location": "Sentinel drop location",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Sentinel drop usage",
        "service_RADIO_CHANNEL_instance_1_location": "Sentinel channel name",
        "service_RADIO_CHANNEL_instance_1_usage": "Sentinel channel usage",
    })
    assert response.status_code == 302
    location = response.headers["Location"]
    assert f"/techops/item/" in location and location.endswith("/edit")

    body = html.unescape(client.get(location).get_data(as_text=True))
    # The new card, from the same field names every other card uses.
    assert f'data-space-card="{unassigned_offerable_space.id}"' in body
    assert f'name="space_{unassigned_offerable_space.id}_answer"' in body
    assert "Woodrow Wilson C" in body
    # Every sentinel, across contact info, notes, WiFi, ethernet, and a
    # department-wide service, survived the redirect.
    assert 'value="Sentinel Contact Name"' in body
    assert 'value="sentinel-contact@magfest.org"' in body
    assert "Sentinel additional notes text" in body
    assert "Sentinel per-space notes" in body
    assert "Sentinel wifi description" in body
    assert "Sentinel drop location" in body
    assert "Sentinel drop usage" in body
    assert "Sentinel channel name" in body
    assert "Sentinel channel usage" in body
    # Nothing else was offerable at this venue, so with the just-added
    # space now shown as a card, the picker has nothing left to offer.
    assert 'id="space-picker-select"' not in body


def test_add_space_creates_the_draft_so_the_next_save_edits_it(
        app, client, techops_portfolio, unassigned_offerable_space):
    """A brand-new request has no work item yet; add_space must create the
    draft (like Save Draft would) and redirect to its edit route, so a
    second save posted from that page updates the same item rather than
    creating a duplicate."""
    _login(client, "test:admin")
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "add_space",
        "space_ids": str(unassigned_offerable_space.id),
    })
    edit_url = response.headers["Location"]
    assert WorkItem.query.count() == 1

    client.post(edit_url, data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(unassigned_offerable_space.id),
        f"space_{unassigned_offerable_space.id}_answer": "NOTHING",
    })
    assert WorkItem.query.count() == 1


def test_an_unanswered_pick_survives_a_draft_reload(
        app, client, techops_portfolio, unassigned_offerable_space):
    """Fix round 1, finding 1: TechOpsRequestSpace.answer is nullable and
    replace_spaces() now persists every space, so a space picked but not
    yet answered "NEEDS"/"NOTHING" survives a save and a genuinely fresh
    page load — a separate GET, not the redisplay-from-`answers` response
    that added it."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": [str(room.id), str(unassigned_offerable_space.id)],
        f"space_{room.id}_answer": "NOTHING",
        f"space_{unassigned_offerable_space.id}_notes": "Sentinel unanswered note",
        # No space_{unassigned_offerable_space.id}_answer posted: opened
        # through the picker, not yet answered.
    })
    assert response.status_code == 302
    work_item = WorkItem.query.one()
    row = TechOpsRequestSpace.query.filter_by(
        work_item_id=work_item.id, space_id=unassigned_offerable_space.id).one()
    assert row.answer is None

    edit_url = (f"/{techops_portfolio['cycle'].code}/"
               f"{techops_portfolio['department'].code}/techops/item/"
               f"{work_item.public_id}/edit")
    body = html.unescape(client.get(edit_url).get_data(as_text=True))
    assert f'data-space-card="{unassigned_offerable_space.id}"' in body
    assert "Sentinel unanswered note" in body
    assert "Woodrow Wilson C" in body


def test_submit_still_rejects_an_unanswered_picked_space(
        app, client, techops_portfolio, unassigned_offerable_space):
    """validate() must keep refusing a SUBMIT with any space left
    unanswered, by name — persisting an unanswered pick (finding 1) must
    not let one through review just because it now has a row."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "submit",
        "space_ids": [str(room.id), str(unassigned_offerable_space.id)],
        f"space_{room.id}_answer": "NOTHING",
        # unassigned_offerable_space left unanswered.
    })
    body = html.unescape(response.get_data(as_text=True))
    assert response.status_code == 200  # validation failure, not submitted
    assert ("Woodrow Wilson C: say whether this space needs services or "
           "needs nothing.") in body
    assert WorkItem.query.count() == 0



# ---- Task 11: the order preview ----

@pytest.fixture
def one_space_payload(techops_portfolio):
    """A posted form for the one assigned room, answered with enough gear
    (WiFi, one drop, one phone line with one handset) to create more than
    one line. A payload that created zero lines could not distinguish a
    correct count from a preview and a submit that both happen to agree on
    nothing."""
    room_id = techops_portfolio["space_id"]
    return {
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room_id),
        f"space_{room_id}_answer": "NEEDS",
        f"space_{room_id}_WIFI_enabled": "1",
        f"space_{room_id}_WIFI_description": "Staff laptops",
        f"space_{room_id}_ETHERNET_drop_1_location": "Back wall",
        f"space_{room_id}_ETHERNET_drop_1_usage": "Switch uplink",
        f"space_{room_id}_PHONE_line_1_purpose": "VOICE",
        f"space_{room_id}_PHONE_line_1_caller_id_name": "EXPOE",
        f"space_{room_id}_PHONE_line_1_handset_1_location": "Front counter",
    }


def test_the_preview_and_submit_agree_on_what_gets_created(
        app, client, techops_portfolio, one_space_payload):
    """The argument for the single-function design. If this fails, a second
    implementation of the grain rules has appeared somewhere.

    Round-1 review, item 6: this used to post "SUBMIT" (upper case) while
    ACTION_SUBMIT is "submit", so it silently took the draft branch and
    the item was never actually submitted; the headline test of this
    task's whole premise had never exercised submit. The status assertion
    below is what makes that mismatch impossible to reintroduce unnoticed."""
    _login(client, "test:admin")
    preview = client.post(techops_portfolio["preview_url"], data=one_space_payload)
    preview_count = preview.get_data(as_text=True).count('data-preview-line')
    assert preview_count == 4  # WIFI + ETHERNET + PHONE_NUMBER + DESK_PHONE

    client.post(techops_portfolio["new_request_url"],
               data={**one_space_payload, "action": ACTION_SUBMIT},
               follow_redirects=True)
    item = techops_portfolio["latest_item"]()
    assert item.status == WORK_ITEM_STATUS_SUBMITTED
    assert len(item.lines) == preview_count


def test_the_preview_writes_nothing(app, client, techops_portfolio,
                                    one_space_payload):
    before = WorkItem.query.count()
    _login(client, "test:admin")
    client.post(techops_portfolio["preview_url"], data=one_space_payload)
    assert WorkItem.query.count() == before


def test_the_preview_survives_a_half_finished_draft(app, client,
                                                    techops_portfolio):
    """A phone line whose source names a line that does not exist must not
    raise: the preview runs on every keystroke."""
    _login(client, "test:admin")
    response = client.post(techops_portfolio["preview_url"], data={
        "space_ids": str(techops_portfolio["space_id"]),
        f"space_{techops_portfolio['space_id']}_answer": "NEEDS",
        f"space_{techops_portfolio['space_id']}_PHONE_line_1_source": "999:1",
        f"space_{techops_portfolio['space_id']}_PHONE_line_1_handset_count": "1",
        f"space_{techops_portfolio['space_id']}_PHONE_line_1_handset_1_location": "Desk",
    })
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # Renders the dangling share honestly instead of crashing on a None
    # parent_index (line_grain.expand_to_lines' documented behavior).
    assert "Rings a number not yet requested." in body


def test_the_preview_names_the_service_the_space_and_the_ring_target(
        app, client, techops_portfolio, one_space_payload):
    """Checks the actual row content, not just the row count: a mutation
    that renders the right number of rows with the wrong labels would
    still pass a bare data-preview-line count."""
    _login(client, "test:admin")
    body = client.post(techops_portfolio["preview_url"],
                       data=one_space_payload).get_data(as_text=True)
    assert "Expo Hall E" in body
    assert "Rings the number on line" in body
    assert "4 order lines" in body


def test_the_preview_rows_are_ordered_and_columned_correctly(
        app, client, techops_portfolio, one_space_payload):
    """Round-1 review, item 5 (medium). Two mutations passed all 29 tests
    that existed before this one: reversing the row list, and swapping the
    service/space <td>s. Reversal is not academic: "Rings the number on
    line N" names a list position, so reversing the list points the ring
    reference at the wrong row while a bare substring check still
    matches. Assert the exact ordered tuple of service labels and which
    column holds which value."""
    _login(client, "test:admin")
    body = client.post(techops_portfolio["preview_url"],
                       data=one_space_payload).get_data(as_text=True)
    rows = _preview_rows(body)
    assert [row[1] for row in rows] == [
        "WiFi access/coverage", "Hardwired ethernet", "Phone number", "Desk phone",
    ]
    assert [row[0] for row in rows] == ["1", "2", "3", "4"]
    assert all(row[2] == "Expo Hall E" for row in rows)
    # The PHONE_NUMBER row is row 3; the DESK_PHONE row (row 4) must ring
    # that row specifically, not just any row with matching text.
    assert "Rings the number on line 3." in rows[3][3]


def test_section_5_no_longer_carries_its_own_save_draft_button(
        app, client, request_with_a_two_handset_line):
    """The scripting-off design (section 5's own fallback Save Draft
    button, distinct from the bottom one) was a deviation from the
    approved design and is gone; the plain <select> space picker and the
    space-card radios are the only fallbacks that remain. This replaces
    test_the_no_script_save_draft_button_in_section_5_performs_a_real_save,
    which exercised the button this test now proves is absent."""
    _login(client, "test:admin")
    body = client.get(
        request_with_a_two_handset_line["edit_url"]).get_data(as_text=True)
    assert "order-preview-saved-note" not in body
    assert "Showing the order as of last save" not in body
    # Exactly one Save Draft button remains: the bottom one.
    assert body.count('name="action" value="save_draft"') == 1


def test_the_preview_does_not_call_validate(app, client, techops_portfolio):
    """An empty, unanswered request has no primary contact name and would
    fail validate() with 'Primary contact name is required.' The preview
    must not show that: a requester mid-typing has many errors, and the
    preview's job is a count, not a nag. Checked through the session's
    flash queue, not the fragment's body: the fragment never renders
    flashes either way, so a body-text assertion alone would pass even if
    the route called validate() and flashed every error it returned."""
    _login(client, "test:admin")
    response = client.post(techops_portfolio["preview_url"], data={})
    assert response.status_code == 200
    with client.session_transaction() as sess:
        assert not sess.get("_flashes")


def test_the_new_request_page_has_no_saved_order_yet(app, client,
                                                      techops_portfolio):
    """A brand-new request has nothing on disk to preview; the fallback
    section must say so rather than showing a stale table. Round-1 review,
    item 7: it must also not claim to show "the order as of last save"
    when nothing has ever been saved."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert "No lines yet" in body
    assert body.count('data-preview-line') == 0
    assert "as of last save" not in body


def test_an_edited_drafts_page_shows_its_own_saved_order(
        app, client, request_with_a_two_handset_line):
    """An existing draft's edit page (a plain GET, before any script has
    run) must show that draft's own persisted lines (rows_from_saved_lines),
    not an empty state and not a recomputation that could drift from what
    is on disk."""
    _login(client, "test:admin")
    body = client.get(
        request_with_a_two_handset_line["edit_url"]).get_data(as_text=True)
    # PHONE_NUMBER + 2 handsets. No WIFI: the fixture never answers WiFi,
    # and an unanswered card produces no line even on a forced space
    # (item 4 of the post-review fixes) — this was 4 rows, including an
    # auto-added WIFI line, before that fix.
    assert body.count('data-preview-line') == 3


def test_the_live_preview_script_binds_to_the_request_form_not_the_dev_header(
        app, client, techops_portfolio):
    """Round-1 review, item 1 (blocker). base.html includes the dev "view
    as" header above the content block whenever DEV_LOGIN_ENABLED or
    BETA_TESTING_MODE is on; a super admin in that configuration (staging)
    sees the header's own role-override form as document order's first
    <form>. `document.querySelector('form')` would silently bind the live
    preview to that form instead of the request form. Checked against the
    script's own source, since a document-order assertion alone would stay
    green if the header ever moved after the request form in markup while
    the script still picked "whichever form is first"."""
    app.config["BETA_TESTING_MODE"] = True
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    # Confirms the header actually rendered in this test, not just that the
    # config flag was set.
    assert body.count("<form") >= 2
    assert "role-override" in body
    assert "document.querySelector('form')" not in body
    assert "preview.closest('form')" in body


@pytest.fixture
def request_holding_an_archived_room(app):
    """A DRAFT request answered for a room that is archived (is_active
    flipped off) after the draft already holds it. space_cards() keeps it
    on the request as a held_extra space regardless; offerable_spaces()
    (the venue catalog) no longer offers it."""
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

    work_type = _seed_techops_worktype()

    room = Space(venue_id=venue.id, name="Soon Archived Room", code="ARCHV",
                kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(room)
    db.session.flush()

    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id="test:admin",
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="SMF2027-TESTDEPT-TEC-1",
        created_by_user_id="test:admin",
    )
    db.session.add(work_item)
    db.session.flush()

    answers = RequestAnswers(
        primary_contact_name="Ada", primary_contact_email="ada@magfest.org",
        additional_notes="", no_services_needed=False, action="save_draft",
        spaces=(SpaceAnswer(space_id=room.id, display_name="Soon Archived Room",
                           answer="NEEDS", wifi_requested=True,
                           wifi_description="Badge scanners"),),
    )
    replace_spaces(work_item, answers)
    replace_lines(work_item, answers)
    db.session.commit()

    # Archived after the draft already holds it: the exact ordering the
    # review's repro describes.
    room.is_active = False
    db.session.commit()

    return {
        "cycle": cycle, "department": dept, "work_item": work_item, "room": room,
        "edit_url": f"/{cycle.code}/{dept.code}/techops/item/{work_item.public_id}/edit",
        "preview_url": f"/{cycle.code}/{dept.code}/techops/item/{work_item.public_id}/preview-lines",
    }


def test_the_preview_still_shows_a_line_for_a_room_archived_after_it_was_held(
        app, client, request_holding_an_archived_room):
    """Round-1 review, item 2 (high). A room this draft already holds must
    not silently disappear from the live preview just because it stopped
    being offerable after the draft was saved: replace_lines() still
    creates its line on the next save regardless, and the preview must
    accept the same set of spaces the save path does."""
    _login(client, "test:admin")
    room = request_holding_an_archived_room["room"]
    body = client.post(
        request_holding_an_archived_room["preview_url"],
        data={
            "primary_contact_name": "Ada",
            "primary_contact_email": "ada@magfest.org",
            "action": "save_draft",
            "space_ids": str(room.id),
            f"space_{room.id}_answer": "NEEDS",
            f"space_{room.id}_WIFI_enabled": "1",
            f"space_{room.id}_WIFI_description": "Badge scanners",
        }).get_data(as_text=True)
    assert body.count('data-preview-line') == 1


def test_the_no_script_fallback_also_shows_the_archived_rooms_line(
        app, client, request_holding_an_archived_room):
    """Same guarantee as the live preview, for the plain-GET rendering
    that reads rows_from_saved_lines() straight off the persisted draft."""
    _login(client, "test:admin")
    body = html.unescape(client.get(
        request_holding_an_archived_room["edit_url"]).get_data(as_text=True))
    assert body.count('data-preview-line') == 1


@pytest.fixture
def request_for_an_aliased_room(app):
    """An assigned, active room with a SpaceEventOverride alias for this
    event, answered with real gear so it produces a line."""
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

    room = Space(venue_id=venue.id, name="Potomac Ballroom C", code="POTC",
                kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(room)
    db.session.flush()
    db.session.add(SpaceAssignment(
        space_id=room.id, event_cycle_id=cycle.id, department_id=dept.id))
    db.session.add(SpaceEventOverride(
        space_id=room.id, event_cycle_id=cycle.id, alias="Main Stage"))
    db.session.commit()

    return {
        "cycle": cycle, "department": dept, "room": room,
        "new_request_url": f"/{cycle.code}/{dept.code}/techops/new",
        "preview_url": f"/{cycle.code}/{dept.code}/techops/preview-lines",
    }


def test_the_preview_shows_the_events_alias_not_the_venue_catalog_name(
        app, client, request_for_an_aliased_room):
    """Round-1 review, item 4 (medium). The card shows "Main Stage"
    (SpaceEventOverride.alias); the preview must show the same name, not
    Potomac Ballroom C, the underlying Space.name a requester who filled
    in the card never sees anywhere else on the page."""
    _login(client, "test:admin")
    room = request_for_an_aliased_room["room"]
    body = client.post(
        request_for_an_aliased_room["preview_url"],
        data={
            "primary_contact_name": "Ada",
            "primary_contact_email": "ada@magfest.org",
            "action": "save_draft",
            "space_ids": str(room.id),
            f"space_{room.id}_answer": "NEEDS",
            f"space_{room.id}_WIFI_enabled": "1",
            f"space_{room.id}_WIFI_description": "Badge scanners",
        }).get_data(as_text=True)
    assert "Main Stage" in body
    assert "Potomac Ballroom C" not in body


def test_the_no_script_fallback_also_shows_the_events_alias(
        app, client, request_for_an_aliased_room):
    _login(client, "test:admin")
    room = request_for_an_aliased_room["room"]
    cycle = request_for_an_aliased_room["cycle"]
    dept = request_for_an_aliased_room["department"]
    client.post(request_for_an_aliased_room["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "1",
        f"space_{room.id}_WIFI_description": "Badge scanners",
    })
    work_item = WorkItem.query.one()
    edit_url = f"/{cycle.code}/{dept.code}/techops/item/{work_item.public_id}/edit"
    body = html.unescape(client.get(edit_url).get_data(as_text=True))
    assert body.count('data-preview-line') == 1
    assert "Main Stage" in body
    assert "Potomac Ballroom C" not in body


def test_a_blank_picker_selection_does_not_error_a_normal_save(
        app, client, techops_portfolio):
    """The picker's placeholder option posts an empty string in space_ids
    when nothing was chosen; parse_form must treat that as "nothing
    added," not as a garbage id to reject."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": [str(room.id), ""],
        f"space_{room.id}_answer": "NOTHING",
    }, follow_redirects=True)
    body = response.get_data(as_text=True)
    assert "not available at this event" not in body
    assert "Draft saved" in body


# ---- Owner feedback round 2 ----

def test_the_per_card_button_is_a_real_submit_named_save_this_space(
        app, client, techops_portfolio):
    """Item 2: the old button said "Done with this space" and saved
    nothing (`type="button"`, no name/value, pure client-side collapse).
    Checked against the submit's field name and type, not the button's
    visible label alone: a mutation that renamed the label back without
    restoring the save would still show the right text."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert "Done with this space" not in body
    assert re.search(
        rf'<button[^>]*type="submit"[^>]*name="save_space_id"[^>]*value="{room.id}"',
        body)
    assert "Save this space" in body


def test_saving_one_card_persists_and_redirects_to_the_edit_form_at_that_card(
        app, client, techops_portfolio):
    """Item 2, end to end on a brand-new request (create.py). The old
    behaviour sent every save to the read-only detail page; this checks
    the redirect Location header directly; `follow_redirects` alone would
    hide a wrong destination behind a plausible-looking final page."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_WIFI_enabled": "1",
        f"space_{room.id}_WIFI_description": "Save-this-space sentinel",
        "save_space_id": str(room.id),
        # No "action" field posted: the per-card button does not name one,
        # matching what the template actually sends.
    })
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/techops/item/" in location and location.endswith(f"/edit?open={room.id}")

    work_item = techops_portfolio["latest_item"]()
    assert work_item is not None
    assert work_item.techops_detail.primary_contact_name == "Ada"
    body = client.get(location).get_data(as_text=True)
    assert "Save-this-space sentinel" in body


def test_saving_one_card_on_an_existing_draft_redirects_back_to_it_too(
        app, client, request_with_a_two_handset_line):
    """Item 2 on edit.py's path (an existing draft), which is a distinct
    code path from create.py above and had its own separate redirect to
    fix."""
    _login(client, "test:admin")
    room = request_with_a_two_handset_line["room"]
    response = client.post(request_with_a_two_handset_line["edit_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_PHONE_line_1_purpose": "VOICE",
        f"space_{room.id}_PHONE_line_1_caller_id_name": "EXPOE",
        "save_space_id": str(room.id),
    })
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.endswith(f"/edit?open={room.id}")


def test_bottom_save_draft_returns_to_the_form_not_the_detail_page(
        app, client, techops_portfolio):
    """Item 2's other button: today both save paths land on the read-only
    detail page. The bottom Save Draft carries no save_space_id, so the
    redirect must omit ?open= while still landing on the edit form."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NOTHING",
    })
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.endswith("/edit")
    assert "open=" not in location


def test_submit_still_lands_on_the_detail_page_not_the_form(
        app, client, techops_portfolio, one_space_payload):
    """Only a draft save changed destination. A submitted item leaves
    DRAFT status, so redirecting it back to the edit form would 409;
    the detail page is still correct here."""
    _login(client, "test:admin")
    response = client.post(techops_portfolio["new_request_url"],
                           data={**one_space_payload, "action": ACTION_SUBMIT})
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/edit" not in location
    item = techops_portfolio["latest_item"]()
    assert location.endswith(f"/item/{item.public_id}")


def test_cancel_still_leaves_for_the_detail_page(app, client,
                                                 request_with_a_two_handset_line):
    """Item 2 is explicit that Cancel's behaviour is unchanged: it is a
    plain link, not a form submission, and must keep leaving DRAFT edits
    for the read-only page rather than saving anything."""
    _login(client, "test:admin")
    body = client.get(
        request_with_a_two_handset_line["edit_url"]).get_data(as_text=True)
    assert re.search(
        r'<a class="btn btn-muted" href="[^"]*/item/[^"]*"[^>]*>\s*Cancel\s*</a>',
        body)


def test_the_storage_only_control_is_gone_from_the_card(app, client,
                                                         techops_portfolio):
    """Item 3: removed completely, not hidden. Checked against the field
    name the old checkbox posted, which a mutation that merely re-labels
    or re-hides an equivalent control would still fail to reintroduce."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert f'name="space_{room.id}_storage_only"' not in body
    assert "storage only" not in body.lower()


def test_the_storage_only_column_is_gone_from_both_tables(app, techops_portfolio):
    """The model-level half of item 3, checked against the live table
    columns rather than re-reading the source of models.py."""
    from app.models import TechOpsLineDetail, TechOpsRequestSpace
    assert "storage_only" not in TechOpsLineDetail.__table__.columns.keys()
    assert "storage_only" not in TechOpsRequestSpace.__table__.columns.keys()


def test_every_card_and_the_section_offer_collapse_controls(
        app, client, techops_portfolio):
    """Item 4: a "Collapse all" / "Expand all" pair for the section, and
    every card collapsible regardless of whether it has been answered —
    the assigned room here is a brand-new, unanswered card."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    # Checked against the rendered markup, not the raw page text: the
    # script's own selectors also name these attributes, so a page with
    # the JS wired but the buttons never rendered would still contain the
    # substring.
    markup = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
    assert re.search(r"<button[^>]*data-collapse-all", markup)
    assert re.search(r"<button[^>]*data-expand-all", markup)
    # The summary element every card gets, whatever its answer state, is
    # what the header-click toggle and the section buttons collapse into.
    assert "data-space-card-summary" in markup


def test_collapse_controls_are_absent_when_there_are_no_cards_to_collapse(
        app, client):
    """The bulk controls are gated on `cards`, the same guard the space
    section itself uses for a venue-less event with nothing to answer."""
    _seed_admin()
    cycle = EventCycle(code="NOVENUE", name="No Venue Yet",
                       is_active=True, is_default=True, sort_order=1)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()
    _seed_techops_worktype()
    db.session.commit()

    _login(client, "test:admin")
    body = client.get(f"/{cycle.code}/{dept.code}/techops/new").get_data(as_text=True)
    # The script's own selectors name these attributes regardless of
    # whether any element carries them, so the check must look at markup,
    # not the raw page text (the script block would false-positive it).
    markup = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
    assert "data-collapse-all" not in markup
    assert "data-expand-all" not in markup


def test_no_services_needed_is_hidden_when_the_department_holds_a_space(
        app, client, techops_portfolio):
    """Item 6: validate() already refuses this combination server-side;
    this is the form agreeing so a requester is never offered a choice
    it would reject. Checked against the field name the checkbox posts,
    not the visible label, which a differently-worded but equivalent
    control could still satisfy."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert 'name="no_services_needed"' not in body


def test_no_services_needed_still_offered_with_zero_cards(app, client):
    """The counterpart to the test above: a department with nothing to
    answer individually must keep the one way it has to submit at all."""
    _seed_admin()
    cycle = EventCycle(code="NOVENUE", name="No Venue Yet",
                       is_active=True, is_default=True, sort_order=1)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()
    _seed_techops_worktype()
    db.session.commit()

    _login(client, "test:admin")
    body = client.get(f"/{cycle.code}/{dept.code}/techops/new").get_data(as_text=True)
    assert 'name="no_services_needed"' in body


# ---------------------------------------------------------------------
# Frequency-first layout: WiFi prominent, ethernet/phone/radio collapse.
# ---------------------------------------------------------------------

def _opening_tag(body, needle):
    """Return one element's full opening tag, found by a substring inside
    its attributes. Used to check for the boolean `open` attribute
    without depending on attribute order or the exact whitespace the
    template happens to emit around a conditional attribute."""
    start = body.index(needle)
    tag_start = body.rindex("<", 0, start)
    tag_end = body.index(">", start)
    return body[tag_start:tag_end + 1]


def _edit_url(portfolio):
    item = portfolio["latest_item"]()
    return (f"/{portfolio['cycle'].code}/{portfolio['department'].code}"
            f"/techops/item/{item.public_id}/edit")


def test_wifi_is_unboxed_and_first_ethernet_and_phone_collapse_untouched(
        app, client, techops_portfolio):
    """The measurable goal: a WiFi-only room must not be shown an ethernet
    or phone form at all until asked for. Breaks if WiFi reverts to the
    bordered `space-subsection` box (same weight as ethernet/phone), if
    WiFi is moved after either collapsible, or if an untouched card's
    ethernet/phone <details> render pre-opened."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)

    assert 'class="space-wifi-row"' in body
    assert '<details class="space-collapsible" data-service-subsection="WIFI"' not in body

    wifi_pos = body.index('data-service-subsection="WIFI"')
    ethernet_pos = body.index('data-service-subsection="ETHERNET"')
    phone_pos = body.index('data-service-subsection="PHONE"')
    assert wifi_pos < ethernet_pos < phone_pos

    assert "open" not in _opening_tag(body, 'data-service-subsection="ETHERNET"')
    assert "open" not in _opening_tag(body, 'data-service-subsection="PHONE"')


def test_ethernet_badge_reports_the_saved_drop_count_and_opens(
        app, client, techops_portfolio):
    """Item 3: a collapsed section holding content must say so. Breaks if
    the badge stops naming a unit ("2 drops"), or if a card with saved
    drops renders its ethernet <details> collapsed, hiding them from a
    requester who did not think to click."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada", "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_ETHERNET_drop_1_location": "Front left",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Tech table",
        f"space_{room.id}_ETHERNET_drop_2_location": "Back wall",
        f"space_{room.id}_ETHERNET_drop_2_usage": "Router feed",
    })
    body = client.get(_edit_url(techops_portfolio)).get_data(as_text=True)

    assert "open" in _opening_tag(body, 'data-service-subsection="ETHERNET"')
    badge = re.search(r'data-ethernet-count>([^<]*)<', body).group(1)
    assert "2 drops" in badge


def test_phone_badge_reports_lines_and_handsets(
        app, client, request_with_a_two_handset_line):
    """Matches the owner's own example format: "Phone · 1 line, 2
    handsets". Breaks if the handset count is dropped from the badge, or
    if the line count and handset count are transposed."""
    _login(client, "test:admin")
    body = client.get(request_with_a_two_handset_line["edit_url"]).get_data(as_text=True)

    assert "open" in _opening_tag(body, 'data-service-subsection="PHONE"')
    badge = re.search(r'data-phone-count>([^<]*)<', body).group(1)
    assert "1 line" in badge
    assert "2 handsets" in badge


def test_a_card_with_no_gear_shows_a_zero_count_and_no_ethernet_or_phone_form(
        app, client, techops_portfolio):
    """Item 3, reversed from the old rule: an empty section's badge used to
    stay blank, which is exactly what the owner said reads as "nothing
    here to check." The badge must now always show a number. Breaks if an
    untouched card's badge goes blank instead of reading "0 drops"/"0
    lines"."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    ethernet_badge = re.search(r'data-ethernet-count>([^<]*)<', body).group(1)
    phone_badge = re.search(r'data-phone-count>([^<]*)<', body).group(1)
    assert "0 drops" in ethernet_badge
    assert "0 lines" in phone_badge


def test_radio_channel_and_other_collapse_and_open_with_saved_content(
        app, client, techops_portfolio):
    """Item 2: section 3 collapses the same way space cards do. Breaks if
    either service renders open with nothing saved, or stays collapsed
    once it holds a saved channel or request.

    RADIO_CHANNEL's badge always shows a count, including zero, the same
    as ethernet/phone (fix-department-wide-checkboxes). OTHER carries no
    `data-service-count` element at all: a single description is not a
    repeating group, so any number there would be invented."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]

    # Untouched: both collapse. RADIO_CHANNEL's badge reads "0 channels";
    # OTHER has no badge element to read.
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    assert "open" not in _opening_tag(body, 'data-service-section="RADIO_CHANNEL"')
    assert "open" not in _opening_tag(body, 'data-service-section="OTHER"')
    radio_badge = re.search(r'data-service-section="RADIO_CHANNEL".*?data-service-count>([^<]*)<',
                            body, re.S).group(1)
    assert "0 channels" in radio_badge
    other_section = re.search(r'data-service-section="OTHER".*?</summary>', body, re.S).group(0)
    assert "data-service-count" not in other_section

    # Saved: both open, RADIO_CHANNEL's badge names what it holds.
    client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada", "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        "service_RADIO_CHANNEL_instance_1_location": "Tech-1",
        "service_RADIO_CHANNEL_instance_1_usage": "Ops chatter",
        "service_OTHER_description": "Cabling consultation",
    })
    body = client.get(_edit_url(techops_portfolio)).get_data(as_text=True)
    assert "open" in _opening_tag(body, 'data-service-section="RADIO_CHANNEL"')
    assert "open" in _opening_tag(body, 'data-service-section="OTHER"')
    radio_badge = re.search(r'data-service-section="RADIO_CHANNEL".*?data-service-count>([^<]*)<',
                            body, re.S).group(1)
    assert "1 channel" in radio_badge
    other_section = re.search(r'data-service-section="OTHER".*?</summary>', body, re.S).group(0)
    assert "data-service-count" not in other_section


def test_the_saved_ethernet_badge_agrees_with_what_the_script_would_count(
        app, client, techops_portfolio):
    """Item 3's own drift guard, made explicit: the server renders the
    badge from card.ethernet_count (spaces.count_filled_drops); the
    script recomputes it from the DOM via filledDropRows. Both apply the
    same rule — a row counts when either its location or its usage is
    non-blank — so the server's rendered count for a saved draft must
    equal a straight count of rows that rule would keep. This suite runs
    no JS, so filledDropRows' own source is checked directly below for
    the rule it must implement; this test checks the server's number
    against the same rule applied by hand to what was actually saved.
    Breaks if the two rules diverge: a drop with only usage filled (no
    location) must still count, matching form_utils._parse_drops."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    # Drop 1: location only. Drop 2: usage only. Both must count.
    client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada", "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_ETHERNET_drop_1_location": "Front left",
        f"space_{room.id}_ETHERNET_drop_1_usage": "",
        f"space_{room.id}_ETHERNET_drop_2_location": "",
        f"space_{room.id}_ETHERNET_drop_2_usage": "Router feed",
    })
    body = client.get(_edit_url(techops_portfolio)).get_data(as_text=True)
    badge = re.search(r'data-ethernet-count>([^<]*)<', body).group(1)
    assert "2 drops" in badge

    fn_src = _extract_js_function(body, "filledDropRows")
    # The bug this guards: checking only the location input undercounts a
    # drop whose usage is filled but whose location is still blank.
    assert "row.querySelector('input[type=\"text\"]')" in fn_src
    assert "row.querySelector('textarea')" in fn_src
    assert "loc.value.trim()" in fn_src and "use.value.trim()" in fn_src
    assert "||" in fn_src.split("return")[-1]


def test_filled_phone_line_rows_reads_the_scoped_purpose_select_and_handsets(
        app, client, techops_portfolio):
    """The pre-existing filledPhoneLineRows read `row.querySelector('select')`
    unscoped, which grabs the phone line's Number/source select (always a
    non-blank value once share options exist) ahead of its Purpose
    select, always counting a touched-nowhere line as filled. Fixed to
    read `[data-phone-purpose-field] select` specifically, and to count a
    line with only handsets filled in (no purpose or usage typed), which
    the parser also keeps (form_utils._parse_phone_lines: `not handsets`
    is part of the skip condition)."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)
    fn_src = _extract_js_function(body, "filledPhoneLineRows")
    assert "'[data-phone-purpose-field] select'" in fn_src
    assert "row.querySelector('select')" not in fn_src
    assert "data-handset-row" in fn_src


def test_zero_drops_renders_no_drop_rows_with_a_singular_add_button(
        app, client, techops_portfolio):
    """Item 2's empty-section case: an untouched card must show no drop
    row at all, and the button must read "+ Add a drop", not "...another
    drop" naming a row that does not exist yet. The container's own
    next-index must start at 1, not 2, with nothing rendered to clone
    against."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)

    assert f'name="space_{room.id}_ETHERNET_drop_1_location"' not in body
    match = re.search(
        r'data-drops-container[^>]*data-next-index="(\d+)"', body)
    assert match.group(1) == "1"
    # Scoped to the button's own rendered text, not the whole page: the
    # script sets the same string client-side after a click, which would
    # make a body-wide "not in" assertion pass for the wrong reason.
    btn = re.search(r'data-add-drop="\d+">\s*([^<]+?)\s*</button>', body).group(1)
    assert btn == "+ Add a drop"


def test_one_saved_drop_renders_exactly_one_drop_row(
        app, client, techops_portfolio):
    """The regression the owner reported: a section holding one drop must
    render exactly one drop row, Drop 1, with no Drop 2 placeholder to be
    mistaken for a saved row and "deleted". The next-index must pick up
    at 2, past the one real row."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada", "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_ETHERNET_drop_1_location": "Front left",
        f"space_{room.id}_ETHERNET_drop_1_usage": "Tech table",
    })
    body = client.get(_edit_url(techops_portfolio)).get_data(as_text=True)

    assert f'name="space_{room.id}_ETHERNET_drop_1_location"' in body
    assert f'name="space_{room.id}_ETHERNET_drop_2_location"' not in body
    match = re.search(
        r'data-drops-container[^>]*data-next-index="(\d+)"', body)
    assert match.group(1) == "2"
    btn = re.search(r'data-add-drop="\d+">\s*([^<]+?)\s*</button>', body).group(1)
    assert btn == "+ Add another drop"


def test_zero_phone_lines_renders_no_line_rows_with_a_singular_add_button(
        app, client, techops_portfolio):
    """Same rule as the drop container, applied to phone lines: nothing
    saved means nothing rendered, and the button names the state
    correctly ("+ Add a phone line")."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)

    assert f'name="space_{room.id}_PHONE_line_1_purpose"' not in body
    match = re.search(
        r'data-phone-lines-container[^>]*data-next-index="(\d+)"', body)
    assert match.group(1) == "1"
    btn = re.search(r'data-add-phone-line="\d+">\s*([^<]+?)\s*</button>', body).group(1)
    assert btn == "+ Add a phone line"


def test_one_saved_phone_line_renders_exactly_one_line_row(
        app, client, techops_portfolio):
    """The same regression as the drop case, for phone lines: one saved
    line must render as exactly Phone line 1, with no blank Phone line 2
    and next-index picking up past the one real row."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada", "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_PHONE_line_1_purpose": "VOICE",
        f"space_{room.id}_PHONE_line_1_usage": "Front desk",
    })
    body = client.get(_edit_url(techops_portfolio)).get_data(as_text=True)

    assert f'name="space_{room.id}_PHONE_line_1_purpose"' in body
    assert f'name="space_{room.id}_PHONE_line_2_purpose"' not in body
    match = re.search(
        r'data-phone-lines-container[^>]*data-next-index="(\d+)"', body)
    assert match.group(1) == "2"
    assert "+ Add another phone line" in body


def test_radio_channel_section_has_no_phantom_row_when_empty(
        app, client, techops_portfolio):
    """Item 2's own note that section 3 has the same pattern: with nothing
    saved, RADIO_CHANNEL used to render one blank instance row so the
    section was never truly empty. That phantom row must be gone, the Add
    button must read "+ Add a channel", and next-index must start at 1."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)

    assert 'name="service_RADIO_CHANNEL_instance_1_location"' not in body
    match = re.search(
        r'data-instances-container[^>]*data-next-index="(\d+)"', body)
    assert match.group(1) == "1"
    btn = re.search(
        r'data-add-instance="RADIO_CHANNEL"[^>]*>\s*([^<]+?)\s*</button>', body).group(1)
    assert btn == "+ Add a channel"


def test_one_saved_radio_channel_renders_exactly_one_channel_row(
        app, client, techops_portfolio):
    """One saved channel must render as exactly Channel 1, not Channel 1
    plus a blank Channel 2, and the Add button must switch to naming the
    now-real first channel ("+ Add another channel")."""
    _login(client, "test:admin")
    client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada", "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "service_RADIO_CHANNEL_instance_1_location": "Tech-1",
        "service_RADIO_CHANNEL_instance_1_usage": "Ops chatter",
    })
    body = client.get(_edit_url(techops_portfolio)).get_data(as_text=True)

    assert 'name="service_RADIO_CHANNEL_instance_1_location"' in body
    assert 'name="service_RADIO_CHANNEL_instance_2_location"' not in body
    match = re.search(
        r'data-instances-container[^>]*data-next-index="(\d+)"', body)
    assert match.group(1) == "2"
    assert "+ Add another channel" in body


def test_ethernet_and_phone_summaries_carry_the_catalog_description(
        app, client, techops_portfolio):
    """Item 1: a closed section must explain itself without being opened,
    and the sentence must come from TechOpsServiceType.description (seeded
    in app/seeds/bootstrap.py), not a second copy hand-typed into the
    template. Scoped to each section's own <details> so a match elsewhere
    on the page (e.g. section 3's own description reuse of similar
    wording) cannot pass this by accident."""
    _login(client, "test:admin")
    body = client.get(techops_portfolio["new_request_url"]).get_data(as_text=True)

    ethernet_section = re.search(
        r'data-service-subsection="ETHERNET".*?</details>', body, re.S).group(0)
    phone_section = re.search(
        r'data-service-subsection="PHONE".*?</details>', body, re.S).group(0)

    ethernet_type = TechOpsServiceType.query.filter_by(code="ETHERNET").one()
    phone_type = TechOpsServiceType.query.filter_by(code="PHONE_NUMBER").one()
    assert ethernet_type.description in ethernet_section
    assert phone_type.description in phone_section

    # The disclosure marker <summary> loses under `display: flex`; base.html
    # must supply its own glyph rather than leaving the section looking
    # like a plain, unopenable label.
    assert "space-collapsible-summary::before" in body


def test_the_disclosure_marker_swaps_between_open_and_closed(app, client, techops_portfolio):
    """The glyph must actually change state, not just exist once: an
    ethernet section with no saved drops (closed) and a phone section on
    a request with a saved line (open) must render with the two different
    marker rules in play."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada", "primary_contact_email": "ada@magfest.org",
        "action": "save_draft",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_PHONE_line_1_purpose": "VOICE",
        f"space_{room.id}_PHONE_line_1_usage": "Front desk",
    })
    body = client.get(_edit_url(techops_portfolio)).get_data(as_text=True)

    assert "open" not in _opening_tag(body, 'data-service-subsection="ETHERNET"')
    assert "open" in _opening_tag(body, 'data-service-subsection="PHONE"')
    assert "\\25B8" in body  # closed-state glyph, defined once in base.html
    assert "\\25BE" in body  # open-state glyph
