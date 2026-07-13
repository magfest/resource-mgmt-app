"""Force check-in authority: worktype admin of the item's work type."""
from datetime import datetime, timedelta

from app import db
from app.models import (
    User, UserRole, WorkItem,
    ROLE_WORKTYPE_ADMIN, ROLE_APPROVER,
    WORK_ITEM_STATUS_SUBMITTED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _checkout_to(data, holder_id):
    """Put the seeded item in SUBMITTED with an active checkout held by holder_id."""
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    item.checked_out_by_user_id = holder_id
    item.checked_out_at = datetime.utcnow()
    item.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()
    return item


CHECKIN_URL = "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/checkin"


def test_worktype_admin_can_force_release(client, seed_draft_work_item):
    data = seed_draft_work_item
    wt_admin = User(id="test:wtadmin", email="wtadmin@test.local",
                    display_name="WT Admin", is_active=True)
    db.session.add(wt_admin)
    db.session.add(UserRole(user_id="test:wtadmin",
                            role_code=ROLE_WORKTYPE_ADMIN,
                            work_type_id=data["work_type"].id))
    _checkout_to(data, data["reviewer"].id)

    _login(client, "test:wtadmin")
    resp = client.post(CHECKIN_URL, follow_redirects=False)
    assert resp.status_code == 302

    item = db.session.get(WorkItem, data["work_item"].id)
    assert item.checked_out_by_user_id is None


def test_non_holder_approver_cannot_force_release(client, seed_draft_work_item):
    data = seed_draft_work_item
    other = User(id="test:other", email="other@test.local",
                 display_name="Other Approver", is_active=True)
    db.session.add(other)
    db.session.add(UserRole(user_id="test:other",
                            role_code=ROLE_APPROVER,
                            approval_group_id=data["approval_group"].id))
    _checkout_to(data, data["reviewer"].id)

    _login(client, "test:other")
    resp = client.post(CHECKIN_URL, follow_redirects=False)
    assert resp.status_code == 302

    item = db.session.get(WorkItem, data["work_item"].id)
    assert item.checked_out_by_user_id == data["reviewer"].id
