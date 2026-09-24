"""Parsing the room-first form.

Field names carry the real space id, so there is no synthetic key to
reconcile. Every bound is checked here: SQLite enforces none of them and
Postgres enforces all of them.
"""
from werkzeug.datastructures import MultiDict

from app.routes.work.techops.form_utils import (
    MAX_CALLER_ID_LENGTH,
    MAX_DEPARTMENT_WIDE_INSTANCES,
    MAX_HANDSETS_PER_LINE,
    MAX_INT4,
    MAX_PHONE_LINES_PER_SPACE,
    MAX_SPACES_PER_REQUEST,
    _space_id_or_none,
    parse_form,
)


def _base():
    return MultiDict([
        ("primary_contact_name", "Ada"),
        ("primary_contact_email", "ada@magfest.org"),
        ("action", "SAVE_DRAFT"),
    ])


def test_a_space_card_becomes_a_space_answer():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_WIFI_enabled", "1")
    form.add("space_412_WIFI_description", "Badge scanners")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert errors == []
    assert len(answers.spaces) == 1
    assert answers.spaces[0].space_id == 412
    assert answers.spaces[0].wifi_requested is True
    assert answers.spaces[0].wifi_description == "Badge scanners"


def test_wifi_enabled_posted_as_0_parses_false_not_unanswered():
    """The radio pair's "No WiFi needed here" option posts "0", distinct
    from the field being absent entirely. A parser that folded "0" into
    the same bucket as absent could not tell an explicit decline from a
    card nobody opened."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_WIFI_enabled", "0")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].wifi_requested is False


def test_wifi_enabled_absent_parses_as_unanswered_not_declined():
    """Neither radio posts a value when neither is picked. A checkbox
    could only ever produce this same absent-field state for "declined"
    too; the radio pair's whole point is telling them apart."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].wifi_requested is None


def test_ticking_wifi_clears_a_stale_decline_reason():
    """_expand_space gives a decline reason precedence over the checkbox
    (`declined = bool(space.wifi_declined_reason)`), so a reason left over
    from a previous "no WiFi" answer would silently suppress the WiFi line
    even with the box ticked. Ticking is the current answer; the reason
    must not survive it."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_WIFI_enabled", "1")
    form.add("space_412_WIFI_declined_reason", "No staff working this room")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].wifi_requested is True
    assert answers.spaces[0].wifi_declined_reason == ""


def test_a_decline_reason_survives_an_explicit_decline():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_WIFI_enabled", "0")
    form.add("space_412_WIFI_declined_reason", "No staff working this room")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].wifi_requested is False
    assert answers.spaces[0].wifi_declined_reason == "No staff working this room"


def test_an_untouched_decline_reason_survives_wifi_left_unanswered():
    """A reason posted (by a crafted request; the field only ever renders
    for a declined, forced space) while neither radio is checked must
    still come through unanswered, not get promoted to a decline just
    because a reason happens to be present."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_WIFI_declined_reason", "No staff working this room")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].wifi_requested is None
    assert answers.spaces[0].wifi_declined_reason == "No staff working this room"


def test_a_phone_line_source_posted_blank_stays_blank():
    """An orphaned handset's source is deliberately blank
    (_redisplay_extras) so validate() refuses the submit and the
    requester re-picks, rather than the line silently becoming its own
    new, billable number. A field entirely absent still defaults to NEW
    (test_a_sharing_line_defaults_to_new_when_source_is_blank) — only a
    field posted empty is a preserved orphan."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_usage", "Front counter")
    form.add("space_412_PHONE_line_1_source", "")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].phone_lines[0].source == ""


def test_a_space_card_carries_its_catalog_display_name():
    """display_name comes from offerable_spaces, not the form: the browser
    never posts it, and validate()'s per-space errors depend on it being
    here rather than blank."""
    form = _base()
    form.add("space_ids", "412")
    answers, _ = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].display_name == "Expo Hall E"


def test_a_space_with_no_catalog_name_falls_back_to_a_non_empty_label():
    """A blank catalog name must never surface as a blank name or the raw
    id; every message built from it stays readable and actionable."""
    form = _base()
    form.add("space_ids", "412")
    answers, _ = parse_form(form, offerable_spaces={412: ""})
    assert answers.spaces[0].display_name
    assert answers.spaces[0].display_name != "412"


def test_drops_are_indexed_and_blank_rows_are_dropped():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_ETHERNET_drop_1_location", "Front")
    form.add("space_412_ETHERNET_drop_1_usage", "Scanner")
    form.add("space_412_ETHERNET_drop_2_location", "")
    form.add("space_412_ETHERNET_drop_2_usage", "")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert len(answers.spaces[0].ethernet_drops) == 1


def test_a_partial_drop_survives_as_a_half_finished_row():
    """A parse error would block the preview endpoint on every keystroke;
    only validate() (Task 6) may reject a partial row on submit."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_ETHERNET_drop_1_location", "Front")
    form.add("space_412_ETHERNET_drop_1_usage", "")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert errors == []
    assert len(answers.spaces[0].ethernet_drops) == 1
    assert answers.spaces[0].ethernet_drops[0].location == "Front"
    assert answers.spaces[0].ethernet_drops[0].usage == ""


