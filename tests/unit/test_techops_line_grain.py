"""Invariants 1 through 11 from the spec's Line grain section.

One test per invariant. No Flask, no database: this module is plain
Python, so these run in milliseconds and a reviewer can check a code path
against a numbered list instead of a helper name.
"""
from dataclasses import replace

import pytest

from app.routes.work.techops.line_grain import (
    NETWORK_NO, NETWORK_UNSURE, NETWORK_YES,
    QUESTION_NETWORK_KINDS, QUESTION_NETWORK_UNSURE,
    NO_ANSWER_FORWARD, NO_ANSWER_RING, NO_ANSWER_VOICEMAIL,
    QUESTION_CAPABILITIES, QUESTION_VOICE_DELIVERY, QUESTION_HANDSETS,
    QUESTION_LOCATION, QUESTION_VOICEMAIL_SLACK, phone_open_questions,
    VOICE_DELIVERY_DESK_PHONE, VOICE_DELIVERY_FORWARD,
    VOICE_DELIVERY_NONE, VOICE_DELIVERY_VOICEMAIL, DepartmentWideAnswer,
    PhoneHandset, PhoneLine, RequestAnswers, SpaceAnswer,
    derive_internal_only, derive_purpose, expand_to_lines,
)


def _space(space_id=1, **kwargs):
    defaults = dict(
        space_id=space_id, display_name=f"Space {space_id}", answer="NEEDS",
        no_services_reason="", wifi_requested=False,
        wifi_declined_reason="", wifi_description="",
        network_needed="", network_kinds=(), network_count="",
        network_traffic="", network_notes="",
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


def _voice_line(index=1, source="NEW", handsets=1, **kw):
    # A number that reaches outside both ways and rings a desk phone. The
    # validate and persistence modules import this builder too.
    return PhoneLine(
        index=index, source=source,
        dial_in=kw.pop("dial_in", True),
        dial_out=kw.pop("dial_out", True),
        texts=kw.pop("texts", False),
        voice_delivery=kw.pop("voice_delivery", VOICE_DELIVERY_DESK_PHONE),
        no_answer=kw.pop("no_answer", NO_ANSWER_RING),
        usage=kw.pop("usage", "Front counter"),
        handsets=tuple(PhoneHandset(location=f"Position {n}")
                       for n in range(1, handsets + 1)),
        **kw,
    )


def _number_only_line(index=1, source="NEW", **kw):
    """A number with no physical phone: voicemail delivery, so the desk
    phone branch never applies and no placeholder handset is implied."""
    return _voice_line(index=index, source=source, handsets=0,
                       voice_delivery=VOICE_DELIVERY_VOICEMAIL,
                       voicemail_slack_channel="#vm", **kw)


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
    space = _space(network_needed=NETWORK_YES, network_kinds=("COMPUTERS",))
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
                   network_needed=NETWORK_YES, network_kinds=("COMPUTERS",))
    lines = expand_to_lines(_answers([space]))
    assert [line.service_code for line in lines] == ["WIFI", "ETHERNET"]
    assert lines[0].description == "Badge scanners"


def test_invariant_2_a_declined_wifi_produces_no_line():
    space = _space(wifi_requested=False, wifi_declined_reason="Wired only",
                   network_needed=NETWORK_YES, network_kinds=("COMPUTERS",))
    lines = expand_to_lines(_answers([space]))
    assert [line.service_code for line in lines] == ["ETHERNET"]


def test_invariant_3_a_room_that_needs_wiring_is_one_line():
    """Was one line per drop. The requester no longer counts drops, because
    counting them is a network plan they have no way to make; the network
    team decides that from what the room says it has."""
    space = _space(network_needed=NETWORK_YES,
                   network_kinds=("COMPUTERS", "STREAMING"),
                   network_count="6-10", network_traffic="INTERNET")
    ethernet = [l for l in expand_to_lines(_answers([space]))
                if l.service_code == "ETHERNET"]
    assert len(ethernet) == 1
    assert ethernet[0].config["kinds"] == ["COMPUTERS", "STREAMING"]


