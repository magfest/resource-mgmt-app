"""Invariants 1 through 11 from the spec's Line grain section.

One test per invariant. No Flask, no database: this module is plain
Python, so these run in milliseconds and a reviewer can check a code path
against a numbered list instead of a helper name.
"""
import pytest

from app.routes.work.techops.line_grain import (
    DepartmentWideAnswer, EthernetDrop, PhoneHandset, PhoneLine,
    RequestAnswers, SpaceAnswer, expand_to_lines,
)


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
    # Task 6 imports this builder rather than copying it, and needs to vary
    # action and no_services_needed. Both are parameters from the start.
    return RequestAnswers(
        primary_contact_name="Ada", primary_contact_email="ada@magfest.org",
        additional_notes="", no_services_needed=no_services_needed,
        action=action,
        spaces=tuple(spaces), department_wide=tuple(department_wide),
    )


def _voice_line(index=1, source="NEW", handsets=1, purpose="VOICE", **kw):
    # Task 6 imports this builder too; kept beside _space/_answers.
    return PhoneLine(
        index=index, source=source, purpose=purpose,
        internal_only=kw.pop("internal_only", False),
        usage=kw.pop("usage", "Front counter"),
        caller_id_name=kw.pop("caller_id_name", "REGDESK"),
        handsets=tuple(PhoneHandset(location=f"Position {n}")
                       for n in range(1, handsets + 1)),
        **kw,
    )


def test_invariant_1_an_answered_space_produces_no_line_of_its_own():
    lines = expand_to_lines(_answers([_space()]))
    assert lines == []


def test_invariant_2_wifi_is_one_line_per_space():
    lines = expand_to_lines(_answers([_space(wifi_requested=True,
                                             wifi_description="Badge scanners")]))
    assert [line.service_code for line in lines] == ["WIFI"]
    assert lines[0].description == "Badge scanners"


def test_invariant_2_an_unanswered_wifi_question_produces_no_line_even_when_forced():
    """Item 4 of the owner's post-review fixes: a forced space with no
    explicit WiFi answer must not silently get a WiFi line. Before that
    fix, `_space()`'s default (`wifi_requested=False`, the only
    "unanswered" a plain bool could represent) triggered exactly this
    auto-add; the tri-state default is now `None`, and this locks down
    that it produces nothing, matching an explicit decline."""
    space = _space(ethernet_drops=(EthernetDrop(location="Back wall",
                                                usage="Tech table"),))
    lines = expand_to_lines(_answers([space]))
    assert [line.service_code for line in lines] == ["ETHERNET"]


def test_invariant_2_an_explicit_wifi_answer_creates_a_line_on_a_forced_space():
    """auto_added (round-3 fix item 3) used to flag this line for a
    reviewer's benefit; it is gone now that no WiFi line is ever created
    without an explicit answer. What survives is the underlying rule: a
    forced space (wired gear present) with an explicit "needed" answer
    still gets its WIFI line alongside the gear's own lines.
    """
    space = _space(wifi_requested=True, wifi_description="Badge scanners",
                   ethernet_drops=(EthernetDrop(location="Back wall",
                                                usage="Tech table"),))
    lines = expand_to_lines(_answers([space]))
    assert [line.service_code for line in lines] == ["WIFI", "ETHERNET"]
    assert lines[0].description == "Badge scanners"


def test_invariant_2_a_declined_wifi_produces_no_line():
    space = _space(wifi_requested=False, wifi_declined_reason="Wired only",
                   ethernet_drops=(EthernetDrop(location="Back wall",
                                                usage="Tech table"),))
    lines = expand_to_lines(_answers([space]))
    assert [line.service_code for line in lines] == ["ETHERNET"]


def test_invariant_3_each_drop_is_its_own_line():
    space = _space(ethernet_drops=(
        EthernetDrop(location="Front", usage="Scanner"),
        EthernetDrop(location="Back", usage="Printer"),
    ))
    lines = expand_to_lines(_answers([space]))
    ethernet = [line for line in lines if line.service_code == "ETHERNET"]
    assert [line.location for line in ethernet] == ["Front", "Back"]


