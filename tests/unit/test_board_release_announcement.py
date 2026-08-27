"""The board release posts one event-level Slack summary, not one post per item."""
from unittest.mock import patch

from app.models import EventCycle
from app.services.notifications import announce_board_release
from app.services.slack_messages import format_board_release


class _Cycle:
    """Stand-in for EventCycle. The formatter reads two attributes."""
    code = "SMF27"
    name = "Super MAGFest 2027"


def test_the_summary_names_the_event_and_the_count(app):
    with app.app_context():
        text, blocks = format_board_release(_Cycle(), 47)
    assert "Super MAGFest 2027" in text
    assert "47" in text
    assert blocks[0]["type"] == "section"


def test_one_released_budget_is_not_pluralised(app):
    with app.app_context():
        text, _ = format_board_release(_Cycle(), 1)
    assert "1 budget released" in text
    assert "budgets" not in text


def test_a_release_of_zero_announces_nothing(app):
    """release_event_budgets is idempotent, so a resubmitted form releases 0.

    Posting "0 budgets released" to the channel on every double-submit is
    noise the channel cannot act on.
    """
    with app.app_context():
        app.config["SLACK_ENABLED"] = True
        with patch("app.services.notifications.send_slack_message") as post:
            announce_board_release(_Cycle(), 0)
        assert post.call_count == 0


def test_the_announcement_posts_with_no_work_item(app):
    """work_item_id must be None: this is an event, not a work item.

    slack.py:130 skips the debounce when work_item_id is None, which is what
    lets two event releases inside one hour both announce.
    """
    with app.app_context():
        app.config["SLACK_ENABLED"] = True
        with patch("app.services.notifications.send_slack_message") as post:
            announce_board_release(_Cycle(), 3)
        assert post.call_count == 1
        assert post.call_args.kwargs["work_item_id"] is None
        assert post.call_args.kwargs["template_key"] == "board_release"


def test_a_slack_failure_does_not_raise(app):
    """The release is already committed. An announcement failure must not 500."""
    with app.app_context():
        app.config["SLACK_ENABLED"] = True
        with patch("app.services.notifications.send_slack_message",
                   side_effect=RuntimeError("slack down")):
            announce_board_release(_Cycle(), 3)  # must not raise
