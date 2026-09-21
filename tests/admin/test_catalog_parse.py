"""Parsing a pasted venue table.

Pure functions, no Flask and no database. The parser decides what a paste
means; the catalog_paste_confirm route decides what to write.
"""
from app.routes.spaces.catalog_parse import (
    MAX_CODE_LENGTH, _normalize_name, generate_code, implied_room_code,
    is_slice_coded, parse_rows,
)


def test_a_code_comes_from_the_first_word_and_the_last_token():
    assert generate_code("Chesapeake G", "SLICE") == "CHE-S-G"
    assert generate_code("Annapolis 1", "SLICE") == "ANN-S-1"
    assert generate_code("Woodrow Wilson A", "SLICE") == "WOO-S-A"


def test_a_slashed_suffix_loses_its_slashes():
    assert generate_code("Chesapeake G/H/I", "ROOM") == "CHE-P-GHI"


def test_a_long_last_word_is_dropped_rather_than_mangled():
    """Better a short code a person fixes than a long one nobody reads."""
    assert generate_code("Maryland Ballroom", "ROOM") == "MAR-P"
    assert generate_code("Fort Washington Boardroom", "ROOM") == "FOR-P"


def test_a_digit_led_name_generates_a_usable_code():
    """A room numbered 302 having the code 302 is correct, not a
    workaround; the old letters-only prefix rule threw the digits away and
    generated an empty string instead."""
    assert generate_code("302 Boardroom", "ROOM") == "302-P"
    assert generate_code("3 Rivers Ballroom", "ROOM") == "3-P"


def test_a_name_with_no_letters_or_digits_generates_no_code():
    """A CJK name has no ASCII letter or digit for the rule to keep. The
    caller must reject this, not write the empty string it gets back, and
    it never carries a bare kind marker on its own."""
    assert generate_code("会議室", "ROOM") == ""
    assert generate_code("会議室", "SLICE") == ""


def test_a_generated_code_carries_its_kind():
    """A room gets -P-, a slice gets -S-, inserted after the prefix. These
    codes are pasted into Slack and read with no app next to them; marking
    only the ambiguous ones would make an unmarked one look mistyped."""
    assert generate_code("Woodrow Wilson Ballroom", "ROOM") == "WOO-P"
    assert generate_code("Woodrow Wilson A", "SLICE") == "WOO-S-A"
    assert generate_code("Baltimore 1&2", "ROOM") == "BAL-P-12"
    assert generate_code("Baltimore 1", "SLICE") == "BAL-S-1"
    assert generate_code("Baltimore 3-5", "ROOM") == "BAL-P-35"
    assert generate_code("Baltimore 3", "SLICE") == "BAL-S-3"


def test_the_longest_generated_code_stays_well_under_the_bound():
    """3 (prefix) + 3 ("-P-" or "-S-") + 4 (suffix) = 10 characters,
    confirmed rather than assumed, far inside MAX_CODE_LENGTH's 32."""
    code = generate_code("Annapolis Suite ABCD", "ROOM")

    assert code == "ANN-P-ABCD"
    assert len(code) == 10
    assert len(code) < MAX_CODE_LENGTH