def test_invariant_9_nothing_needed_is_one_line_and_no_others():
    space = _space(answer="NOTHING",
                   no_services_reason="Crates only",
                   wifi_requested=True,
                   network_needed=NETWORK_YES, network_kinds=("COMPUTERS",))
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
    space = _space(phone_lines=(_number_only_line(),))
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
    owner = _space(space_id=1, phone_lines=(_number_only_line(),))
    sharer = _space(space_id=2,
                    phone_lines=(_voice_line(index=1, source="1:1", handsets=1),))
    lines = expand_to_lines(_answers([owner, sharer]))
    assert [line.service_code for line in lines] == [
        "PHONE_NUMBER", "DESK_PHONE"]
    assert lines[1].parent_index == 0
    assert lines[1].space_id == 2


def test_invariant_6_a_sharing_line_creates_no_number():
    owner = _space(space_id=1, phone_lines=(_number_only_line(),))
    sharer = _space(space_id=2,
                    phone_lines=(_voice_line(index=1, source="1:1", handsets=1),))
    lines = expand_to_lines(_answers([owner, sharer]))
    numbers = [line for line in lines if line.service_code == "PHONE_NUMBER"]
    assert len(numbers) == 1
    assert numbers[0].space_id == 1


def test_invariant_7_a_text_only_line_produces_no_handsets():
    space = _space(phone_lines=(_voice_line(texts=True, voice_delivery=VOICE_DELIVERY_NONE, handsets=3),))
    codes = [line.service_code for line in expand_to_lines(_answers([space]))]
    assert codes == ["PHONE_NUMBER"]


def test_invariant_7_a_line_sharing_a_text_only_number_gets_no_handsets():
    """The sharing line inherits the source's purpose, so the rule follows."""
    owner = _space(space_id=1,
                   phone_lines=(_voice_line(texts=True, voice_delivery=VOICE_DELIVERY_NONE, handsets=0),))
    sharer = _space(space_id=2,
                    phone_lines=(_voice_line(index=1, source="1:1", handsets=2),))
    lines = expand_to_lines(_answers([owner, sharer]))
    assert [line.service_code for line in lines] == ["PHONE_NUMBER"]


def test_invariant_8_a_number_reaching_nobody_outside_is_internal_only():
    space = _space(phone_lines=(
        _voice_line(dial_in=False, dial_out=False, handsets=0),))
    number = expand_to_lines(_answers([space]))[0]
    assert number.internal_only is True
    assert number.config["dial_in"] is False
    assert number.config["dial_out"] is False


def test_phone_config_carries_every_channel_the_answers_ask_for():
    """Each channel is stored only when the answers put its question on
    screen. Forwarding is the delivery, voicemail is what happens when
    nobody picks up, and texts are ticked, so all three are asked."""
    space = _space(phone_lines=(_voice_line(
        handsets=0,
        texts=True,
        voice_delivery=VOICE_DELIVERY_FORWARD,
        no_answer=NO_ANSWER_VOICEMAIL,
        voicemail_slack_channel="#reg-voicemail",
        text_slack_channel="#reg-hotliner",
        forward_target="Ada, 301-555-0100"),))
    number = expand_to_lines(_answers([space]))[0]
    assert number.config["voicemail_slack_channel"] == "#reg-voicemail"
    assert number.config["text_slack_channel"] == "#reg-hotliner"
    assert number.config["forward_target"] == "Ada, 301-555-0100"


def test_a_channel_for_a_question_that_is_not_shown_is_not_stored():
    """A desk phone that keeps ringing asks for no voicemail box, so a
    channel typed before the requester changed their mind is not an
    answer to anything."""
    space = _space(phone_lines=(_voice_line(
        handsets=1, voicemail_slack_channel="#stale",
        forward_target="Ada, 301-555-0100"),))
    number = expand_to_lines(_answers([space]))[0]
    assert number.config["voicemail_slack_channel"] == ""
    assert number.config["forward_target"] == ""


