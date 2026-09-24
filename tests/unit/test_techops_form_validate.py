"""Semantic validation of a room-first request.

Every rule here is also enforced in the browser. The browser's copy is a
convenience; this one is the rule.
"""
from dataclasses import replace

from werkzeug.datastructures import MultiDict

from app.routes.work.techops.form_utils import (
    ACTION_SAVE_DRAFT, ACTION_SUBMIT, parse_form, validate,
)
from app.routes.work.techops.line_grain import (
    EthernetDrop, PhoneHandset, PhoneLine, RequestAnswers, SpaceAnswer,
    expand_to_lines,
)
from tests.unit.test_techops_line_grain import _answers, _space


def test_submitting_with_an_unanswered_space_is_rejected_by_name():
    space = _space(space_id=5, display_name="Expo Hall E", answer="")
    errors = validate(_answers([space], action=ACTION_SUBMIT), has_space_cards=True)
    assert any("Expo Hall E" in e for e in errors)


def test_an_unanswered_wifi_question_on_a_forced_space_is_rejected_like_a_bare_decline():
    """Item 4: an untouched WiFi radio pair on a space with wired gear
    must be refused the same way an explicit decline with no reason is,
    since both leave TechOps without either a line or a stated reason."""
    space = _space(display_name="Regdesk A", wifi_requested=None,
                   ethernet_drops=(EthernetDrop(location="Back", usage="AP"),))
    assert space.wifi_requested is None
    errors = validate(_answers([space], action=ACTION_SUBMIT), has_space_cards=True)
    assert any("Regdesk A" in e and "WiFi" in e for e in errors)


def test_an_unanswered_wifi_question_on_a_space_with_no_gear_is_rejected_by_name():
    """Item 4's core rule: every "Needs services" card must answer WiFi,
    not only a forced one. Wording differs from the forced case (no
    "wired gear" framing makes sense when there is none) but the space is
    still named, matching the unanswered-space-card error's pattern."""
    space = _space(display_name="Quiet Storage Room", wifi_requested=None)
    assert space.wifi_requested is None
    errors = validate(_answers([space], action=ACTION_SUBMIT), has_space_cards=True)
    assert any(e == "Quiet Storage Room: say whether this space needs WiFi."
              for e in errors)


def test_an_explicit_wifi_answer_with_no_gear_is_not_rejected():
    space = _space(wifi_requested=True)
    errors = validate(_answers([space], action=ACTION_SUBMIT), has_space_cards=True)
    assert not any("WiFi" in e for e in errors)


def test_declining_wifi_without_a_reason_is_rejected():
    space = _space(display_name="Regdesk A", wifi_requested=False,
                   wifi_declined_reason="",
                   ethernet_drops=(EthernetDrop(location="Back", usage="AP"),))
    errors = validate(_answers([space], action=ACTION_SUBMIT), has_space_cards=True)
    assert any("Regdesk A" in e and "WiFi" in e for e in errors)


def test_declining_wifi_is_fine_when_a_reason_is_given():
    space = _space(wifi_requested=False, wifi_declined_reason="Wired only",
                   ethernet_drops=(EthernetDrop(location="Back", usage="AP"),))
    assert validate(_answers([space], action=ACTION_SUBMIT),
                    has_space_cards=True) == []


def test_a_phone_line_with_no_purpose_is_rejected():
    space = _space(phone_lines=(PhoneLine(index=1, source="NEW", purpose=None,
                                          internal_only=False, usage="Front"),))
    errors = validate(_answers([space], action=ACTION_SUBMIT), has_space_cards=True)
    assert any("purpose" in e.lower() for e in errors)


def test_sharing_a_line_that_does_not_exist_is_rejected():
    space = _space(space_id=2, display_name="Regdesk B",
                   phone_lines=(PhoneLine(index=1, source="9:1", purpose=None,
                                          internal_only=False,
                                          handsets=(PhoneHandset("Counter"),)),))
    errors = validate(_answers([space], action=ACTION_SUBMIT), has_space_cards=True)
    assert any("Regdesk B" in e and "does not exist" in e for e in errors)


def test_an_orphaned_handsets_blank_source_is_rejected_not_silently_new():
    """End to end through parse_form, not the dataclass shortcut: a source
    field posted empty (as _space_card.html now renders an orphaned
    handset's preserved blank, rather than defaulting it to NEW) must
    still reach validate() blank, and validate() must still refuse it."""
    form = MultiDict([
        ("primary_contact_name", "Ada"),
        ("primary_contact_email", "ada@magfest.org"),
        ("action", ACTION_SUBMIT),
        ("space_ids", "412"),
        ("space_412_answer", "NEEDS"),
        ("space_412_PHONE_line_1_purpose", "VOICE"),
        ("space_412_PHONE_line_1_usage", "Front counter"),
        ("space_412_PHONE_line_1_source", ""),
    ])
    answers, parse_errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].phone_lines[0].source == ""
    errors = parse_errors + validate(answers, has_space_cards=True)
    assert any("does not exist" in e for e in errors)


def test_sharing_a_line_that_itself_shares_is_rejected():
    """A chain has no owner at the end of it, so nothing provisions."""
    owner = _space(space_id=1, phone_lines=(
        PhoneLine(index=1, source="3:1", purpose=None, internal_only=False),))
    sharer = _space(space_id=2, display_name="Regdesk B", phone_lines=(
        PhoneLine(index=1, source="1:1", purpose=None, internal_only=False),))
    errors = validate(_answers([owner, sharer], action=ACTION_SUBMIT),
                      has_space_cards=True)
    # The sharer's own error, not the owner's separate "does not exist"
    # error for its own dangling "3:1" reference: the sharer's target
    # ("1:1") does exist but is itself a share, so nothing provisions.
    assert any("Regdesk B" in e and "itself" in e for e in errors)


