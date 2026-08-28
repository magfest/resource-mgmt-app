"""
Slack message formatters for budget workflow events.

Each function returns (text, blocks) where:
- text: Plain fallback for notifications and non-Block Kit clients
- blocks: Slack Block Kit blocks for rich channel display
"""
from __future__ import annotations

from flask import current_app

from app.models import WorkItem


def _get_base_url() -> str:
    return current_app.config.get('BASE_URL', 'https://budget.magfest.org')


def _build_item_url(work_item: WorkItem) -> str:
    base = _get_base_url()
    portfolio = work_item.portfolio
    event = portfolio.event_cycle.code
    dept = portfolio.department.code
    return f"{base}/{event}/{dept}/{portfolio.work_type_slug}/item/{work_item.public_id}"


def _format_message(emoji: str, title: str, work_item: WorkItem, detail: str = "") -> tuple:
    """Build a standard Slack message with section + context blocks."""
    public_id = work_item.public_id
    dept_name = work_item.portfolio.department.name
    event_name = work_item.portfolio.event_cycle.name
    url = _build_item_url(work_item)

    text = f"{emoji} {title}: {public_id} — {dept_name}"

    context_parts = [f"*{dept_name}*  |  {event_name}  |  <{url}|View Request>"]
    if detail:
        context_parts.insert(0, detail)

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} *<{url}|{public_id}>* — {title}"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": line} for line in context_parts],
        },
    ]

    return text, blocks


def format_submitted(work_item: WorkItem) -> tuple:
    """Budget request submitted, awaiting dispatch."""
    return _format_message(
        ":inbox_tray:", "Submitted — awaiting dispatch", work_item,
    )


def format_dispatched(work_item: WorkItem) -> tuple:
    """Budget request dispatched to approval groups."""
    return _format_message(
        ":eyes:", "Dispatched for approval review", work_item,
    )


def format_needs_attention(work_item: WorkItem) -> tuple:
    """Reviewer requested more info or adjustments."""
    dept_name = work_item.portfolio.department.name
    return _format_message(
        ":warning:", "Needs attention", work_item,
        detail=f"A reviewer has requested action from {dept_name}",
    )


def format_response_received(work_item: WorkItem) -> tuple:
    """Requester responded to reviewer feedback."""
    return _format_message(
        ":speech_balloon:", "Response received", work_item,
    )


def format_finalized(work_item: WorkItem) -> tuple:
    """Budget request finalized."""
    return _format_message(
        ":white_check_mark:", "Finalized", work_item,
    )


def format_board_release(event_cycle, released_count: int) -> tuple:
    """The board approved an event topline and released its held budgets.

    Event-level, not work-item-level. One board action releases every held
    budget in the event, and one post per budget buries the channel.
    """
    url = f"{_get_base_url()}/admin/final/"
    noun = "budget" if released_count == 1 else "budgets"

    text = (f":unlock: FY budget approval recorded for {event_cycle.name}: "
            f"{released_count} {noun} released")

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":unlock: *FY budget approval recorded for {event_cycle.name}*",
            },
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"{released_count} {noun} released  |  <{url}|Open admin final>",
            }],
        },
    ]

    return text, blocks
