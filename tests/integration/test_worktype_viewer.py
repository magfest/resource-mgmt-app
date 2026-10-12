"""A TechOps viewer reads everything TechOps and changes nothing."""
from app import db
from app.models import (
    User, UserRole, WorkLineComment, WorkLineReview, WorkType,
    ROLE_APPROVER, REVIEW_STATUS_APPROVED, REVIEW_STATUS_PENDING,
)
from app.models.constants import COMMENT_VISIBILITY_ADMIN

from tests.integration.techops_review_helpers import add_user_with_role, line_url, login
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    DETAIL_URL, REVIEW_URL, techops_review,
)

VIEWER = "WORKTYPE_VIEWER"


def test_viewer_reads_request_line_list_and_queue(client, techops_review):
    add_user_with_role("test:viewer", VIEWER)
    db.session.add(WorkLineComment(
        work_line_id=techops_review["line"].id, visibility=COMMENT_VISIBILITY_ADMIN,
        body="internal only", created_by_user_id="test:reviewer"))
    db.session.commit()
    login(client, "test:viewer")

    line_resp = client.get(REVIEW_URL)

    assert client.get(DETAIL_URL).status_code == 200
    assert line_resp.status_code == 200
    assert "internal only" not in line_resp.get_data(as_text=True)
    assert client.get("/admin/techops/requests/").status_code == 200
    assert client.get("/approvals/TECHOPS_GEN").status_code == 200


def test_viewer_cannot_decide_reopen_or_comment(client, techops_review):
    add_user_with_role("test:viewer", VIEWER)
    line = techops_review["line"]
    login(client, "test:viewer")

    client.post(line_url(1, "approve"))
    client.post(line_url(1, "comment"), data={"comment": "hi"})

    assert WorkLineReview.query.filter_by(work_line_id=line.id).one().status == REVIEW_STATUS_PENDING
    assert WorkLineComment.query.filter_by(work_line_id=line.id).count() == 0


def test_viewer_who_is_also_an_approver_still_decides(client, techops_review):
    add_user_with_role("test:viewer", VIEWER)
    db.session.add(UserRole(user_id="test:viewer", role_code=ROLE_APPROVER,
                            approval_group_id=techops_review["group"].id))
    db.session.commit()
    login(client, "test:viewer")

    client.post(line_url(1, "approve"))

    review = WorkLineReview.query.filter_by(work_line_id=techops_review["line"].id).one()
    assert review.status == REVIEW_STATUS_APPROVED


def test_user_form_offers_and_saves_the_techops_viewer_role(client, seed_workflow_data):
    techops = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(techops)
    db.session.add(User(id="u:target", email="target@magfest.org", display_name="Target"))
    db.session.commit()
    budget_id = seed_workflow_data["work_type"].id
    login(client, "test:admin")

    form_html = client.get("/admin/config/users/u:target").get_data(as_text=True)
    client.post("/admin/config/users/u:target", data={
        "email": "target@magfest.org", "display_name": "Target", "is_active": "1",
        f"role_worktype_viewer_{techops.id}": "1",
    })

    assert f'name="role_worktype_viewer_{techops.id}"' in form_html
    assert f'name="role_worktype_viewer_{budget_id}"' not in form_html
    assert UserRole.query.filter_by(user_id="u:target", role_code=VIEWER,
                                    work_type_id=techops.id).count() == 1