def test_invariant_9_nothing_needed_is_one_line_and_no_others():
    space = _space(answer="NOTHING",
                   no_services_reason="Crates only",
                   wifi_requested=True,
                   ethernet_drops=(EthernetDrop(location="x", usage="y"),))
    lines = expand_to_lines(_answers([space]))
    assert [line.service_code for line in lines] == ["NO_SERVICES"]
    assert lines[0].description == "Crates only"


def test_invariant_10_department_wide_lines_have_no_space():
    answers = _answers(department_wide=(
        DepartmentWideAnswer(service_code="RADIO_CHANNEL",
                             location="REG-1", usage="Floor leads",
                             description=""),
    ))
    lines = expand_to_lines(answers)
    assert lines[0].space_id is None
    assert lines[0].location == "REG-1"


def test_invariant_11_numbers_follow_expansion_order_not_space_order():
    answers = _answers([
        _space(space_id=7, wifi_requested=True),
        _space(space_id=3, wifi_requested=True),
    ])
    lines = expand_to_lines(answers)
    assert [line.space_id for line in lines] == [7, 3]


def test_invariant_4_each_new_number_is_one_phone_number_line():
    # wifi_requested is unset (declined, via _space()'s default): the
    # phone invariant is what this test is about, so WiFi contributes no
    # line either way and the codes list stays uncoupled from it.
    space = _space(phone_lines=(_voice_line(handsets=0),))
    codes = [line.service_code for line in expand_to_lines(_answers([space]))]
    assert codes == ["PHONE_NUMBER"]


def test_invariant_5_each_handset_is_a_line_parented_to_its_number():
    space = _space(phone_lines=(_voice_line(handsets=2),))
    lines = expand_to_lines(_answers([space]))
    assert [line.service_code for line in lines] == [
        "PHONE_NUMBER", "DESK_PHONE", "DESK_PHONE"]
    assert lines[1].parent_index == 0
    assert lines[2].parent_index == 0
    assert lines[1].location == "Position 1"
    # Pins the usage=None decision on handsets: a handset does not inherit
    # its parent line's usage ("Front counter" is _voice_line's default).
    assert lines[1].usage is None


def test_invariant_5_a_handset_may_ring_a_number_in_another_space():
    owner = _space(space_id=1, phone_lines=(_voice_line(handsets=0),))
    sharer = _space(space_id=2,
                    phone_lines=(_voice_line(index=1, source="1:1", handsets=1),))
    lines = expand_to_lines(_answers([owner, sharer]))
    assert [line.service_code for line in lines] == [
        "PHONE_NUMBER", "DESK_PHONE"]
    assert lines[1].parent_index == 0
    assert lines[1].space_id == 2


def test_invariant_6_a_sharing_line_creates_no_number():
    owner = _space(space_id=1, phone_lines=(_voice_line(handsets=0),))
    sharer = _space(space_id=2,
                    phone_lines=(_voice_line(index=1, source="1:1", handsets=1),))
    lines = expand_to_lines(_answers([owner, sharer]))
    numbers = [line for line in lines if line.service_code == "PHONE_NUMBER"]
    assert len(numbers) == 1
    assert numbers[0].space_id == 1


def test_invariant_7_a_text_only_line_produces_no_handsets():
    space = _space(phone_lines=(_voice_line(purpose="TEXT", handsets=3),))
    codes = [line.service_code for line in expand_to_lines(_answers([space]))]
    assert codes == ["PHONE_NUMBER"]


def test_invariant_7_a_line_sharing_a_text_only_number_gets_no_handsets():
    """The sharing line inherits the source's purpose, so the rule follows."""
    owner = _space(space_id=1,
                   phone_lines=(_voice_line(purpose="TEXT", handsets=0),))
    sharer = _space(space_id=2,
                    phone_lines=(_voice_line(index=1, source="1:1",
                                             purpose=None, handsets=2),))
    lines = expand_to_lines(_answers([owner, sharer]))
    assert [line.service_code for line in lines] == ["PHONE_NUMBER"]


