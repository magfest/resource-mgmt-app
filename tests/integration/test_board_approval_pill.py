"""Budget Administration shows a pill for the event's board-approval state.

Board approval used to be visible only via the release banner, which only
renders when budgets are held. An admin who never saw Slack had no way to
tell whether the board had signed off on the topline.
"""
from datetime import datetime

from app import db
from app.models import WorkItemAuditEvent, AUDIT_EVENT_BOARD_RELEASE


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_pill_shows_awaiting_when_board_has_not_approved(client, seed_draft_work_item):
    data = seed_draft_work_item
    assert data["cycle"].board_approved_at is None

    _login(client, "test:admin")
    resp = client.get("/admin/budget/")
    body = resp.get_data(as_text=True)

    assert "Awaiting FY Budget Approval" in body
    assert "FY Budget Approved" not in body


def test_pill_shows_approved_with_note_in_tooltip(client, seed_draft_work_item):
    data = seed_draft_work_item
    cycle = data["cycle"]
    cycle.board_approved_at = datetime(2026, 8, 1, 12, 0)
    cycle.board_approved_by_user_id = data["admin"].id
    db.session.add(WorkItemAuditEvent(
        work_item_id=data["work_item"].id,
        event_type=AUDIT_EVENT_BOARD_RELEASE,
        reason="Board signed off at the July retreat.",
        created_by_user_id=data["admin"].id,
    ))
    db.session.commit()

    _login(client, "test:admin")
    resp = client.get("/admin/budget/")
    body = resp.get_data(as_text=True)

    assert "FY Budget Approved" in body
    assert "Board signed off at the July retreat." in body
    assert "Test Admin" in body
    assert "Aug 01, 2026" in body


def test_pill_falls_back_gracefully_with_no_audit_note(client, seed_draft_work_item):
    """The latch can be set with nothing held, so no audit event exists."""
    data = seed_draft_work_item
    cycle = data["cycle"]
    cycle.board_approved_at = datetime(2026, 8, 1, 12, 0)
    cycle.board_approved_by_user_id = data["admin"].id
    db.session.commit()

    assert WorkItemAuditEvent.query.filter_by(
        event_type=AUDIT_EVENT_BOARD_RELEASE).count() == 0

    _login(client, "test:admin")
    resp = client.get("/admin/budget/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert "FY Budget Approved" in body
    assert 'title=""' not in body
    assert "Note: None" not in body