def test_space_id_or_none_rejects_zero_negative_and_int4_overflow():
    """A value above int4 can never be a real Space.id, so at this one
    call site the offerable-set check below already rejects it; this test
    exercises the ceiling clause directly so it cannot be satisfied by
    that other check alone. Any other caller of this helper that queries
    by id without a membership check behind it depends on this clause."""
    assert _space_id_or_none(str(MAX_INT4 + 1)) is None
    assert _space_id_or_none("9" * 25) is None
    assert _space_id_or_none("0") is None
    assert _space_id_or_none("-1") is None
    assert _space_id_or_none("abc") is None
    assert _space_id_or_none("412") == 412


def test_a_space_id_above_int4_is_rejected_with_an_error_not_dropped():
    """Proves parse_form errors and drops the card rather than silently
    keeping nothing for it; the ceiling itself is proven separately above,
    since the offerable-set check alone would reject a value this large
    either way."""
    form = _base()
    form.add("space_ids", str(MAX_INT4 + 1))
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces == ()
    assert len(errors) == 1
    assert "not available" in errors[0]


def test_a_non_offerable_space_id_is_rejected_not_dropped():
    form = _base()
    form.add("space_ids", "999")
    form.add("space_999_answer", "NEEDS")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces == ()
    assert len(errors) == 1


def test_a_non_numeric_space_id_is_rejected():
    form = _base()
    form.add("space_ids", "412; DROP TABLE spaces")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces == ()
    assert len(errors) == 1


def test_a_rejected_space_id_is_not_echoed_in_the_error():
    form = _base()
    form.add("space_ids", "412; DROP TABLE spaces")
    _, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert "DROP TABLE" not in errors[0]


def test_up_to_the_space_bound_is_accepted():
    form = _base()
    ids = list(range(1, MAX_SPACES_PER_REQUEST + 1))
    for i in ids:
        form.add("space_ids", str(i))
    answers, errors = parse_form(form, offerable_spaces={i: f"Space {i}" for i in ids})
    assert len(answers.spaces) == MAX_SPACES_PER_REQUEST
    assert errors == []


def test_past_the_space_bound_truncates_with_an_error_naming_it():
    form = _base()
    ids = list(range(1, MAX_SPACES_PER_REQUEST + 2))
    for i in ids:
        form.add("space_ids", str(i))
    answers, errors = parse_form(form, offerable_spaces={i: f"Space {i}" for i in ids})
    assert len(answers.spaces) == MAX_SPACES_PER_REQUEST
    assert any(
        f"at most {MAX_SPACES_PER_REQUEST} spaces" in e for e in errors)


def test_too_many_drops_is_an_error_naming_the_bound():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    for n in range(1, 40):
        form.add(f"space_412_ETHERNET_drop_{n}_location", f"Spot {n}")
        form.add(f"space_412_ETHERNET_drop_{n}_usage", "Gear")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert any("25" in e for e in errors)


