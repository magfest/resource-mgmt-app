"""User feedback from the nav bar's Feedback button, posted to Slack.

Feedback goes to SLACK_FEEDBACK_CHANNEL_ID, not the workflow channel, because
the team triages it as tasks. Slack is the only record; there is no feedback
table. The button renders only when is_feedback_enabled() is true, so a user
is never told "sent" by an environment that cannot send.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from flask import current_app

from app.services.slack import _check_circuit_breaker, is_slack_enabled, send_slack_message

FEEDBACK_MAX_CHARS = 4000
FEEDBACK_TEMPLATE_KEY = "feedback"


@dataclass(frozen=True)
class FeedbackContext:
    """Where the user was when they pressed Feedback. Display only; never trusted."""
    page_url: str = ""
    page_title: str = ""
    page_messages: str = ""
    event: str = ""
    dept: str = ""
    public_id: str = ""
    viewport: str = ""
    user_agent: str = ""
    host: str = ""
    viewing_as: str = ""
    role_override: str = ""


# Checked in order. Edge carries a Chrome token and Chrome carries a Safari
# token, so the more specific browser must match first.
_BROWSERS = (
    ("Edge", re.compile(r"Edg(?:A|iOS)?/(\d+)")),
    ("Firefox", re.compile(r"(?:Firefox|FxiOS)/(\d+)")),
    ("Chrome", re.compile(r"(?:Chrome|CriOS)/(\d+)")),
    ("Safari", re.compile(r"Version/(\d+).*Safari/")),
)
# iPhone and iPad user agents say "like Mac OS X", and Android says Linux.
_PLATFORMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("CrOS", "ChromeOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)


def short_browser(user_agent: str) -> str:
    """Return "Chrome 154 on Windows" style text, or the raw string if unrecognized."""
    browser = next(
        (f"{name} {m.group(1)}" for name, rx in _BROWSERS if (m := rx.search(user_agent))),
        None,
    )
    platform = next((label for token, label in _PLATFORMS if token in user_agent), None)
    if browser is None:
        return user_agent or "unknown browser"
    return f"{browser} on {platform}" if platform else browser


def is_feedback_enabled() -> bool:
    return is_slack_enabled() and bool(current_app.config.get("SLACK_FEEDBACK_CHANNEL_ID"))


def normalize_notes(raw: str | None) -> str:
    """Return the notes with CRLF folded to LF and outer whitespace stripped.

    Browsers submit textarea content with CRLF, so a raw length check counts
    each line break twice against FEEDBACK_MAX_CHARS.
    """
    return (raw or "").replace("\r\n", "\n").strip()


def _escape(text: str) -> str:
    # Slack reads <!channel>, <@U123> and <url|label> as control sequences.
    # Its documented escaping is these three entities and nothing else.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_feedback(notes: str, user, ctx: FeedbackContext) -> tuple[str, list[dict]]:
    """Build the fallback text and Block Kit blocks for one feedback post.

    `user` is the person who pressed the button. During impersonation that is
    the real signed-in admin, and ctx.viewing_as names the account on screen.
    """
    who = f"*{_escape(f'{user.display_name} ({user.email})')}*"
    if ctx.viewing_as:
        who += f" _viewing as {_escape(ctx.viewing_as)}_"
    if ctx.role_override:
        who += f" _(role override: {_escape(ctx.role_override)})_"

    where = "  ·  ".join(_escape(part) for part in (ctx.event, ctx.dept) if part)
    if ctx.public_id:
        where = f"{where}  ·  `{_escape(ctx.public_id)}`" if where else f"`{_escape(ctx.public_id)}`"

    context_line = who
    if where:
        context_line += f"  |  {where}"
    if ctx.page_url.startswith(("https://", "http://")):
        context_line += f"  |  <{_escape(ctx.page_url)}|Open page>"

    heading = ":speech_balloon: *Feedback*"
    if ctx.page_title:
        heading += f" on _{_escape(ctx.page_title)}_"
    quoted = "\n".join(f"> {line}" for line in _escape(notes).split("\n"))

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{heading}\n{quoted}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_line}]},
    ]
    if ctx.page_messages:
        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn", "text": f":warning: On screen: {_escape(ctx.page_messages)}",
        }]})
    tech = [ctx.host, ctx.viewport or "unknown size", short_browser(ctx.user_agent)]
    blocks.append({"type": "context", "elements": [{
        "type": "mrkdwn", "text": _escape("  ·  ".join(part for part in tech if part)),
    }]})

    text = _escape(f"Feedback from {user.display_name}: {notes[:150]}")
    return text, blocks


def send_feedback(notes: str, user, ctx: FeedbackContext) -> bool:
    """Post feedback to the feedback channel. Return True only if Slack accepted it.

    send_slack_message returns True when Slack is disabled or the circuit
    breaker is open, because workflow callers treat a skipped post as fine.
    Feedback cannot; the user would be told "sent" for a message nobody sees.
    Adds a NotificationLog row and leaves the commit to the caller.
    """
    if not is_feedback_enabled():
        return False
    allowed, _reason = _check_circuit_breaker()
    if not allowed:
        return False

    text, blocks = format_feedback(notes, user, ctx)
    return send_slack_message(
        text=text,
        blocks=blocks,
        template_key=FEEDBACK_TEMPLATE_KEY,
        channel_id=current_app.config["SLACK_FEEDBACK_CHANNEL_ID"],
    )
