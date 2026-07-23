"""
Task 16: /admin-review is removed; /review is the single line-review page,
and its Admin Final Review tab is open to work-type admins (not just
super admins) — scoped to the line's own work type.
"""
from flask import url_for

from app import db
from app.models import (
    User,
    UserRole,
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    ROLE_WORKTYPE_ADMIN,
    WORK_ITEM_STATUS_SUBMITTED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _make_submitted(data):
    """Promote seed_draft_work_item's item to SUBMITTED with a pending AG
    review, mirroring what dispatch creates."""
    data["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    data["line"].current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    review = WorkLineReview(
        work_line_id=data["line"].id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_PENDING,
        created_by_user_id="test:admin",
    )
    db.session.add(review)
    db.session.commit()
    return review


def _seed_budget_worktype_admin(data):
    """A non-super user who is only a WORKTYPE_ADMIN for the seeded BUDGET
    work type — NOT an approver/member of the line's routed approval group."""
    wt_admin = User(
        id="test:budgetadmin", email="budgetadmin@test.local",
        display_name="Budget Admin", is_active=True,
    )
    db.session.add(wt_admin)
    db.session.add(UserRole(
        user_id="test:budgetadmin",
        role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=data["work_type"].id,
    ))
    db.session.commit()
    return wt_admin


def test_worktype_admin_can_view_review_page_for_unrouted_line(app, client, seed_draft_work_item):
    """A non-super budget WORKTYPE_ADMIN can GET /review for a line that is
    NOT routed to their approval group (they aren't an approver at all —
    the fixture's only approval group is TECH), and sees the admin tab."""
    data = seed_draft_work_item
    _make_submitted(data)
    _seed_budget_worktype_admin(data)

    _login(client, "test:budgetadmin")

    with app.test_request_context():
        url = url_for(
            "approvals.line_review",
            event=data["cycle"].code,
            dept=data["department"].code,
            public_id=data["work_item"].public_id,
            line_num=data["line"].line_number,
        )

    resp = client.get(url)
    assert resp.status_code == 200
    assert b"Admin Final Review" in resp.data


def test_admin_review_route_removed(app, client, seed_draft_work_item):
    """The old /admin-review page no longer exists."""
    data = seed_draft_work_item
    _make_submitted(data)
    _login(client, "test:admin")

    url = (
        f"/{data['cycle'].code}/{data['department'].code}/budget/item/"
        f"{data['work_item'].public_id}/line/{data['line'].line_number}/admin-review"
    )
    resp = client.get(url)
    assert resp.status_code == 404
