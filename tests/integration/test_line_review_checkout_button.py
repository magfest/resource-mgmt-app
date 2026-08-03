"""The line-review page must not offer a checkout it cannot grant.

The three "Start Reviewing" forms used to render off can_review and
can_admin_decide, neither of which knows whether checkout would succeed.
"""
from datetime import datetime, timedelta

from app import db
from app.models import (
    User, UserRole,
    ROLE_APPROVER,
    WORK_ITEM_STATUS_SUBMITTED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


LINE_REVIEW_URL = "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/line/1/review"
CHECKOUT_URL = "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/checkout"


def _submitted_and_locked_by_other(data):
    """SUBMITTED, with an active lock held by a user who is not the reviewer."""
    other = User(id="test:holder", email="holder@test.local",
                 display_name="Lock Holder", is_active=True)
    db.session.add(other)
    db.session.add(UserRole(user_id=data["reviewer"].id,
                            role_code=ROLE_APPROVER,
                            approval_group_id=data["approval_group"].id))
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    item.checked_out_by_user_id = "test:holder"
    item.checked_out_at = datetime.utcnow()
    item.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()
    return item


def test_no_checkout_form_when_another_reviewer_holds_the_lock(client, seed_draft_work_item):
    data = seed_draft_work_item
    _submitted_and_locked_by_other(data)

    _login(client, data["reviewer"].id)
    resp = client.get(LINE_REVIEW_URL)

    assert resp.status_code == 200
    assert b"/checkout" not in resp.data
    assert b"Another reviewer has this request checked out." in resp.data


def test_refusal_names_the_reason(client, seed_draft_work_item):
    """The handler discarded can_checkout()'s reason and flashed a generic line."""
    data = seed_draft_work_item
    _submitted_and_locked_by_other(data)

    _login(client, data["reviewer"].id)
    resp = client.post(CHECKOUT_URL, follow_redirects=True)

    assert resp.status_code == 200
    assert b"already checked out by another reviewer" in resp.data