def test_a_phone_line_becomes_a_phone_line():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_usage", "Front counter")
    form.add("space_412_PHONE_line_1_caller_id_name", "regdesk")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert errors == []
    lines = answers.spaces[0].phone_lines
    assert len(lines) == 1
    assert lines[0].index == 1
    assert lines[0].purpose == "VOICE"
    # Upper-cased on the way in, per the phone system's display convention.
    assert lines[0].caller_id_name == "REGDESK"


def test_an_untouched_phone_line_slot_is_not_a_line():
    """purpose, usage, and handsets all blank means the requester never
    opened that slot, not that they submitted an empty line."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_internal_only", "1")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].phone_lines == ()


def test_a_blank_first_line_does_not_shift_the_second_lines_index():
    """The index a sharing line's `source` names is the position in the
    owning space's OWN field names, not a renumbering of filled lines.
    Skipping slot 1 must not relabel slot 2 as slot 1."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    # Line 1 left untouched.
    form.add("space_412_PHONE_line_2_purpose", "VOICE")
    form.add("space_412_PHONE_line_2_usage", "Back office")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    lines = answers.spaces[0].phone_lines
    assert len(lines) == 1
    assert lines[0].index == 2


def test_two_phone_lines_in_one_space_get_distinct_indexes():
    """Breaking edit that reproduces the Task 4 review finding: derive
    `index` from a form field (e.g. int(form.get(f"...line_{n}_index")))
    instead of the loop position `n`. With a fixed or attacker-supplied
    index value, both lines would collide and the second would silently
    replace the first, per line_grain._sharing_key."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_usage", "Front counter")
    form.add("space_412_PHONE_line_2_purpose", "VOICE")
    form.add("space_412_PHONE_line_2_usage", "Back office")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    lines = answers.spaces[0].phone_lines
    assert [line.index for line in lines] == [1, 2]
    assert len(set(line.index for line in lines)) == 2


def test_a_stray_index_field_in_the_post_is_ignored():
    """There is no `..._index` field in the schema; a client that posts
    one anyway must have no effect, proving index cannot be read from the
    form even when a caller tries to smuggle one in."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_usage", "Front counter")
    form.add("space_412_PHONE_line_1_index", "99")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].phone_lines[0].index == 1


