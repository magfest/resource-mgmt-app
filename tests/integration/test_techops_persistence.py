"""Writing a parsed request to the database.

The interesting case is parent_line_id: expansion produces a positional
reference because the preview needs it before any row exists, and it is
resolved here, after the flush that assigns primary keys.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app import db
from app.models import (
    Department,
    EventCycle,
    Space,
    TechOpsLineDetail,
    TechOpsRequestSpace,
    TechOpsServiceType,
    User,
    Venue,
    WorkItem,
    WorkItemAuditEvent,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROUTING_STRATEGY_CATEGORY,
    SPACE_KIND_ROOM,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes.work.techops.form_utils import (
    audit_draft_edit,
    capture_form_snapshot,
    capture_state_snapshot,
    replace_lines,
    replace_spaces,
    upsert_request_detail,
)
from app.routes.work.techops.line_grain import (
    EthernetDrop,
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


# Same small builders as test_techops_line_grain.py. Duplicated rather than
# imported across the unit/integration boundary; both copies build plain
# dataclasses and drift would show up as a failing assertion, not a defect.
def _space(space_id=1, **kwargs):
    defaults = dict(
        space_id=space_id, display_name=f"Space {space_id}", answer="NEEDS",
        no_services_reason="", wifi_requested=False,
        wifi_declined_reason="", wifi_description="", ethernet_drops=(),
        phone_lines=(), notes="",
    )
    defaults.update(kwargs)
    return SpaceAnswer(**defaults)


def _answers(spaces=(), department_wide=(), action="SAVE_DRAFT",
             no_services_needed=False):
    return RequestAnswers(
        primary_contact_name="Ada", primary_contact_email="ada@magfest.org",
        additional_notes="", no_services_needed=no_services_needed,
        action=action,
        spaces=tuple(spaces), department_wide=tuple(department_wide),
    )


def _voice_line(index=1, source="NEW", handsets=1, purpose="VOICE", **kw):
    return PhoneLine(
        index=index, source=source, purpose=purpose,
        internal_only=kw.pop("internal_only", False),
        usage=kw.pop("usage", "Front counter"),
        caller_id_name=kw.pop("caller_id_name", "REGDESK"),
        handsets=tuple(PhoneHandset(location=f"Position {n}")
                       for n in range(1, handsets + 1)),
        **kw,
    )


@pytest.fixture(scope="function")
def techops_draft(app):
    """A DRAFT TechOps work item in a seeded portfolio.

    Seeds the bootstrap catalog (work types, approval groups, service
    types) rather than a hand-rolled subset: conftest's db.create_all()
    skips Alembic data migrations, so PHONE_NUMBER, DESK_PHONE, and
    NO_SERVICES exist nowhere else in a test run.
    """
    seed_techops_service_types(seed_approval_groups(seed_work_types()))

    user = User(
        id="test:techops_user", email="techops@test.local",
        display_name="TechOps Tester", is_active=True,
    )
    db.session.add(user)

    cycle = EventCycle(
        code="TST2026", name="Test Event 2026",
        is_active=True, is_default=True, sort_order=1,
    )
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()

    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    db.session.add(WorkTypeConfig(
        work_type_id=work_type.id, url_slug="techops",
        public_id_prefix="TEC", line_detail_type="techops",
        routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    ))

    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=user.id,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-TESTDEPT-TEC-1",
        created_by_user_id=user.id,
    )
    db.session.add(work_item)
    db.session.commit()

    return work_item


@pytest.fixture(scope="function")
def two_spaces(app):
    """Two active spaces at one venue, offerable to any event."""
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    owner = Space(venue_id=venue.id, name="Con Suite", code="CONSUITE",
                 kind=SPACE_KIND_ROOM, is_active=True)
    sharer = Space(venue_id=venue.id, name="Reg Desk", code="REGDESK",
                   kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add_all([owner, sharer])
    db.session.commit()
    return owner, sharer


def test_a_handset_ends_up_pointing_at_its_number(app, techops_draft, two_spaces):
    owner, sharer = two_spaces
    answers = _answers([
        _space(space_id=owner.id, phone_lines=(_voice_line(handsets=0),)),
        _space(space_id=sharer.id,
               phone_lines=(_voice_line(index=1, source=f"{owner.id}:1",
                                        handsets=1),)),
    ])
    replace_lines(techops_draft, answers)
    db.session.flush()

    details = (db.session.query(TechOpsLineDetail)
               .join(TechOpsLineDetail.work_line)
               .order_by(TechOpsLineDetail.work_line_id).all())
    handset = next(d for d in details
                   if d.service_type.code == "DESK_PHONE")
    number = next(d for d in details
                  if d.service_type.code == "PHONE_NUMBER")
    assert handset.parent_line_id == number.work_line_id
    assert handset.space_id == sharer.id


def test_a_handset_resolves_to_the_right_number_when_a_text_line_is_dropped(
    app, techops_draft, two_spaces
):
    """Two numbers exist, so a wrong parent_index mapping has somewhere
    else to land instead of the correct one. The TEXT line's handset is
    dropped by invariant 7 before the surviving handset is resolved, which
    is exactly the shift a stale or re-sorted position list gets wrong.

    This is the fixture the mutation below was run against: sorting the
    planned list before this second pass makes this test fail while the
    single-number test above keeps passing.
    """
    owner, sharer = two_spaces
    answers = _answers([
        _space(space_id=owner.id, phone_lines=(
            _voice_line(index=1, purpose="VOICE", handsets=0),
            _voice_line(index=2, purpose="TEXT", handsets=0),
        )),
        _space(space_id=sharer.id, phone_lines=(
            _voice_line(index=1, source=f"{owner.id}:2", purpose=None,
                       handsets=1),
            _voice_line(index=2, source=f"{owner.id}:1", purpose=None,
                       handsets=1),
        )),
    ])
    replace_lines(techops_draft, answers)
    db.session.flush()

    details = (db.session.query(TechOpsLineDetail)
               .join(TechOpsLineDetail.work_line)
               .order_by(TechOpsLineDetail.work_line_id).all())
    numbers = [d for d in details if d.service_type.code == "PHONE_NUMBER"]
    handsets = [d for d in details if d.service_type.code == "DESK_PHONE"]

    assert len(numbers) == 2
    assert len(handsets) == 1

    voice_number = next(d for d in numbers if d.purpose == "VOICE")
    text_number = next(d for d in numbers if d.purpose == "TEXT")
    assert handsets[0].parent_line_id == voice_number.work_line_id
    assert handsets[0].parent_line_id != text_number.work_line_id


def test_a_draft_resave_does_not_violate_the_parent_line_id_fk(
    app, techops_draft, two_spaces
):
    """conftest.py never turns SQLite foreign key enforcement on, so this
    class of defect passes silently in every other test in this suite and
    would only surface as a 500 in production, on Postgres. Enable
    enforcement for this test's own connection so the regression is
    visible here.

    On a re-save, the delete loop's lazy cascade load triggers autoflush
    mid-iteration, which can flush a PHONE_NUMBER line's delete to the
    database while a DESK_PHONE row's parent_line_id still points at it.
    """
    db.session.execute(text("PRAGMA foreign_keys=ON"))

    owner = two_spaces[0]
    answers = _answers([
        _space(space_id=owner.id, phone_lines=(_voice_line(handsets=2),)),
    ])
    replace_lines(techops_draft, answers)
    db.session.commit()

    # Re-saving identical content re-triggers the delete loop over the
    # PHONE_NUMBER line and its two now-existing DESK_PHONE handsets.
    replace_lines(techops_draft, answers)
    db.session.commit()

    details = db.session.query(TechOpsLineDetail).all()
    handsets = [d for d in details if d.service_type.code == "DESK_PHONE"]
    number = next(d for d in details if d.service_type.code == "PHONE_NUMBER")
    assert len(handsets) == 2
    assert all(h.parent_line_id == number.work_line_id for h in handsets)


def test_saving_a_draft_keeps_an_answered_but_empty_card(app, techops_draft,
                                                         two_spaces):
    """The gap techops_request_spaces exists to close."""
    owner, sharer = two_spaces
    answers = _answers([
        _space(space_id=owner.id, answer="NEEDS", wifi_requested=True),
        _space(space_id=sharer.id, answer="NEEDS"),
    ])
    replace_spaces(techops_draft, answers)
    replace_lines(techops_draft, answers)
    db.session.commit()

    rows = (db.session.query(TechOpsRequestSpace)
            .filter_by(work_item_id=techops_draft.id).all())
    assert {r.space_id for r in rows} == {owner.id, sharer.id}
    # The second space produced no lines at all, and is still recorded.
    assert len(techops_draft.lines) == 1


def test_replacing_spaces_removes_a_card_that_was_taken_off(app, techops_draft,
                                                            two_spaces):
    owner, sharer = two_spaces
    replace_spaces(techops_draft, _answers([_space(space_id=owner.id),
                                            _space(space_id=sharer.id)]))
    db.session.flush()
    replace_spaces(techops_draft, _answers([_space(space_id=owner.id)]))
    db.session.flush()
    rows = (db.session.query(TechOpsRequestSpace)
            .filter_by(work_item_id=techops_draft.id).all())
    assert [r.space_id for r in rows] == [owner.id]


def test_replace_spaces_persists_an_unanswered_card_with_null_answer(
        app, techops_draft, two_spaces):
    """Fix round 1: an opened-but-unanswered card (answer="") IS a row now,
    with answer stored as NULL, not skipped. `answer` became nullable so a
    space picked through the Task 10 picker survives a save even before
    the requester chooses "Needs services" or "Nothing needed" — a picked
    space has no other record of being on the request, unlike an assigned
    space, which is rebuilt from the department's assignment list either
    way.
    """
    owner, sharer = two_spaces
    replace_spaces(techops_draft, _answers([
        _space(space_id=owner.id, answer="NEEDS"),
        _space(space_id=sharer.id, answer=""),
    ]))
    db.session.commit()

    rows = {r.space_id: r for r in db.session.query(TechOpsRequestSpace).all()}
    assert set(rows) == {owner.id, sharer.id}
    assert rows[owner.id].answer == "NEEDS"
    assert rows[sharer.id].answer is None


def test_a_declined_wifi_reason_survives_with_no_wifi_line(app, techops_draft,
                                                           two_spaces):
    owner, _ = two_spaces
    answers = _answers([_space(
        space_id=owner.id, wifi_requested=False,
        wifi_declined_reason="Wired tech table only",
        ethernet_drops=(EthernetDrop(location="Back", usage="Switch"),))])
    replace_spaces(techops_draft, answers)
    replace_lines(techops_draft, answers)
    db.session.commit()

    row = db.session.query(TechOpsRequestSpace).one()
    assert row.wifi_declined_reason == "Wired tech table only"
    codes = {line.techops_detail.service_type.code
             for line in techops_draft.lines}
    assert codes == {"ETHERNET"}


def test_a_no_services_needed_draft_persists_its_affirmation_line(app, techops_draft):
    """expand_to_lines() emits the affirmation line now (line_grain.py), so
    replace_lines() writes it on an ordinary save. There is no longer a
    separate synthesize-at-submit step; a second implementation of this
    decision is what item 3 of round-1 review found and removed."""
    answers = _answers([], no_services_needed=True)
    replace_lines(techops_draft, answers)
    db.session.commit()

    line = db.session.query(TechOpsLineDetail).one()
    assert line.service_type.code == "NO_SERVICES"
    assert line.space_id is None


def test_saving_an_unchanged_draft_twice_emits_one_audit_event(
    app, techops_draft, two_spaces
):
    """capture_state_snapshot and capture_form_snapshot must agree on
    shape, or a save that changes nothing still reports a change because
    the two dicts can never compare equal.

    The owner's phone line must own its number (source=NEW), not merely
    exist as a dangling share target: only then does the sharer's
    handset get a real, non-NULL parent_line_id, which is what exercises
    the id-to-position translation this test exists to protect. A
    fixture where every parent_line_id stays NULL cannot catch a
    regression that compares raw ids instead of positions — replacing
    the translation with the raw id left this test green until this
    fixture was strengthened.
    """
    owner, sharer = two_spaces
    answers = _answers([
        _space(space_id=owner.id, wifi_requested=True,
              wifi_description="Badge scanners",
              phone_lines=(_voice_line(index=1, handsets=0,
                                       usage="Front desk"),)),
        _space(space_id=sharer.id, phone_lines=(_voice_line(
            index=1, source=f"{owner.id}:1", handsets=1),)),
    ])

    def _save():
        before = capture_state_snapshot(techops_draft)
        upsert_request_detail(techops_draft, answers, SimpleNamespace(user_id=techops_draft.created_by_user_id))
        replace_spaces(techops_draft, answers)
        replace_lines(techops_draft, answers)
        after = capture_form_snapshot(answers)
        audit_draft_edit(techops_draft, before, after,
                         SimpleNamespace(user_id=techops_draft.created_by_user_id))
        db.session.commit()

    _save()
    _save()

    events = (db.session.query(WorkItemAuditEvent)
             .filter_by(work_item_id=techops_draft.id).all())
    assert len(events) == 1


def test_saving_a_changed_draft_twice_emits_two_audit_events(
    app, techops_draft, two_spaces
):
    """The mirror of the test above: two genuinely different saves must
    still each be recorded, so the shared-shape fix above did not just
    make audit_draft_edit compare two dicts that are always equal.
    """
    owner, sharer = two_spaces

    def _save(wifi_description):
        answers = _answers([_space(space_id=owner.id, wifi_requested=True,
                                   wifi_description=wifi_description)])
        before = capture_state_snapshot(techops_draft)
        upsert_request_detail(techops_draft, answers, SimpleNamespace(user_id=techops_draft.created_by_user_id))
        replace_spaces(techops_draft, answers)
        replace_lines(techops_draft, answers)
        after = capture_form_snapshot(answers)
        audit_draft_edit(techops_draft, before, after,
                         SimpleNamespace(user_id=techops_draft.created_by_user_id))
        db.session.commit()

    _save("Badge scanners")
    _save("Press box laptops")

    events = (db.session.query(WorkItemAuditEvent)
             .filter_by(work_item_id=techops_draft.id).all())
    assert len(events) == 2
