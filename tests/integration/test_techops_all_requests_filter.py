"""The All TechOps Requests status filter offers only statuses a request can hold.

NEEDS_INFO is a line status. No code writes it to work_item.status, so
filtering requests by it always returned an empty page.
"""
from app import db
from app.models import ROLE_WORKTYPE_ADMIN, User, UserRole, WorkType

from tests.integration.test_techops_review_affordances import (  # noqa: F401
    techops_review,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_status_filter_does_not_offer_needs_info(client, techops_review):
    db.session.add(User(id="test:techopsadmin", email="techopsadmin@test.local",
                        display_name="TechOps Lead", is_active=True))
    db.session.flush()
    db.session.add(UserRole(
        user_id="test:techopsadmin", role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=WorkType.query.filter_by(code="TECHOPS").one().id))
    db.session.commit()
    _login(client, "test:techopsadmin")

    html = client.get("/admin/techops/requests/").get_data(as_text=True)

    assert 'value="SUBMITTED"' in html
    assert 'value="NEEDS_INFO"' not in html