def test_no_services_needed_is_only_offered_with_zero_cards():
    answers = _answers([_space()], action=ACTION_SUBMIT)
    answers = replace(answers, no_services_needed=True)
    errors = validate(answers, has_space_cards=True)
    assert any("spaces" in e for e in errors)


def test_a_draft_may_be_saved_half_finished():
    """Invariant: only SUBMIT demands completeness. A draft saves as-is."""
    space = _space(answer="")
    assert validate(_answers([space], action=ACTION_SAVE_DRAFT),
                    has_space_cards=True) == []


def test_a_needs_space_half_filled_saves_as_a_draft_without_error():
    """A card marked NEEDS with an incomplete drop and an unpurposed line
    is exactly the mid-thought state the governing principle protects;
    only SUBMIT may reject it, never SAVE_DRAFT."""
    space = _space(
        answer="NEEDS",
        ethernet_drops=(EthernetDrop(location="Back", usage=""),),
        phone_lines=(PhoneLine(index=1, source="NEW", purpose=None,
                                internal_only=False),),
    )
    assert validate(_answers([space], action=ACTION_SAVE_DRAFT),
                    has_space_cards=True) == []


def test_a_handset_with_no_location_is_rejected_on_submit():
    space = _space(display_name="Regdesk C", phone_lines=(
        PhoneLine(index=1, source="NEW", purpose="VOICE",
                  internal_only=False,
                  handsets=(PhoneHandset(location=""),)),))
    errors = validate(_answers([space], action=ACTION_SUBMIT), has_space_cards=True)
    assert any("Regdesk C" in e and "handset" in e.lower() for e in errors)


def test_missing_contact_email_is_rejected_even_on_a_draft():
    answers = replace(_answers([], action=ACTION_SAVE_DRAFT),
                      primary_contact_email="")
    errors = validate(answers, has_space_cards=False)
    assert any("email" in e.lower() for e in errors)


def test_the_shipped_buttons_action_value_reaches_validate_as_a_rejection():
    """Crosses the parse_form -> validate boundary with the literal field
    the template posts (work_item_form.html:367,370), not a hand-built
    RequestAnswers. A prior version of this module invented a second,
    differently-cased action vocabulary that parse_form never produced,
    which made every completeness rule below unreachable from a real
    request; this is the test that would have caught it.
    """
    form = MultiDict([
        ("primary_contact_name", "Ada"),
        ("primary_contact_email", "ada@magfest.org"),
        ("action", "submit"),
    ])
    form.add("space_ids", "412")
    # space_412_answer left unset: an opened, unanswered card.
    answers, parse_errors = parse_form(
        form, offerable_spaces={412: "Expo Hall E"})
    assert parse_errors == []
    errors = validate(answers, has_space_cards=True)
    assert any("Expo Hall E" in e and "say whether this space needs services" in e
              for e in errors)


def test_the_catalog_name_reaches_the_error_message_across_the_boundary():
    """A prior version left SpaceAnswer.display_name blank at parse time
    and expected a later layer to fill it in; nothing did, and every
    per-space message from a real request opened with a bare colon. This
    goes through parse_form, not a hand-built RequestAnswers, because that
    is the only path the bug lived on."""
    form = MultiDict([
        ("primary_contact_name", "Ada"),
        ("primary_contact_email", "ada@magfest.org"),
        ("action", "submit"),
    ])
    form.add("space_ids", "7")
    # space_7_answer left unset: an opened, unanswered card.
    answers, parse_errors = parse_form(
        form, offerable_spaces={7: "Regdesk A"})
    assert parse_errors == []
    errors = validate(answers, has_space_cards=True)
    assert any(e.startswith("Regdesk A:") for e in errors)


def test_missing_contact_name_is_rejected_even_on_a_draft():
    """The email rule had a test and the name rule did not. Deleting the
    name check passed the whole techops suite.
    """
    answers = replace(_answers([], action=ACTION_SAVE_DRAFT),
                      primary_contact_name="")
    errors = validate(answers, has_space_cards=False)
    assert any("name" in e.lower() for e in errors)


def test_submitting_a_request_that_would_create_no_lines_is_rejected():
    """A space answered NEEDS with nothing filled in produces no work at
    all. Without this rule a department submits an empty request and
    TechOps has nothing to review and no affirmation that nothing is
    wanted. Deleting the rule passed all 81 techops tests.
    """
    answers = _answers([_space(display_name="Expo Hall E", answer="NEEDS")],
                       action=ACTION_SUBMIT)
    errors = validate(answers, has_space_cards=True)
    assert any("no lines" in e for e in errors)


def test_the_no_lines_rule_does_not_fire_when_the_department_needs_nothing():
    """The affirmation is the whole point of the carve-out: a department
    with no spaces says so once, and that request legitimately produces
    one line rather than none. Checked directly against expand_to_lines
    rather than trusting the rule's absence to mean the line exists."""
    answers = _answers([], action=ACTION_SUBMIT, no_services_needed=True)
    errors = validate(answers, has_space_cards=False)
    assert not any("no lines" in e for e in errors)
    assert [line.service_code for line in expand_to_lines(answers)] == ["NO_SERVICES"]
