"""Which spaces a TechOps request may show, and what each card knows."""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    Department,
    EventCycle,
    Space,
    SpaceAssignment,
    TechOpsRequestSpace,
    User,
    Venue,
    WorkItem,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROUTING_STRATEGY_CATEGORY,
    SPACE_KIND_FREEFORM,
    SPACE_KIND_ROOM,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes.work.techops.form_utils import (
    capture_state_snapshot,
    replace_lines,
    replace_spaces,
)
from app.routes.work.techops.line_grain import (
    EthernetDrop,
    PhoneHandset,
    PhoneLine,
    RequestAnswers,
    SOURCE_NEW,
    SpaceAnswer,
)
from app.routes.work.techops.spaces import (
    offerable_spaces,
    picker_candidates,
    shared_with,
    space_cards,
)
from app.seeds.bootstrap import (
    seed_approval_groups,
    seed_techops_service_types,
    seed_work_types,
)


def _answers(spaces=(), action="save_draft"):
    return RequestAnswers(
        primary_contact_name="Ada", primary_contact_email="ada@magfest.org",
        additional_notes="", no_services_needed=False, action=action,
        spaces=tuple(spaces),
    )


def _card_to_answer(card):
    """Rebuild the SpaceAnswer a re-submitted, untouched card would post.

    Used to simulate "save, reopen, save again without editing" without
    going through the HTTP form: the card's redisplay fields are exactly
    what a template would echo back into hidden/pre-filled inputs.
    """
    return SpaceAnswer(
        space_id=card["space"].id,
        display_name=card["display_name"],
        answer=card["answer"],
        no_services_reason=card["no_services_reason"],
        wifi_requested=card["wifi_requested"],
        wifi_declined_reason=card["wifi_declined_reason"],
        wifi_description=card["wifi_description"],
        ethernet_drops=card["drops"],
        phone_lines=card["phone_lines"],
        notes=card["notes"],
    )