def test_invariant_8_internal_only_forces_dial_in_and_dial_out_false():
    space = _space(phone_lines=(_voice_line(internal_only=True, handsets=0),))
    number = expand_to_lines(_answers([space]))[0]
    assert number.internal_only is True
    assert number.config["dial_in"] is False
    assert number.config["dial_out"] is False


def test_phone_config_carries_the_flat_pass_one_fields():
    space = _space(phone_lines=(_voice_line(
        handsets=0, caller_id_name="REGDESK",
        voicemail_slack_channel="#reg-voicemail",
        text_slack_channel="#reg-hotliner",
        forward_target="Ada, 301-555-0100"),))
    number = expand_to_lines(_answers([space]))[0]
    assert number.config["caller_id_name"] == "REGDESK"
    assert number.config["voicemail_slack_channel"] == "#reg-voicemail"
    assert number.config["text_slack_channel"] == "#reg-hotliner"
    assert number.config["forward_target"] == "Ada, 301-555-0100"


def test_parent_index_resolves_against_the_post_drop_list_not_the_pre_drop_one():
    """Line 1 is TEXT: its two handsets are dropped by invariant 7. Line 2
    is VOICE: its two handsets survive and must point at line 2's number,
    which only lands at position 2 after line 1's handsets are gone.
    Reading parent_index off the pre-drop count instead would point a
    surviving handset at a DESK_PHONE line, not its PHONE_NUMBER.
    """
    space = _space(phone_lines=(
        _voice_line(index=1, purpose="TEXT", handsets=2),
        _voice_line(index=2, purpose="VOICE", handsets=2),
    ))
    lines = expand_to_lines(_answers([space]))
    assert [line.service_code for line in lines] == [
        "PHONE_NUMBER", "PHONE_NUMBER", "DESK_PHONE", "DESK_PHONE"]
    for handset in lines[2:]:
        assert handset.parent_index == 1
        parent = lines[handset.parent_index]
        assert parent.service_code == "PHONE_NUMBER"
        assert parent.purpose == "VOICE"


@pytest.mark.parametrize("source", ["9:9", "", "banana", "1:"])
def test_expansion_does_not_raise_on_an_unresolvable_phone_source(source):
    """The preview endpoint runs expand_to_lines() on every keystroke of a
    half-finished draft, where a dangling or malformed source is the
    normal state, not an edge case. validate() rejects it later; here it
    must resolve to parent_index=None and never raise.
    """
    space = _space(phone_lines=(
        _voice_line(index=1, source=source, handsets=1),
    ))
    lines = expand_to_lines(_answers([space]))
    desk_phones = [line for line in lines if line.service_code == "DESK_PHONE"]
    assert len(desk_phones) == 1
    assert desk_phones[0].parent_index is None


def test_a_no_services_needed_department_gets_one_affirmation_line():
    """Round-1 review, item 3: this decision used to live only in
    form_utils.synthesize_no_services_line, called at submit time, so the
    order preview (which only calls expand_to_lines) never showed it. Now
    expand_to_lines is the only place that decides this line exists."""
    lines = expand_to_lines(_answers([], no_services_needed=True))
    assert [line.service_code for line in lines] == ["NO_SERVICES"]
    assert lines[0].space_id is None


def test_expansion_does_not_raise_on_a_multi_hop_share_chain():
    """Sharing resolves one hop only: a source must name a line that owns
    its number directly. Space C's source names space B's line, which
    itself shares space A's number rather than owning one, so C's handset
    cannot be resolved. That must not raise.
    """
    a = _space(space_id=1, phone_lines=(_voice_line(index=1, handsets=0),))
    b = _space(space_id=2, phone_lines=(
        _voice_line(index=1, source="1:1", handsets=0),))
    c = _space(space_id=3, phone_lines=(
        _voice_line(index=1, source="2:1", handsets=1),))
    lines = expand_to_lines(_answers([a, b, c]))
    desk_phones = [line for line in lines if line.service_code == "DESK_PHONE"]
    assert len(desk_phones) == 1
    assert desk_phones[0].parent_index is None
