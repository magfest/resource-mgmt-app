"""The request-level Request Information kickback is gone.

It set work_item.status to NEEDS_INFO and no template ever posted to the
matching response route, so items entering that state could not be checked
out again. Line-level needs-info is unaffected.
"""
from datetime import datetime, timedelta

from app import db
from app.models import (
    UserRole,
    ROLE_APPROVER,
    WORK_ITEM_STATUS_SUBMITTED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


ITEM_URL = "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1"
REQUEST_INFO_URL = f"{ITEM_URL}/request-info"
RESPOND_INFO_URL = f"{ITEM_URL}/respond-info"


def test_request_info_route_is_gone(client, seed_draft_work_item):
    _login(client, "test:admin")
    resp = client.post(REQUEST_INFO_URL, data={"message": "need details"})
    assert resp.status_code == 404


def test_respond_info_route_is_gone(client, seed_draft_work_item):
    _login(client, "test:admin")
    resp = client.post(RESPOND_INFO_URL, data={"response": "here you go"})
    assert resp.status_code == 404


def test_detail_page_offers_no_request_information_form(client, seed_draft_work_item):
    """A reviewer holding the checkout used to see the form. Now nobody does."""
    data = seed_draft_work_item
    db.session.add(UserRole(user_id=data["reviewer"].id,
                            role_code=ROLE_APPROVER,
                            approval_group_id=data["approval_group"].id))
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    item.checked_out_by_user_id = data["reviewer"].id
    item.checked_out_at = datetime.utcnow()
    item.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()

    _login(client, data["reviewer"].id)
    resp = client.get(ITEM_URL)

    assert resp.status_code == 200
    assert b"request-info" not in resp.data
    assert b"What information do you need?" not in resp.data
