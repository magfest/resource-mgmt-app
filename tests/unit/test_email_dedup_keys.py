"""Dedup key shapes. Keys are bucketed by kind, not by template key."""
from datetime import datetime

from app.models import EmailTemplate
from app import db
from app.services.email_enqueue import build_dedup_key, resolve_template_key


def test_reminder_key_includes_event_cycle():
    """Department rows are global and shared across events. Without the event
    in the key, two same-day reminder runs collide and the second is dropped."""
    now = datetime(2026, 8, 23, 14, 0, 0)
    a = build_dedup_key("submission_reminder", event_cycle_id=1, department_id=7,
                        recipient_email="x@example.org", now=now)
    b = build_dedup_key("submission_reminder", event_cycle_id=2, department_id=7,
                        recipient_email="x@example.org", now=now)
    assert a != b


def test_finalized_key_is_stable_within_one_release():
    """A same-release re-run of the CLI must still collide."""
    released = datetime(2026, 8, 20, 12, 0, 0)
    now = datetime(2026, 8, 23, 14, 0, 0)
    later = datetime(2026, 8, 24, 9, 0, 0)
    a = build_dedup_key("finalized", work_item_id=3, recipient_email="x@example.org",
                        now=now, event_stamp=released)
    b = build_dedup_key("finalized", work_item_id=3, recipient_email="x@example.org",
                        now=later, event_stamp=released)
    assert a == b


def test_resubmission_gets_a_new_confirmation_key():
    """Recall-and-resubmit is a second submission, not a duplicate click."""
    first = datetime(2026, 8, 23, 10, 0, 0)
    second = datetime(2026, 8, 23, 15, 0, 0)
    a = build_dedup_key("submission_confirmation", work_item_id=3,
                        recipient_email="x@example.org", event_stamp=first)
    b = build_dedup_key("submission_confirmation", work_item_id=3,
                        recipient_email="x@example.org", event_stamp=second)
    assert a != b


def test_rerelease_gets_a_new_finalized_key():
    """Unfinalize clears board_released_at so a re-finalize notifies again."""
    first = datetime(2026, 8, 23, 10, 0, 0)
    second = datetime(2026, 9, 6, 10, 0, 0)
    a = build_dedup_key("finalized", work_item_id=3,
                        recipient_email="x@example.org", event_stamp=first)
    b = build_dedup_key("finalized", work_item_id=3,
                        recipient_email="x@example.org", event_stamp=second)
    assert a != b


def test_resolver_falls_back_to_generic_key(app):
    with app.app_context():
        db.session.add(EmailTemplate(
            template_key="submitted", name="Submitted", subject="s", body_text="b",
        ))
        db.session.commit()
        assert resolve_template_key("submitted", "SUPPLY") == "submitted"
        db.session.add(EmailTemplate(
            template_key="supply_submitted", name="Supply Submitted", subject="s", body_text="b",
        ))
        db.session.commit()
        assert resolve_template_key("submitted", "SUPPLY") == "supply_submitted"
