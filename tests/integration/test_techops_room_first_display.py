"""A reviewer's read-only pages show the facts a line now carries.

Task 12: space, parent-line reference, NO_SERVICES reason, and department-
assignment warning. Every assertion below reads from inside the target
line's own <tr id="line-N"> markup (see _line_row), not the page as a
whole — a room name or a line number appears more than once on these
pages, so a bare "in body" check would pass for the wrong reason.
"""
from __future__ import annotations

import re

import pytest

from app import db
from app.models import (
    Department,
    EventCycle,
    Space,
    SpaceAssignment,
    SpaceEventOverride,
    TechOpsLineDetail,
    TechOpsServiceType,
    User,
    UserRole,
    Venue,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROLE_SUPER_ADMIN,
    ROUTING_STRATEGY_CATEGORY,
    SPACE_KIND_ROOM,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.routes.work.techops.form_utils import replace_lines
from app.routes.work.techops.line_grain import (
    DepartmentWideAnswer,
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


def _line_row(body: str, line_number: int) -> str:
    """The exact <tr id="line-N">...</tr> markup for one line, and nothing
    else on the page. Assertions must search only within this string."""
    match = re.search(
        rf'<tr id="line-{line_number}"[^>]*>(.*?)</tr>', body, re.S)
    assert match, f"no <tr id=\"line-{line_number}\"> found in page"
    return match.group(1)


def _answers(spaces=(), department_wide=(), action="SAVE_DRAFT"):
    return RequestAnswers(
        primary_contact_name="Ada", primary_contact_email="ada@magfest.org",
        additional_notes="", no_services_needed=False, action=action,
        spaces=tuple(spaces), department_wide=tuple(department_wide),
    )


@pytest.fixture
def techops_reviewable_request(app):
    """A SUBMITTED TechOps request whose lines exercise every fact Task 12
    must surface: a combined space's composed name, a department-wide
    line, a resolved desk-phone parent, a dangling one, a NO_SERVICES
    reason, and a space the department does not hold.

    A throwaway earlier work item is saved first purely to advance the
    work_lines id sequence, so the target item's line_number (which
    restarts at 1) cannot coincidentally equal its work_line_id — a
    mutation that swapped one for the other would otherwise pass by luck
    alone in a single-item test database.
    """
    admin = User(id="test:admin", email="admin@test.local",
                display_name="Test Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))

    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    db.session.add(WorkTypeConfig(
        work_type_id=work_type.id, url_slug="techops", public_id_prefix="TEC",
        line_detail_type="techops", routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    ))

    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, sort_order=1,
                       venue_id=venue.id)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()

    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=admin.id,
    )
    db.session.add(portfolio)
    db.session.flush()

    # A room folded with two slices for this event: spaces_for_department()
    # must report it under the composed name, the same one the requester's
    # own pages show (compose_combined_name in app/models/spaces.py).
    primary = Space(venue_id=venue.id, name="RiverView 1", code="RV1",
                    kind=SPACE_KIND_ROOM, is_active=True)
    slice2 = Space(venue_id=venue.id, name="RiverView 2", code="RV2",
                   kind=SPACE_KIND_ROOM, is_active=True)
    slice3 = Space(venue_id=venue.id, name="RiverView 3", code="RV3",
                   kind=SPACE_KIND_ROOM, is_active=True)
    # Assigned to nobody: the "department does not hold this space" case.
    unheld = Space(venue_id=venue.id, name="Expo Hall Z", code="EXPOZ",
                   kind=SPACE_KIND_ROOM, is_active=True)
    storage = Space(venue_id=venue.id, name="Storage Room", code="STOR",
                    kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add_all([primary, slice2, slice3, unheld, storage])
    db.session.flush()

    db.session.add(SpaceAssignment(
        space_id=primary.id, event_cycle_id=cycle.id, department_id=dept.id))
    db.session.add(SpaceEventOverride(
        space_id=slice2.id, event_cycle_id=cycle.id,
        combined_into_space_id=primary.id))
    db.session.add(SpaceEventOverride(
        space_id=slice3.id, event_cycle_id=cycle.id,
        combined_into_space_id=primary.id))
    db.session.commit()

    # Throwaway earlier item: consumes work_lines ids before the real one.
    filler = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="SMF2027-TESTDEPT-TEC-1",
        created_by_user_id=admin.id,
    )
    db.session.add(filler)
    db.session.commit()
    replace_lines(filler, _answers(department_wide=(
        DepartmentWideAnswer(service_code="OTHER", description="Filler A"),
        DepartmentWideAnswer(service_code="OTHER", description="Filler B"),
        DepartmentWideAnswer(service_code="OTHER", description="Filler C"),
    )))
    db.session.commit()

    work_item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="SMF2027-TESTDEPT-TEC-2",
        created_by_user_id=admin.id,
    )
    db.session.add(work_item)
    db.session.commit()

    answers = _answers(spaces=[
        # Line 1: WIFI in the combined, department-held space. Line 2: a
        # phone number the department holds, owned here.
        SpaceAnswer(space_id=primary.id, display_name="RiverView 1/2/3",
                   answer="NEEDS", wifi_requested=True,
                   wifi_description="Badge scanners", phone_lines=(
                       PhoneLine(index=1, source="NEW", purpose="VOICE",
                                internal_only=False, usage="Will-call desk"),
                   )),
        # Line 3: a handset that shares the number above, but sits
        # physically in a space the department does not hold — its own
        # space must be reported, not its parent's.
        SpaceAnswer(space_id=unheld.id, display_name="Expo Hall Z",
                   answer="NEEDS", phone_lines=(
                       PhoneLine(index=1, source=f"{primary.id}:1",
                                purpose=None, internal_only=False,
                                handsets=(PhoneHandset(location="Counter 1"),)),
                   )),
        # Line 4: NO_SERVICES with a stated reason.
        SpaceAnswer(space_id=storage.id, display_name="Storage Room",
                   answer="NOTHING",
                   no_services_reason="Used for storage only, no gear."),
    ], department_wide=[
        # Line 5: department-wide, no space at all.
        DepartmentWideAnswer(service_code="RADIO_CHANNEL",
                             location="Ops 3", usage="Stage coordination"),
    ])
    replace_lines(work_item, answers)
    db.session.commit()

    lines_by_number = {l.line_number: l for l in work_item.lines}
    phone_number_line = next(
        l for l in lines_by_number.values()
        if l.techops_detail.service_type.code == "PHONE_NUMBER")
    desk_phone_id = TechOpsServiceType.query.filter_by(code="DESK_PHONE").one().id

    # A dangling handset: parent_line_id NULL, the state _redisplay_extras
    # documents as unrecoverable once the line it shared from is gone.
    # Crafted directly rather than through replace_lines(), which never
    # produces this shape on a normal save.
    dangling = WorkLine(
        work_item_id=work_item.id, line_number=max(lines_by_number) + 1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(dangling)
    db.session.flush()
    db.session.add(TechOpsLineDetail(
        work_line_id=dangling.id, service_type_id=desk_phone_id,
        space_id=primary.id, location="Dangling desk", parent_line_id=None,
    ))
    db.session.commit()

    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    db.session.commit()
    db.session.refresh(work_item)

    lines_by_number = {l.line_number: l for l in work_item.lines}
    return {
        "detail_url": f"/{cycle.code}/{dept.code}/techops/item/{work_item.public_id}",
        "line_review_url": lambda n: (
            f"/{cycle.code}/{dept.code}/techops/item/{work_item.public_id}/line/{n}/review"
        ),
        "work_item": work_item,
        "wifi_line_number": next(
            n for n, l in lines_by_number.items()
            if l.techops_detail.service_type.code == "WIFI"),
        "phone_number_line_number": phone_number_line.line_number,
        "handset_line_number": next(
            n for n, l in lines_by_number.items()
            if l.techops_detail.service_type.code == "DESK_PHONE"
            and l.techops_detail.parent_line_id is not None),
        "dangling_line_number": dangling.line_number,
        "no_services_line_number": next(
            n for n, l in lines_by_number.items()
            if l.techops_detail.service_type.code == "NO_SERVICES"),
        "department_wide_line_number": next(
            n for n, l in lines_by_number.items()
            if l.techops_detail.space_id is None),
        "primary_space_id": primary.id,
        "unheld_space_id": unheld.id,
    }


# ---------------------------------------------------------------------
# work_item_detail.html
# ---------------------------------------------------------------------

def test_a_combined_space_renders_its_composed_name(app, client, techops_reviewable_request):
    """Mutation: read Space.name directly instead of routing through
    space_cards()/compose_combined_name() and this fails, since the raw
    catalog name is "RiverView 1", not "RiverView 1/2/3"."""
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["detail_url"]).get_data(as_text=True)
    row = _line_row(body, r["wifi_line_number"])
    assert "RiverView 1/2/3" in row
    # Strip the correct composed name and confirm the uncomposed "RiverView
    # 1" alone is not also present anywhere in the row.
    assert "RiverView 1" not in row.replace("RiverView 1/2/3", "")


