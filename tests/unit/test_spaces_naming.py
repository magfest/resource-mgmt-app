"""compose_combined_name(): the one rule for naming a combined space.

Pure function, no app context needed. spaces_for_department() and
build_space_rows() both call this instead of each inventing its own rule;
tests/admin/test_spaces_queries.py and tests/admin/test_spaces_combinations.py
cover those two call sites and their agreement with each other.
"""
from app.models.spaces import compose_combined_name


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