def test_a_row_with_fewer_than_five_columns_is_rejected():
    """A three-column sheet (Name, Dimensions, Area only, no Code, no
    Parent code) is the paste that shipped this bug: padding it silently
    put a dimension string in the code field. Name what was found instead
    of guessing which columns are missing."""
    rows = parse_rows("Meeting Room A\t136X102X22\t2100",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "3 columns" in rows[0].error
    assert "all 5 are needed" in rows[0].error
    # No guessing: the dimension string must not land in code.
    assert rows[0].code == ""


def test_a_five_column_row_with_trailing_blanks_still_parses_clean():
    rows = parse_rows("Solo Room\tSOLO\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is None
    assert rows[0].name == "Solo Room"
    assert rows[0].code == "SOLO"
    assert rows[0].dimensions is None
    assert rows[0].area_sqft is None
    assert rows[0].parent_code is None


def test_tab_separated_rows_parse():
    rows = parse_rows(
        "Chesapeake G/H/I\tCHE-GHI\t29x86x14\t2453\t\n"
        "Chesapeake G\tCHE-G\t29x28x14\t811\tCHE-GHI",
        existing_codes=set(), existing_names=set(), legal_parent_codes=set(),
    )

    assert [r.name for r in rows] == ["Chesapeake G/H/I", "Chesapeake G"]
    assert rows[1].parent_code == "CHE-GHI"
    assert rows[0].area_sqft == 2453
    assert all(r.error is None for r in rows)


def test_a_header_row_is_skipped():
    rows = parse_rows("Name\tCode\tDimensions\tArea\tParent code\n"
                      "Chesapeake G\tCHE-G\t\t811\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert len(rows) == 1
    assert rows[0].name == "Chesapeake G"


def test_a_header_reading_room_name_is_skipped():
    """A real sheet's header cell is rarely the bare word "Name"."""
    rows = parse_rows("Room Name\tCode\tDimensions\tArea\tParent code\n"
                      "Chesapeake G\tCHE-G\t\t811\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert len(rows) == 1
    assert rows[0].name == "Chesapeake G"


def test_a_leading_blank_line_does_not_defeat_the_header_skip():
    rows = parse_rows("\nName\tCode\tDimensions\tArea\tParent code\n"
                      "Chesapeake G\tCHE-G\t\t811\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert len(rows) == 1
    assert rows[0].name == "Chesapeake G"


def test_a_header_with_two_matching_cells_is_recognized_by_any_cell():
    """The Gaylord sheet that exposed this bug: "Meeting Room" alone names
    no recognized column, but "DIMENSIONS (LXWXH)" and "Area (sq.ft)" do,
    two matching cells, meeting the corrected threshold. The header is
    only three columns, matching its own data rows, so it must be caught
    before the short-row check runs."""
    rows = parse_rows(
        "Meeting Room\tDIMENSIONS (LXWXH)\tArea (sq.ft)\n"
        "Potomac 1\t30X20X12\t600",
        existing_codes=set(), existing_names=set(), legal_parent_codes=set(),
    )

    assert len(rows) == 1
    assert rows[0].name == "Potomac 1"
    # Still short on real data (no Code, no Parent code column), so it is
    # the new short-row error, not a silently shifted guess.
    assert rows[0].error is not None
    assert "3 columns" in rows[0].error


def test_a_first_row_with_only_one_matching_cell_is_kept_as_data():
    """One label-shaped cell is not a header. Real spaces are named
    "Pre-function Area" and "Registration Area"; a looser rule would
    swallow the first one pasted."""
    rows = parse_rows("Pre-function Area\tPRE-P\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert len(rows) == 1
    assert rows[0].name == "Pre-function Area"
    assert rows[0].error is None


def test_a_row_with_two_matching_cells_still_loses_its_row():
    """The narrower cost of the corrected rule: two label-shaped cells in
    a genuine data row, not just one, is still mistaken for a header."""
    rows = parse_rows("Area 51\tCODE1\t\t500\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert len(rows) == 0


def test_a_blank_code_is_generated_and_marked():
    rows = parse_rows("Chesapeake G\t\t29x28x14\t811\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].code == "CHE-P-G"
    assert rows[0].generated_code is True


def test_a_generated_slice_code_is_marked_s_not_p():
    """A row with its own parent code present generates a slice-marked
    code, the same signal that decides kind elsewhere."""
    rows = parse_rows("Chesapeake G\t\t\t\tCHE-GHI",
                      existing_codes={"CHE-GHI"}, existing_names=set(),
                      legal_parent_codes={"CHE-GHI"})

    assert rows[0].code == "CHE-S-G"
    assert rows[0].generated_code is True


def test_a_generated_codes_kind_survives_an_illegal_parent():
    """Kind comes from whether a parent code was pasted, not from whether
    it resolves; code generation runs before the second pass that checks
    legality, and must not wait on it."""
    rows = parse_rows("Chesapeake G\t\t\t\tNOPE",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].code == "CHE-S-G"
    assert rows[0].error is not None
    assert "NOPE" in rows[0].error


def test_a_blank_code_that_generates_nothing_is_rejected():
    rows = parse_rows("会議室\t\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "code" in rows[0].error.lower()


def test_a_row_with_no_name_is_rejected():
    rows = parse_rows("\tCHE-G\t29x28x14\t811\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "name" in rows[0].error.lower()


def test_a_non_numeric_area_is_rejected_not_dropped():
    """Silently storing nothing loses a number the person typed."""
    rows = parse_rows("Maryland A\tMAR-A\t99x54x28\t1,200\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "number" in rows[0].error.lower()


def test_a_negative_area_is_rejected():
    rows = parse_rows("Room one\tR1\t\t-5\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "zero or greater" in rows[0].error.lower()


def test_an_area_over_the_postgres_int4_bound_is_rejected():
    """SQLite stores this value silently; Postgres raises. Same bound the
    add/edit form enforces via MAX_AREA_SQFT."""
    rows = parse_rows("Room one\tR1\t\t99999999999999\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "square feet" in rows[0].error.lower()


def test_a_code_over_32_characters_is_rejected():
    """Space.code is String(32); SQLite does not enforce it, Postgres
    does. Same bound the add/edit form enforces via flash_if_too_long."""
    rows = parse_rows(f"Room one\t{'X' * 33}\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "longer than 32 characters" in rows[0].error


def test_a_code_repeated_in_the_paste_is_rejected():
    rows = parse_rows("Room one\tDUP\t\t\t\nRoom two\tDUP\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is None
    assert rows[1].error is not None
    assert "already" in rows[1].error.lower()


def test_a_code_the_venue_already_holds_is_rejected():
    rows = parse_rows("Room one\tCHE-G\t\t\t",
                      existing_codes={"CHE-G"}, existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None


def test_a_parent_may_appear_after_its_child():
    """People paste in sheet order, and a venue sheet lists the whole room
    after its pieces as often as before."""
    rows = parse_rows("Chesapeake G\tCHE-G\t\t\tCHE-GHI\n"
                      "Chesapeake G/H/I\tCHE-GHI\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert all(r.error is None for r in rows)


def test_an_unknown_parent_code_is_rejected_by_name():
    rows = parse_rows("Chesapeake G\tCHE-G\t\t\tNOPE",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "NOPE" in rows[0].error


def test_a_parent_code_that_is_not_a_legal_parent_is_rejected():
    """CHE-GHI is a real code at the venue, but a slice or an archived or
    event-scoped room, so it is in existing_codes and not in
    legal_parent_codes. The add form's picker would refuse it too."""
    rows = parse_rows("Chesapeake G\tCHE-G\t\t\tCHE-GHI",
                      existing_codes={"CHE-GHI"}, existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "CHE-GHI" in rows[0].error
    assert "room" in rows[0].error.lower()


def test_a_slice_pasted_earlier_cannot_parent_another_row():
    """A pasted room can parent a row pasted after it, but a pasted slice
    cannot, even though its code is known by then; it is not itself a
    room, mirroring the add form's room-only parent rule."""
    rows = parse_rows(
        "Chesapeake G/H/I\tCHE-GHI\t\t\t\n"
        "Chesapeake G\tCHE-G\t\t\tCHE-GHI\n"
        "Chesapeake G1\tCHE-G1\t\t\tCHE-G",
        existing_codes=set(), existing_names=set(), legal_parent_codes=set(),
    )

    assert rows[0].error is None
    assert rows[1].error is None
    assert rows[2].error is not None
    assert "CHE-G" in rows[2].error
    assert "room" in rows[2].error.lower()


def test_a_self_parented_row_is_rejected():
    rows = parse_rows("Chesapeake G\tCHE-G\t\t\tCHE-G",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "own parent" in rows[0].error.lower()


def test_a_name_repeated_in_the_paste_is_rejected():
    """The venue-wide name rule the add/edit form enforces applies to a
    paste too, or the paste path is a hole in it."""
    rows = parse_rows("Room one\tR1\t\t\t\nRoom one\tR2\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is None
    assert rows[1].error is not None
    assert "already" in rows[1].error.lower()


def test_a_name_the_venue_already_holds_is_rejected():
    rows = parse_rows("Chesapeake G\tNEW\t\t\t",
                      existing_codes=set(),
                      existing_names={_normalize_name("Chesapeake G")},
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "name" in rows[0].error.lower()


def test_a_repeated_name_is_matched_case_and_whitespace_insensitively():
    """Reuses the same normalization the add/edit form uses, so "Room one"
    and "room   one" are treated as the same name."""
    rows = parse_rows("Room one\tR1\t\t\t\nroom   ONE\tR2\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is None
    assert rows[1].error is not None


def test_extra_columns_past_parent_code_are_ignored_not_dropped_as_an_error():
    """A real venue sheet carries capacity columns this app excludes on
    purpose; a sixth column must not error or shift the other fields."""
    rows = parse_rows("Chesapeake G\tCHE-G\t29x28x14\t811\tCHE-GHI\t50\tnotes",
                      existing_codes={"CHE-GHI"}, existing_names=set(),
                      legal_parent_codes={"CHE-GHI"})

    assert rows[0].error is None
    assert rows[0].name == "Chesapeake G"
    assert rows[0].code == "CHE-G"
    assert rows[0].dimensions == "29x28x14"
    assert rows[0].area_sqft == 811
    assert rows[0].parent_code == "CHE-GHI"


def test_a_name_over_128_characters_is_rejected():
    """Space.name is String(128). SQLite stores a longer one, Postgres
    raises, so the paste path bounds it the way the form does."""
    rows = parse_rows(f"{'X' * 129}\tZZZ1\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is not None
    assert "longer than 128 characters" in rows[0].error


def test_a_name_of_exactly_128_characters_is_allowed():
    rows = parse_rows(f"{'X' * 128}\tZZZ1\t\t\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].error is None


def test_is_slice_coded_reads_the_marker_segment():
    assert is_slice_coded("MDB-S-1") is True
    assert is_slice_coded("MDB-S") is True
    assert is_slice_coded("MDB-P") is False
    assert is_slice_coded("CHE-GHI") is False


def test_is_slice_coded_is_case_insensitive():
    assert is_slice_coded("mdb-s-1") is True


def test_is_slice_coded_is_false_with_no_marker_at_all():
    assert is_slice_coded("SOLO") is False


def test_implied_room_code_drops_the_suffix():
    assert implied_room_code("MDB-S-1") == "MDB-P"
    assert implied_room_code("MDB-S") == "MDB-P"


def test_implied_room_code_is_none_for_a_room_or_unmarked_code():
    assert implied_room_code("MDB-P") is None
    assert implied_room_code("CHE-GHI") is None
    assert implied_room_code("SOLO") is None


def test_implied_room_code_upper_cases_a_lowercase_prefix():
    assert implied_room_code("mdb-s-1") == "MDB-P"


def test_a_blank_code_is_still_generated_when_the_area_is_invalid():
    """Pinned, not designed: area bounds run inside validate_row, after
    code generation, so a blank code alongside a bad area still gets
    generated instead of staying empty. The reported error is unchanged."""
    rows = parse_rows("Big Room\t\t\t-5\t",
                      existing_codes=set(), existing_names=set(),
                      legal_parent_codes=set())

    assert rows[0].code == "BIG-P-ROOM"
    assert rows[0].generated_code is True
    assert rows[0].error is not None
    assert "zero or greater" in rows[0].error.lower()
