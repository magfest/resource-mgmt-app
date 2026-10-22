"""compose_combined_name(): the one rule for naming a combined space.

Pure function, no app context needed. spaces_for_department() and
build_space_rows() both call this instead of each inventing its own rule;
tests/admin/test_spaces_queries.py and tests/admin/test_spaces_combinations.py
cover those two call sites and their agreement with each other.
"""
from app.models.spaces import compose_aliased_name, compose_combined_name


def test_two_slices_sharing_a_word_join_with_a_slash():
    assert compose_combined_name(
        ["Chesapeake 4", "Chesapeake 5"]) == "Chesapeake 4/5"


def test_three_slices_reproduce_the_rooms_own_name():
    # The Gaylord's own catalog calls the fully-combined room "RiverView
    # 1/2/3", not "RiverView"; composing all three slices must land on
    # that same string, not invent a shorter one.
    room_name = "RiverView 1/2/3"
    composed = compose_combined_name(
        ["RiverView 1", "RiverView 2", "RiverView 3"])
    assert composed == "RiverView 1/2/3"
    assert composed == room_name


def test_a_multi_word_shared_prefix_is_kept_whole():
    assert compose_combined_name(
        ["Maryland Ballroom A", "Maryland Ballroom B"]
    ) == "Maryland Ballroom A/B"


def test_no_shared_leading_word_falls_back_to_plus():
    assert compose_combined_name(
        ["North End", "South End"]) == "North End + South End"


def test_a_single_name_is_returned_unchanged():
    assert compose_combined_name(["Chesapeake 4"]) == "Chesapeake 4"


def test_a_prefix_that_is_someones_whole_name_falls_back_to_plus():
    # "Chesapeake" is a prefix of "Chesapeake 5" but is also the entirety
    # of the first name; there is no tail left to join it against.
    assert compose_combined_name(
        ["Chesapeake", "Chesapeake 5"]) == "Chesapeake + Chesapeake 5"


def test_an_aliased_single_space_names_its_room_in_brackets():
    assert compose_aliased_name(
        "Staff Coffee Room", ["Azalea 1"]) == "Staff Coffee Room (Azalea 1)"


def test_an_alias_matching_the_room_name_is_not_repeated():
    assert compose_aliased_name("Potomac 3", ["Potomac 3"]) == "Potomac 3"


def test_a_run_of_three_or_more_collapses_to_a_range_and_the_rest_list_singly():
    names = [f"Maryland {tail}" for tail in "123456ABD"]
    assert compose_aliased_name("LAN", names) == "LAN (Maryland 1-6, A, B, D)"


def test_a_run_of_letters_collapses_too():
    names = [f"Potomac {tail}" for tail in "ABCD"]
    assert compose_aliased_name("Mainstage", names) == "Mainstage (Potomac A-D)"


def test_a_pair_keeps_the_slash_form():
    assert compose_aliased_name(
        "Rock Island", ["Potomac 1", "Potomac 2"]) == "Rock Island (Potomac 1/2)"
    assert compose_aliased_name(
        "Pathfinder", ["National Harbor 12", "National Harbor 13"]
    ) == "Pathfinder (National Harbor 12/13)"


def test_ranges_follow_the_given_order_rather_than_sorting():
    # Event Ops sets sort_order; 3, 1, 2 is not a run in that order.
    names = ["Potomac 3", "Potomac 1", "Potomac 2"]
    assert compose_aliased_name("Odd", names) == "Odd (Potomac 3/1/2)"


def test_a_tail_that_is_not_a_number_or_single_letter_keeps_the_slash_form():
    names = ["Hall 1", "Hall 2", "Hall 3", "Hall Foyer"]
    assert compose_aliased_name("Expo", names) == "Expo (Hall 1/2/3/Foyer)"


def test_unshared_names_fall_back_to_plus_inside_the_brackets():
    assert compose_aliased_name(
        "Ends", ["North End", "South End"]) == "Ends (North End + South End)"