def test_parent_index_resolves_against_the_post_drop_list_not_the_pre_drop_one():
    """Line 1 is TEXT: its two handsets are dropped by invariant 7. Line 2
    is VOICE: its two handsets survive and must point at line 2's number,
    which only lands at position 2 after line 1's handsets are gone.
    Reading parent_index off the pre-drop count instead would point a
    surviving handset at a DESK_PHONE line, not its PHONE_NUMBER.
    """
    space = _space(phone_lines=(
        _voice_line(index=1, texts=True,
                    voice_delivery=VOICE_DELIVERY_NONE, handsets=2),
        _voice_line(index=2, handsets=2),
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
    a = _space(space_id=1, phone_lines=(_number_only_line(index=1),))
    b = _space(space_id=2, phone_lines=(
        _voice_line(index=1, source="1:1", handsets=0),))
    c = _space(space_id=3, phone_lines=(
        _voice_line(index=1, source="2:1", handsets=1),))
    lines = expand_to_lines(_answers([a, b, c]))
    desk_phones = [line for line in lines if line.service_code == "DESK_PHONE"]
    # B shares A's number and lists no handset, so it gets a placeholder that
    # resolves to A. C's handset is the unresolvable one.
    assert len(desk_phones) == 2
    assert desk_phones[0].space_id == 2
    assert desk_phones[0].parent_index == 0
    assert desk_phones[1].space_id == 3
    assert desk_phones[1].parent_index is None


@pytest.mark.parametrize("delivery,texts,expected", [
    (VOICE_DELIVERY_DESK_PHONE, False, "VOICE"),
    (VOICE_DELIVERY_VOICEMAIL, False, "VOICE"),
    (VOICE_DELIVERY_DESK_PHONE, True, "BOTH"),
    (VOICE_DELIVERY_NONE, True, "TEXT"),
    ("", True, "TEXT"),
    (VOICE_DELIVERY_NONE, False, None),
    ("", False, None),
])
def test_purpose_is_derived_from_delivery_and_texts(delivery, texts, expected):
    line = PhoneLine(index=1, source="NEW", voice_delivery=delivery, texts=texts)
    assert derive_purpose(line) == expected


def test_a_voice_line_reaching_nobody_outside_is_internal_only():
    """Both call boxes unticked with a delivery chosen is a room phone that
    reaches other rooms and nothing outside. It is not the same as having no
    voice at all, which is why the no-voice choice is explicit."""
    line = PhoneLine(index=1, source="NEW",
                     voice_delivery=VOICE_DELIVERY_DESK_PHONE)
    assert derive_internal_only(line) is True

    reachable = PhoneLine(index=1, source="NEW", dial_in=True,
                          voice_delivery=VOICE_DELIVERY_DESK_PHONE)
    assert derive_internal_only(reachable) is False

    texts_only = PhoneLine(index=1, source="NEW", texts=True,
                           voice_delivery=VOICE_DELIVERY_NONE)
    assert derive_internal_only(texts_only) is False


def test_a_text_only_number_cannot_carry_outside_calling():
    """dial_in and dial_out are written here, never read from the form, so a
    crafted POST cannot set outbound calling on a number with no voice."""
    line = PhoneLine(index=1, source="NEW", dial_in=True, dial_out=True,
                     texts=True, voice_delivery=VOICE_DELIVERY_NONE)
    number = expand_to_lines(_answers([_space(phone_lines=(line,))]))[0]
    assert number.config["dial_in"] is False
    assert number.config["dial_out"] is False


def test_a_blank_voicemail_channel_is_a_question_not_a_refusal():
    line = PhoneLine(index=1, source="NEW", dial_in=True,
                     voice_delivery=VOICE_DELIVERY_VOICEMAIL,
                     voicemail_slack_channel="")
    assert phone_open_questions(line) == [QUESTION_VOICEMAIL_SLACK]

    answered = replace(line, voicemail_slack_channel="#regdesk-vm")
    assert phone_open_questions(answered) == []


def test_a_shared_line_with_no_handset_still_produces_one():
    """A shared line's only meaning is a handset. Producing nothing lost the
    space from the request with no trace."""
    owner = _space(space_id=1, phone_lines=(_voice_line(index=1),))
    sharer = _space(space_id=2, phone_lines=(
        PhoneLine(index=1, source="1:1", handsets=()),))
    lines = expand_to_lines(_answers([owner, sharer]))
    placeholders = [l for l in lines
                    if l.service_code == "DESK_PHONE" and l.space_id == 2]
    assert len(placeholders) == 1
    assert placeholders[0].location is None
    assert placeholders[0].config["open_questions"] == [QUESTION_LOCATION]


def test_a_placeholder_does_not_misalign_the_number_it_rings():
    """parent_index is assigned against positions surviving the invariant-7
    drop, and a placeholder is a new entry in that same pass."""
    owner = _space(space_id=1, phone_lines=(_voice_line(index=1, handsets=2),))
    sharer = _space(space_id=2, phone_lines=(
        PhoneLine(index=1, source="1:1", handsets=()),))
    lines = expand_to_lines(_answers([owner, sharer]))
    placeholder = [l for l in lines
                   if l.service_code == "DESK_PHONE" and l.space_id == 2][0]
    parent = lines[placeholder.parent_index]
    assert parent.service_code == "PHONE_NUMBER"
    assert parent.space_id == 1


def test_a_space_switched_to_nothing_needed_leaks_no_questions():
    """Invariant 9 discards the rest of the card. Questions computed from
    discarded phone answers would reach a reviewer for a space that needs
    nothing."""
    space = _space(answer="NOTHING", phone_lines=(
        PhoneLine(index=1, source="NEW", texts=True, text_slack_channel=""),))
    lines = expand_to_lines(_answers([space]))
    assert [l.service_code for l in lines] == ["NO_SERVICES"]
    assert lines[0].config is None


def test_a_desk_phone_with_no_handsets_gets_a_placeholder():
    """Delivery says desk phone and no location was given: the handset is
    still created so the phone team has a line to place."""
    space = _space(phone_lines=(_voice_line(index=1, handsets=0),))
    lines = expand_to_lines(_answers([space]))
    assert [l.service_code for l in lines] == ["PHONE_NUMBER", "DESK_PHONE"]
    assert lines[0].config["open_questions"] == [QUESTION_HANDSETS]
    assert lines[1].config["open_questions"] == [QUESTION_LOCATION]


def test_a_number_with_no_voice_stores_no_no_answer():
    """The no-answer question is hidden once delivery is NONE, so a value
    left behind from an earlier choice describes a question the requester
    was no longer being asked."""
    line = PhoneLine(index=1, source="NEW", texts=True,
                     text_slack_channel="#texts",
                     voice_delivery=VOICE_DELIVERY_NONE,
                     no_answer=NO_ANSWER_VOICEMAIL,
                     voicemail_slack_channel="#stale")
    number = expand_to_lines(_answers([_space(phone_lines=(line,))]))[0]
    assert number.config["no_answer"] is None
    assert number.config["voicemail_slack_channel"] == ""


def test_a_voicemail_number_keeps_no_no_answer_or_forward_target():
    """Direct-to-voicemail hides the no-answer question, so a value left
    from an earlier Desk phone choice describes a form the requester is no
    longer looking at. The reviewer would otherwise read "forward to Ada"
    on a number that goes straight to voicemail."""
    line = PhoneLine(index=1, source="NEW", dial_in=True,
                     voice_delivery=VOICE_DELIVERY_VOICEMAIL,
                     voicemail_slack_channel="#vm",
                     no_answer=NO_ANSWER_FORWARD,
                     forward_target="Ada 301-555-0100")
    cfg = expand_to_lines(_answers([_space(phone_lines=(line,))]))[0].config
    assert cfg["no_answer"] is None
    assert cfg["forward_target"] == ""


def test_a_ticked_call_box_survives_an_unanswered_delivery():
    """Blank is an unanswered question, not a decline. Forcing dial_in
    False here makes the box come back unticked when the draft reopens,
    because _redisplay_extras rebuilds it from config."""
    line = PhoneLine(index=1, source="NEW", dial_in=True, voice_delivery="")
    cfg = expand_to_lines(_answers([_space(phone_lines=(line,))]))[0].config
    assert cfg["dial_in"] is True


def test_an_unanswered_delivery_is_recorded_as_a_question():
    """The delivery select is always on screen for a line that owns its
    number, so leaving it blank is a question, not a silent text-only
    number."""
    line = PhoneLine(index=1, source="NEW", texts=True,
                     text_slack_channel="#texts", voice_delivery="")
    assert QUESTION_VOICE_DELIVERY in phone_open_questions(line)


def test_a_share_of_an_unanswered_line_is_not_treated_as_text_only():
    """Invariant 7 drops handsets on a number with no voice. An owner who
    has not answered the voice question yet is not such a number, and
    dropping its sharer's handset loses the space from the request."""
    owner = _space(space_id=1, phone_lines=(
        PhoneLine(index=1, source="NEW", texts=True, voice_delivery=""),))
    sharer = _space(space_id=2, phone_lines=(
        PhoneLine(index=1, source="1:1",
                  handsets=(PhoneHandset("Counter"),)),))
    codes = [l.service_code for l in expand_to_lines(_answers([owner, sharer]))]
    assert codes == ["PHONE_NUMBER", "DESK_PHONE"]


def test_handsets_typed_then_abandoned_do_not_become_lines():
    """The handset inputs stay in the DOM and keep posting after the
    requester switches delivery away from Desk phone. Emitting them makes
    an invisible row that survives every later edit."""
    line = PhoneLine(index=1, source="NEW", dial_in=True,
                     voice_delivery=VOICE_DELIVERY_VOICEMAIL,
                     voicemail_slack_channel="#vm",
                     handsets=(PhoneHandset("Front counter"),))
    codes = [l.service_code for l in
             expand_to_lines(_answers([_space(phone_lines=(line,))]))]
    assert codes == ["PHONE_NUMBER"]


def _wired(**kw):
    """A room that answered yes to needing a cable, fully described."""
    defaults = dict(network_needed=NETWORK_YES,
                    network_kinds=("COMPUTERS", "SCANNERS"),
                    network_count="3-5", network_traffic="LOCAL",
                    network_notes="")
    defaults.update(kw)
    return _space(**defaults)


def test_a_wired_room_produces_one_ethernet_line_not_one_per_drop():
    """The network team decides how many drops that takes. A requester
    counting drops was specifying an installation plan they have no way to
    make."""
    lines = expand_to_lines(_answers([_wired(network_notes="Stage left rack")]))
    assert [l.service_code for l in lines] == ["ETHERNET"]
    line = lines[0]
    assert line.location is None
    assert line.usage == "Stage left rack"
    assert line.config["kinds"] == ["COMPUTERS", "SCANNERS"]
    assert line.config["rough_count"] == "3-5"
    assert line.config["traffic"] == "LOCAL"


def test_a_room_that_needs_no_cable_produces_no_ethernet_line():
    lines = expand_to_lines(_answers([_space(network_needed=NETWORK_NO)]))
    assert [l.service_code for l in lines] == []


def test_not_sure_still_produces_a_line_carrying_the_question():
    """Required to answer, allowed not to know. Without a line there is
    nothing for the network team to chase."""
    lines = expand_to_lines(_answers([_space(network_needed=NETWORK_UNSURE)]))
    assert [l.service_code for l in lines] == ["ETHERNET"]
    assert lines[0].config["open_questions"] == [QUESTION_NETWORK_UNSURE]


def test_yes_with_no_device_kinds_is_a_question_not_a_refusal():
    lines = expand_to_lines(_answers([_wired(network_kinds=())]))
    assert lines[0].config["open_questions"] == [QUESTION_NETWORK_KINDS]


def test_an_unanswered_gate_produces_no_line():
    """Nothing was said, so nothing is planned. validate() is what refuses
    this on submit, not a silent default here."""
    assert expand_to_lines(_answers([_space(network_needed="")])) == []


def test_wired_gear_still_forces_a_wifi_answer():
    from app.routes.work.techops.line_grain import wifi_is_forced
    assert wifi_is_forced(_wired()) is True
    assert wifi_is_forced(_space(network_needed=NETWORK_NO)) is False
    assert wifi_is_forced(_space(phone_lines=(_voice_line(),))) is True