def test_a_department_wide_line_reads_as_department_wide_not_blank(
        app, client, techops_reviewable_request):
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["detail_url"]).get_data(as_text=True)
    row = _line_row(body, r["department_wide_line_number"])
    assert "Department-wide" in row


def test_a_desk_phone_names_its_parents_line_number(app, client, techops_reviewable_request):
    """Mutation: render detail.parent_line.work_line_id (the database id)
    in place of detail.parent_line.line_number and this fails, because the
    fixture guarantees the two numbers differ."""
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["detail_url"]).get_data(as_text=True)
    row = _line_row(body, r["handset_line_number"])
    expected = f"Rings the number on line {r['phone_number_line_number']}."
    assert expected in row


def test_a_dangling_handset_reads_honestly(app, client, techops_reviewable_request):
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["detail_url"]).get_data(as_text=True)
    row = _line_row(body, r["dangling_line_number"])
    assert "Rings a number not yet requested." in row
    assert "line None" not in row


def test_a_no_services_line_shows_its_reason(app, client, techops_reviewable_request):
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["detail_url"]).get_data(as_text=True)
    row = _line_row(body, r["no_services_line_number"])
    assert "Used for storage only, no gear." in row


def test_a_line_in_an_unheld_space_carries_the_warning(app, client, techops_reviewable_request):
    """The handset shares its number with a line owned in the held space,
    but sits physically in the unheld one; the warning must follow the
    handset's own space, not its parent's."""
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["detail_url"]).get_data(as_text=True)
    row = _line_row(body, r["handset_line_number"])
    assert "Not assigned to this department" in row


def test_a_line_in_a_held_space_carries_no_warning(app, client, techops_reviewable_request):
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["detail_url"]).get_data(as_text=True)
    row = _line_row(body, r["wifi_line_number"])
    assert "Not assigned to this department" not in row


# ---------------------------------------------------------------------
# line_review.html — spot-check the single-line page independently,
# since it renders the same facts through separate template logic.
# ---------------------------------------------------------------------

def test_line_review_page_names_the_parent_line_number(app, client, techops_reviewable_request):
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["line_review_url"](r["handset_line_number"])).get_data(as_text=True)
    assert f"Rings the number on line {r['phone_number_line_number']}." in body


def test_line_review_page_shows_the_composed_space_name(app, client, techops_reviewable_request):
    _login(client, "test:admin")
    r = techops_reviewable_request
    body = client.get(r["line_review_url"](r["wifi_line_number"])).get_data(as_text=True)
    assert "RiverView 1/2/3" in body
