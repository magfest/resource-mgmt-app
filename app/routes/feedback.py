"""Feedback button endpoint: one POST that forwards the user's notes to Slack."""
from __future__ import annotations

import logging

from flask import Blueprint, abort, current_app, jsonify, request, session

from app import db
from app.models import User
from app.routes import h
from app.services.feedback import (
    FEEDBACK_MAX_CHARS,
    FeedbackContext,
    is_feedback_enabled,
    normalize_notes,
    send_feedback,
)

feedback_bp = Blueprint("feedback", __name__)
logger = logging.getLogger(__name__)


@feedback_bp.app_context_processor
def inject_feedback_enabled():
    return {"feedback_enabled": is_feedback_enabled()}


def _field(name: str, limit: int = 200) -> str:
    return (request.form.get(name) or "").strip()[:limit]


@feedback_bp.post("/feedback")
def submit_feedback():
    """Forward feedback to Slack and answer with JSON for the nav modal's fetch."""
    # Identity comes from the session. The form's context fields are for
    # display in Slack only and are never used to decide anything.
    on_screen = h.get_active_user()
    if on_screen is None:
        abort(403)

    # A super admin using "view as" is signed in as someone else. Credit the
    # feedback to the admin so the team does not reply to the wrong person.
    sender, viewing_as = on_screen, ""
    real_user_id = session.get("real_user_id")
    if real_user_id and real_user_id != on_screen.id:
        real_user = db.session.get(User, real_user_id)
        if real_user is not None:
            sender, viewing_as = real_user, on_screen.display_name

    role_override = ""
    if current_app.config.get("BETA_TESTING_MODE"):
        role_override = session.get("role_override") or ""

    if not is_feedback_enabled():
        return jsonify(ok=False, error="Feedback is not set up on this site."), 503

    notes = normalize_notes(request.form.get("notes"))
    if not notes:
        return jsonify(ok=False, error="Please write something first."), 400
    if len(notes) > FEEDBACK_MAX_CHARS:
        return jsonify(ok=False, error=f"Please keep it under {FEEDBACK_MAX_CHARS} characters."), 400

    ctx = FeedbackContext(
        page_url=_field("page_url", 2000),
        page_title=_field("page_title"),
        page_messages=_field("page_messages", 1000),
        event=_field("event"),
        dept=_field("dept"),
        public_id=_field("public_id"),
        viewport=_field("viewport", 40),
        user_agent=(request.headers.get("User-Agent") or "")[:300],
        host=request.host,
        viewing_as=viewing_as,
        role_override=role_override,
    )

    try:
        sent = send_feedback(notes, sender, ctx)
        db.session.commit()
    except Exception:
        logger.exception("Feedback post to Slack failed")
        db.session.rollback()
        sent = False

    if not sent:
        return jsonify(ok=False, error="Couldn't send; please try again."), 502
    return jsonify(ok=True)