@pytest.fixture
def cycle_with_venue(app):
    """One venue, one event cycle pointed at it, and one department holding
    one of two active rooms at that venue."""
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, sort_order=1,
                       venue_id=venue.id)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()

    assigned = Space(venue_id=venue.id, name="Assigned Room", code="ASSIGNED",
                     kind=SPACE_KIND_ROOM, is_active=True)
    unassigned = Space(venue_id=venue.id, name="Unassigned Room", code="UNASSIGNED",
                       kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add_all([assigned, unassigned])
    db.session.flush()

    db.session.add(SpaceAssignment(
        space_id=assigned.id, event_cycle_id=cycle.id, department_id=dept.id))
    db.session.commit()

    return cycle, venue, assigned, unassigned


@pytest.fixture
def other_venue_space(app, cycle_with_venue):
    """A space at a different venue. Must never be offerable at cycle_with_venue."""
    other_venue = Venue(code="HIL", name="A Different Hotel")
    db.session.add(other_venue)
    db.session.flush()
    space = Space(venue_id=other_venue.id, name="Room Elsewhere", code="ELSEWHERE",
                 kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(space)
    db.session.commit()
    return space


@pytest.fixture
def archived_space(app, cycle_with_venue):
    """A retired room at the right venue. Must never be offerable."""
    _, venue, _, _ = cycle_with_venue
    space = Space(venue_id=venue.id, name="Retired Room", code="RETIRED",
                 kind=SPACE_KIND_ROOM, is_active=False)
    db.session.add(space)
    db.session.commit()
    return space


@pytest.fixture
def other_event_popup(app, cycle_with_venue):
    """A pop-up space built for a different event at the same venue."""
    _, venue, _, _ = cycle_with_venue
    other_cycle = EventCycle(code="SMF2026", name="Super MAGFest 2026",
                             is_active=True, is_default=False, sort_order=2)
    db.session.add(other_cycle)
    db.session.flush()
    popup = Space(venue_id=venue.id, name="Last Year's Pop-up", code="POPUP26",
                 kind=SPACE_KIND_FREEFORM, is_active=True,
                 event_cycle_id=other_cycle.id)
    db.session.add(popup)
    db.session.commit()
    return popup


@pytest.fixture
def cycle_without_venue(app, cycle_with_venue):
    """An event with no venue set, in a database that already has real,
    active, correctly-scoped spaces (cycle_with_venue's two rooms). A
    fixture that leaves the spaces table empty cannot tell a correct
    "no venue means nothing offered" from a query that is broken outright
    and returns everything, or nothing, regardless of venue."""
    cycle = EventCycle(code="NOVENUE", name="No Venue Yet",
                       is_active=True, is_default=False, sort_order=3)
    db.session.add(cycle)
    db.session.commit()
    return cycle


@pytest.fixture
def techops_draft(app, cycle_with_venue):
    """A DRAFT TechOps work item for cycle_with_venue's department.

    Seeds the bootstrap catalog rather than a hand-rolled subset:
    conftest's db.create_all() skips Alembic data migrations, so the
    TechOps service types exist nowhere else in a test run.
    """
    cycle, venue, assigned, unassigned = cycle_with_venue
    seed_techops_service_types(seed_approval_groups(seed_work_types()))

    user = User(id="test:techops_user", email="techops@test.local",
               display_name="TechOps Tester", is_active=True)
    db.session.add(user)
    db.session.flush()

    dept = Department.query.filter_by(code="TESTDEPT").one()
    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    db.session.add(WorkTypeConfig(
        work_type_id=work_type.id, url_slug="techops", public_id_prefix="TEC",
        line_detail_type="techops", routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    ))
    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=user.id,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT, public_id="SMF2027-TESTDEPT-TEC-1",
        created_by_user_id=user.id,
    )
    db.session.add(work_item)
    db.session.commit()
    return work_item


def test_offerable_covers_the_venue_not_just_the_assignment(app, cycle_with_venue):
    """A department may add any room at the venue. Assignment is not
    required; the card says so instead."""
    cycle, venue, assigned, unassigned = cycle_with_venue
    ids = {s.id for s in offerable_spaces(cycle)}
    assert assigned.id in ids
    assert unassigned.id in ids


def test_offerable_excludes_archived_and_other_venues(app, cycle_with_venue,
                                                      other_venue_space,
                                                      archived_space):
    cycle, *_ = cycle_with_venue
    ids = {s.id for s in offerable_spaces(cycle)}
    assert other_venue_space.id not in ids
    assert archived_space.id not in ids


def test_offerable_excludes_a_popup_belonging_to_another_event(
        app, cycle_with_venue, other_event_popup):
    cycle, *_ = cycle_with_venue
    assert other_event_popup.id not in {s.id for s in offerable_spaces(cycle)}


def test_an_event_with_no_venue_offers_nothing(app, cycle_without_venue):
    assert offerable_spaces(cycle_without_venue) == []


def test_an_unassigned_offerable_space_is_a_picker_candidate_not_a_card(
        app, techops_draft, cycle_with_venue):
    """Task 10: a space nobody has picked yet is not an automatic card, so
    the picker has something to offer. space_cards() only carries the
    assigned space plus whatever the request already holds; see
    test_a_space_the_request_already_holds_survives_going_unofferable
    for the flagged-once-held case this test used to cover instead."""
    cycle, venue, assigned, unassigned = cycle_with_venue
    cards = space_cards(techops_draft,
                        department_id=techops_draft.portfolio.department_id,
                        event_cycle=cycle)
    by_id = {card["space"].id: card for card in cards}
    assert by_id[assigned.id]["is_assigned"] is True
    assert unassigned.id not in by_id

    candidate_ids = {s.id for s in picker_candidates(cards, offerable_spaces(cycle))}
    assert unassigned.id in candidate_ids
    assert assigned.id not in candidate_ids


def test_a_space_the_request_already_holds_survives_going_unofferable(
        app, techops_draft, cycle_with_venue):
    """A held space can stop being offerable at all (archived, moved off
    the venue) after a draft names it. The card must still render, flagged
    unassigned, or the draft silently loses what the requester already
    answered. Using a space that stays independently offerable would not
    exercise this: it would show up through the offerable set regardless
    of whether the held-but-unofferable branch exists."""
    cycle, venue, assigned, unassigned = cycle_with_venue
    db.session.add(TechOpsRequestSpace(
        work_item_id=techops_draft.id, space_id=unassigned.id, answer="NEEDS",
    ))
    unassigned.is_active = False
    db.session.commit()

    assert unassigned.id not in {s.id for s in offerable_spaces(cycle)}

    cards = space_cards(techops_draft,
                        department_id=techops_draft.portfolio.department_id,
                        event_cycle=cycle)
    by_id = {card["space"].id: card for card in cards}
    assert unassigned.id in by_id
    assert by_id[unassigned.id]["is_assigned"] is False
    assert by_id[unassigned.id]["answer"] == "NEEDS"
    assert by_id[unassigned.id]["answer_row"] is not None


def test_shared_with_names_the_other_department_holding_a_space(app, cycle_with_venue):
    cycle, venue, assigned, unassigned = cycle_with_venue
    other_dept = Department(code="OTHERDEPT", name="Other Department", is_active=True)
    db.session.add(other_dept)
    db.session.flush()
    db.session.add(SpaceAssignment(
        space_id=assigned.id, event_cycle_id=cycle.id, department_id=other_dept.id))
    db.session.commit()

    dept = Department.query.filter_by(code="TESTDEPT").one()
    result = shared_with([assigned.id], cycle.id, dept.id)
    assert result[assigned.id] == ["Other Department"]
    # The caller's own department never appears in its own shared_with.
    assert unassigned.id not in result


def test_a_dangling_share_handset_survives_an_unedited_resave(
        app, techops_draft, cycle_with_venue):
    """Room B shares Room A's phone line 1 and puts a handset in its own
    space. Room A's line is then cleared on a later save, which is a real
    sequence: the requester touched Room A's card, not Room B's. Room B's
    handset now stores with parent_line_id NULL, since replace_lines()
    cannot resolve a source that no longer names anything.

    Room B's card must still show the handset (not drop it silently), and
    resaving Room B's card exactly as redisplayed — the "reopen, change
    nothing, save" path — must not lose it a second time or flip WiFi's
    stored answer. Reproduces the review's repro end to end rather than
    asserting on `_redisplay_extras` in isolation.
    """
    cycle, venue, assigned, unassigned = cycle_with_venue
    department_id = techops_draft.portfolio.department_id

    # wifi_requested=True on Room B: WiFi is a tri-state now (needed/
    # declined/unanswered), and only an explicit "needed" answer produces
    # a line, even on a forced space (item 4 of the post-review fixes).
    # This test is about the dangling phone share, not WiFi, so it answers
    # WiFi explicitly to keep the stored-answer assertion below meaningful.
    initial = _answers([
        SpaceAnswer(space_id=assigned.id, display_name="Assigned Room", answer="NEEDS",
                   phone_lines=(PhoneLine(index=1, source=SOURCE_NEW, purpose="VOICE",
                                          internal_only=False, usage="Front desk",
                                          caller_id_name="ROOMA", handsets=()),)),
        SpaceAnswer(space_id=unassigned.id, display_name="Unassigned Room", answer="NEEDS",
                   wifi_requested=True,
                   phone_lines=(PhoneLine(index=1, source=f"{assigned.id}:1",
                                          purpose=None, internal_only=False,
                                          handsets=(PhoneHandset(location="B desk"),)),)),
    ])
    replace_spaces(techops_draft, initial)
    replace_lines(techops_draft, initial)
    db.session.commit()

    # Room A's card is cleared; Room B's is untouched, still naming a
    # share that no longer resolves.
    after_clear = _answers([
        SpaceAnswer(space_id=assigned.id, display_name="Assigned Room", answer="NEEDS"),
        SpaceAnswer(space_id=unassigned.id, display_name="Unassigned Room", answer="NEEDS",
                   wifi_requested=True,
                   phone_lines=(PhoneLine(index=1, source=f"{assigned.id}:1",
                                          purpose=None, internal_only=False,
                                          handsets=(PhoneHandset(location="B desk"),)),)),
    ])
    replace_spaces(techops_draft, after_clear)
    replace_lines(techops_draft, after_clear)
    db.session.commit()

    snapshot_after_clear = capture_state_snapshot(techops_draft)
    wifi_row = next(s for s in snapshot_after_clear["services"]
                    if s["code"] == "WIFI" and s["space_id"] == unassigned.id)
    desk_row = next(s for s in snapshot_after_clear["services"]
                    if s["code"] == "DESK_PHONE" and s["space_id"] == unassigned.id)
    assert wifi_row is not None
    assert desk_row["location"] == "B desk"

    cards = space_cards(techops_draft, department_id=department_id, event_cycle=cycle)
    by_id = {c["space"].id: c for c in cards}
    b_card = by_id[unassigned.id]
    assert len(b_card["phone_lines"]) == 1
    assert b_card["phone_lines"][0].source == ""
    assert b_card["phone_lines"][0].handsets[0].location == "B desk"
    assert b_card["wifi_forced"] is True

    # Resave exactly what the cards show, as an unedited "reopen and save"
    # would post.
    resubmitted = _answers([_card_to_answer(c) for c in cards if c["answer"]])
    replace_spaces(techops_draft, resubmitted)
    replace_lines(techops_draft, resubmitted)
    db.session.commit()

    assert capture_state_snapshot(techops_draft) == snapshot_after_clear


def test_a_share_above_an_owned_line_keeps_its_stored_order(
        app, techops_draft, cycle_with_venue):
    """Room B's own phone_lines list puts a share (of Room A's line) at
    slot 1 and its own new line at slot 2. expand_to_lines() stores the
    share's handset row before the owned line's PHONE_NUMBER row, so
    reconstruction must recover [share, owned] and not [owned, share]."""
    cycle, venue, assigned, unassigned = cycle_with_venue
    department_id = techops_draft.portfolio.department_id

    answers = _answers([
        SpaceAnswer(space_id=assigned.id, display_name="Assigned Room", answer="NEEDS",
                   phone_lines=(PhoneLine(index=1, source=SOURCE_NEW, purpose="VOICE",
                                          internal_only=False, usage="Front desk",
                                          caller_id_name="ROOMA", handsets=()),)),
        SpaceAnswer(space_id=unassigned.id, display_name="Unassigned Room", answer="NEEDS",
                   phone_lines=(
                       PhoneLine(index=1, source=f"{assigned.id}:1",
                                purpose=None, internal_only=False,
                                handsets=(PhoneHandset(location="Share handset"),)),
                       PhoneLine(index=2, source=SOURCE_NEW, purpose="VOICE",
                                internal_only=False, usage="B's own line",
                                caller_id_name="ROOMB", handsets=()),
                   )),
    ])
    replace_spaces(techops_draft, answers)
    replace_lines(techops_draft, answers)
    db.session.commit()

    cards = space_cards(techops_draft, department_id=department_id, event_cycle=cycle)
    by_id = {c["space"].id: c for c in cards}
    lines = by_id[unassigned.id]["phone_lines"]

    assert len(lines) == 2
    assert lines[0].source == f"{assigned.id}:1"
    assert lines[0].handsets[0].location == "Share handset"
    assert lines[1].source == SOURCE_NEW
    assert lines[1].caller_id_name == "ROOMB"


def test_a_picked_but_unanswered_space_gets_a_card_and_no_lines(
        app, techops_draft, cycle_with_venue):
    """Fix round 1 inverted this: a blank-answer card now writes a
    TechOpsRequestSpace row with answer NULL instead of being skipped
    (replace_spaces docstring), and produces no lines regardless of what
    was typed into its fields (line_grain._expand_space) — validate()
    only requires a real answer on submit, not on a draft save, but an
    unanswered card must never reach the order as work either way.
    held_ids finds the space through its row, same as any other held
    space, even after it goes unofferable."""
    cycle, venue, assigned, unassigned = cycle_with_venue
    department_id = techops_draft.portfolio.department_id

    answers = _answers([
        SpaceAnswer(space_id=unassigned.id, display_name="Unassigned Room", answer="",
                   ethernet_drops=(EthernetDrop(location="Wall", usage="Switch"),)),
    ])
    replace_spaces(techops_draft, answers)
    replace_lines(techops_draft, answers)
    unassigned.is_active = False
    db.session.commit()

    rows = {r.space_id: r for r in techops_draft.techops_spaces}
    assert rows[unassigned.id].answer is None
    assert not any(
        line.techops_detail and line.techops_detail.space_id == unassigned.id
        for line in techops_draft.lines
    )

    cards = space_cards(techops_draft, department_id=department_id, event_cycle=cycle)
    assert unassigned.id in {c["space"].id for c in cards}


def test_a_declined_wifi_with_no_reason_survives_a_reload(
        app, techops_draft, cycle_with_venue):
    """The exact bug the owner lost work to: before TechOpsRequestSpace
    carried its own wifi_requested column, "declined" was inferred from a
    non-empty wifi_declined_reason. A requester who picked "No WiFi needed
    here" and typed no reason left no evidence anywhere, and the card came
    back unanswered on reload. This room has no gear, so the decline needs
    no reason either way (invariant 2); that is what made the old
    inference lose the answer instead of merely rejecting a required
    field. Reading `row.wifi_requested` directly, not inferring it, is the
    fix under test.
    """
    cycle, venue, assigned, unassigned = cycle_with_venue
    department_id = techops_draft.portfolio.department_id

    answers = _answers([
        SpaceAnswer(space_id=assigned.id, display_name="Assigned Room",
                   answer="NEEDS", wifi_requested=False),
    ])
    replace_spaces(techops_draft, answers)
    replace_lines(techops_draft, answers)
    db.session.commit()

    row = techops_draft.techops_spaces[0]
    assert row.wifi_requested is False

    cards = space_cards(techops_draft, department_id=department_id, event_cycle=cycle)
    by_id = {c["space"].id: c for c in cards}
    assert by_id[assigned.id]["wifi_requested"] is False
    assert by_id[assigned.id]["wifi_declined_reason"] == ""