def test_a_handset_count_disagreeing_with_filled_placements_is_not_trusted():
    """A count of 3 with only 1 filled placement must not produce 3
    handsets: PhoneLine has no count field, only the handsets it was
    actually given."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_usage", "Front counter")
    form.add("space_412_PHONE_line_1_handset_count", "3")
    form.add("space_412_PHONE_line_1_handset_1_location", "Position 1")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    handsets = answers.spaces[0].phone_lines[0].handsets
    assert len(handsets) == 1
    assert handsets[0].location == "Position 1"


def test_too_many_phone_lines_is_an_error_naming_the_bound():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    for n in range(1, MAX_PHONE_LINES_PER_SPACE + 5):
        form.add(f"space_412_PHONE_line_{n}_purpose", "VOICE")
        form.add(f"space_412_PHONE_line_{n}_usage", "Line")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert any(str(MAX_PHONE_LINES_PER_SPACE) in e for e in errors)


def test_too_many_handsets_is_an_error_naming_the_bound():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_usage", "Front counter")
    for h in range(1, MAX_HANDSETS_PER_LINE + 5):
        form.add(f"space_412_PHONE_line_1_handset_{h}_location", f"Position {h}")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert any(str(MAX_HANDSETS_PER_LINE) in e for e in errors)


def test_an_over_length_caller_id_is_an_error_and_is_not_truncated():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_usage", "Front counter")
    long_name = "a" * (MAX_CALLER_ID_LENGTH + 5)
    form.add("space_412_PHONE_line_1_caller_id_name", long_name)
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert any(str(MAX_CALLER_ID_LENGTH) in e for e in errors)
    # Not silently shortened: the full (upper-cased) value survives so the
    # requester sees exactly what they typed on the redraw.
    assert answers.spaces[0].phone_lines[0].caller_id_name == long_name.upper()


def test_a_sharing_line_defaults_to_new_when_source_is_blank():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_usage", "Front counter")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].phone_lines[0].source == "NEW"


def test_a_sharing_source_is_passed_through_for_task_6_to_validate():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("space_412_PHONE_line_1_purpose", "VOICE")
    form.add("space_412_PHONE_line_1_source", "7:1")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.spaces[0].phone_lines[0].source == "7:1"


def test_department_wide_radio_channel_uses_the_old_field_names():
    form = _base()
    form.add("service_RADIO_CHANNEL_instance_1_location", "Ops")
    form.add("service_RADIO_CHANNEL_instance_1_usage", "Coordination")
    answers, errors = parse_form(form, offerable_spaces={})
    assert errors == []
    assert len(answers.department_wide) == 1
    entry = answers.department_wide[0]
    assert entry.service_code == "RADIO_CHANNEL"
    assert entry.location == "Ops"


def test_department_wide_other_uses_the_old_field_names():
    form = _base()
    form.add("service_OTHER_description", "Consultation about cabling")
    answers, errors = parse_form(form, offerable_spaces={})
    entries = [e for e in answers.department_wide if e.service_code == "OTHER"]
    assert len(entries) == 1
    assert entries[0].description == "Consultation about cabling"


def test_a_channel_row_with_content_produces_a_line_with_no_enabled_flag_posted():
    """The `service_RADIO_CHANNEL_enabled` checkbox is gone from the form
    (fix-department-wide-checkboxes): content alone is the signal now, the
    same rule ethernet drops use. A form post that never includes the flag
    at all, the only kind a real browser can send post-removal, must still
    produce the line."""
    form = _base()
    form.add("service_RADIO_CHANNEL_instance_1_location", "Tech-1")
    form.add("service_RADIO_CHANNEL_instance_1_usage", "Ops chatter")
    answers, errors = parse_form(form, offerable_spaces={})
    entries = [e for e in answers.department_wide if e.service_code == "RADIO_CHANNEL"]
    assert len(entries) == 1
    assert entries[0].location == "Tech-1"


def test_a_blank_other_description_produces_no_line():
    """OTHER is gated on its description being non-blank now that the
    `service_OTHER_enabled` checkbox is gone. An "other / consultation"
    request that says nothing is not a request."""
    form = _base()
    form.add("service_OTHER_description", "")
    answers, errors = parse_form(form, offerable_spaces={})
    assert not any(e.service_code == "OTHER" for e in answers.department_wide)


def test_too_many_radio_channels_is_an_error_naming_the_bound():
    form = _base()
    for n in range(1, MAX_DEPARTMENT_WIDE_INSTANCES + 5):
        form.add(f"service_RADIO_CHANNEL_instance_{n}_location", f"Ch {n}")
        form.add(f"service_RADIO_CHANNEL_instance_{n}_usage", "Ops")
    answers, errors = parse_form(form, offerable_spaces={})
    assert any(str(MAX_DEPARTMENT_WIDE_INSTANCES) in e for e in errors)


def test_save_space_id_is_parsed_from_a_per_card_saves_own_field():
    """Item 2: the "Save this space" button posts save_space_id, not
    action, so the route knows which card to send the requester back to.
    """
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("save_space_id", "412")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert errors == []
    assert answers.save_space_id == 412


def test_save_space_id_defaults_to_none_when_not_posted():
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert answers.save_space_id is None


def test_save_space_id_ignores_garbage_rather_than_erroring():
    """It only ever steers a redirect, never a write, so a bad value must
    not fail the whole save the way a bad space_ids entry does."""
    form = _base()
    form.add("space_ids", "412")
    form.add("space_412_answer", "NEEDS")
    form.add("save_space_id", "not-a-number")
    answers, errors = parse_form(form, offerable_spaces={412: "Expo Hall E"})
    assert errors == []
    assert answers.save_space_id is None


def test_the_pickers_blank_placeholder_does_not_count_toward_the_space_bound():
    """100 real spaces plus the picker's empty option posts 101 values.
    Counting the blank refuses the save while naming a limit the requester
    has not reached, and they cannot save at all.
    """
    form = _base()
    names = {}
    for n in range(1, 101):
        form.add("space_ids", str(n))
        form.add(f"space_{n}_answer", "NEEDS")
        names[n] = f"Room {n}"
    form.add("space_ids", "")  # the picker's placeholder
    answers, errors = parse_form(form, names)
    assert len(answers.spaces) == 100
    assert not any("at most" in e for e in errors)
