"""A work-type admin may comment on any line of their own work type.

Admins can already open every line of their work type (line_review's
is_admin); the comment route and forms lagged behind and accepted only
members of the line's routed group.
"""
from app import db
from app.models import ROLE_WORKTYPE_ADMIN, User, UserRole, WorkLineComment, WorkType
from app.models.constants import COMMENT_VISIBILITY_ADMIN

from tests.integration.test_reviewer_admin_notes import _seed_review_for_reviewer
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    REVIEW_URL as TECHOPS_REVIEW_URL,
    techops_review,
)

BUDGET_ITEM = "TST2026-TESTDEPT-BUD-1"
BUDGET_LINE_URL = f"/TST2026/TESTDEPT/budget/item/{BUDGET_ITEM}/line/1"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _add_admin(user_id, work_type_code):
    """A WORKTYPE_ADMIN with no approval-group membership."""
    db.session.add(User(id=user_id, email=f"{user_id.split(':')[1]}@test.local",
                        display_name=user_id, is_active=True))
    db.session.flush()
    work_type = WorkType.query.filter_by(code=work_type_code).one()
    db.session.add(UserRole(user_id=user_id, role_code=ROLE_WORKTYPE_ADMIN,
                            work_type_id=work_type.id))
    db.session.commit()


def test_budget_admin_outside_the_group_can_post_admin_only_comment(client, seed_workflow_data):
    _item, line = _seed_review_for_reviewer(seed_workflow_data)
    _add_admin("test:budgetadmin", "BUDGET")
    _login(client, "test:budgetadmin")

    client.post(f"{BUDGET_LINE_URL}/comment",
                data={"comment": "check the quote", "admin_only": "1"})

    comment = WorkLineComment.query.filter_by(work_line_id=line.id).one()
    assert comment.visibility == COMMENT_VISIBILITY_ADMIN


def test_budget_admin_sees_the_comment_form(client, seed_workflow_data):
    _seed_review_for_reviewer(seed_workflow_data)
    _add_admin("test:budgetadmin", "BUDGET")
    _login(client, "test:budgetadmin")

    html = client.get(f"{BUDGET_LINE_URL}/review").get_data(as_text=True)

    assert f"{BUDGET_LINE_URL}/comment" in html


def test_admin_of_another_work_type_cannot_comment(client, seed_workflow_data):
    _item, line = _seed_review_for_reviewer(seed_workflow_data)
    db.session.add(WorkType(code="TECHOPS", name="TechOps", is_active=True))
    db.session.commit()
    _add_admin("test:techopsadmin", "TECHOPS")
    _login(client, "test:techopsadmin")

    client.post(f"{BUDGET_LINE_URL}/comment", data={"comment": "not mine"})

    assert WorkLineComment.query.filter_by(work_line_id=line.id).count() == 0


def test_techops_admin_sees_comment_form_with_admin_only_box(client, techops_review):
    _add_admin("test:techopsadmin", "TECHOPS")
    _login(client, "test:techopsadmin")

    html = client.get(TECHOPS_REVIEW_URL).get_data(as_text=True)

    assert 'name="comment"' in html
    assert 'name="admin_only"' in html


def test_techops_reviewer_sees_admin_only_box(client, techops_review):
    # The route already honours admin_only from a reviewer; the box was
    # shown only to super admins.
    _login(client, "test:reviewer")

    html = client.get(TECHOPS_REVIEW_URL).get_data(as_text=True)

    assert 'name="admin_only"' in html
