"""The TechOps line page's checkout banner claims decisions only when true.

A work-type admin outside the line's routed group can hold the checkout
but cannot decide the line; the banner told them they could.
"""
from app import db
from app.models import ROLE_WORKTYPE_ADMIN, User, UserRole, WorkType

from tests.integration.test_techops_review_affordances import (  # noqa: F401
    REVIEW_URL,
    _hold_lock,
    techops_review,
)

CLAIM = "You can make review decisions on this line."


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_admin_outside_the_group_is_told_who_decides(client, techops_review):
    db.session.add(User(id="test:techopsadmin", email="techopsadmin@test.local",
                        display_name="TechOps Lead", is_active=True))
    db.session.flush()
    db.session.add(UserRole(
        user_id="test:techopsadmin", role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=WorkType.query.filter_by(code="TECHOPS").one().id))
    db.session.commit()
    _hold_lock(techops_review["item"], "test:techopsadmin")
    _login(client, "test:techopsadmin")

    html = client.get(REVIEW_URL).get_data(as_text=True)

    assert CLAIM not in html
    assert "Only TechOps Generic reviewers can decide this line." in html


def test_reviewer_in_the_group_is_still_told_they_can_decide(client, techops_review):
    _hold_lock(techops_review["item"], "test:reviewer")
    _login(client, "test:reviewer")

    html = client.get(REVIEW_URL).get_data(as_text=True)

    assert CLAIM in html
