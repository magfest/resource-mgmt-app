"""A decided TechOps line can be reopened by anyone who could have decided it."""
from app import db
from app.models import (
    WorkItem, WorkItemAuditEvent, WorkLineComment, WorkLineReview,
    AUDIT_EVENT_UNFINALIZE, REVIEW_STATUS_APPROVED, REVIEW_STATUS_PENDING,
    WORK_ITEM_STATUS_FINALIZED, WORK_ITEM_STATUS_SUBMITTED,
)
from app.models.constants import COMMENT_VISIBILITY_PUBLIC

from tests.integration.techops_review_helpers import grant_department_access, line_url, login
from tests.integration.test_reviewer_admin_notes import _seed_review_for_reviewer
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    REVIEW_URL, techops_review,
)


def _approve_line_1(client):
    login(client, "test:reviewer")
    client.post(line_url(1, "approve"))


def test_reopen_unfinalizes_the_request(client, techops_review):
    _approve_line_1(client)
    item = WorkItem.query.get(techops_review["item"].id)
    assert item.status == WORK_ITEM_STATUS_FINALIZED

    client.post(line_url(1, "reopen"), data={"note": "Wrong room"})

    db.session.expire_all()
    item = WorkItem.query.get(techops_review["item"].id)
    review = WorkLineReview.query.filter_by(work_line_id=techops_review["line"].id).one()
    audit = WorkItemAuditEvent.query.filter_by(
        work_item_id=item.id, event_type=AUDIT_EVENT_UNFINALIZE).one()
    comment = WorkLineComment.query.filter_by(work_line_id=techops_review["line"].id).one()
    assert review.status == REVIEW_STATUS_PENDING
    assert item.status == WORK_ITEM_STATUS_SUBMITTED and item.finalized_at is None
    assert audit.snapshot == {"trigger": "line_reopened", "line_number": 1}
    assert comment.body == "[REOPENED] Wrong room"
    assert comment.visibility == COMMENT_VISIBILITY_PUBLIC


def test_reopen_needs_a_note(client, techops_review):
    _approve_line_1(client)

    client.post(line_url(1, "reopen"), data={"note": "  "})

    review = WorkLineReview.query.filter_by(work_line_id=techops_review["line"].id).one()
    assert review.status == REVIEW_STATUS_APPROVED


def test_requester_sees_no_reopen_and_cannot_post_it(client, techops_review):
    _approve_line_1(client)
    grant_department_access("test:author", techops_review["item"])
    login(client, "test:author")

    resp = client.get(REVIEW_URL)
    html = resp.get_data(as_text=True)
    client.post(line_url(1, "reopen"), data={"note": "Please change it"})

    review = WorkLineReview.query.filter_by(work_line_id=techops_review["line"].id).one()
    assert resp.status_code == 200  # the page loads, so the absence below means something
    assert "/line/1/reopen" not in html
    assert review.status == REVIEW_STATUS_APPROVED


def test_reopen_is_refused_on_budget(client, seed_workflow_data):
    item, line = _seed_review_for_reviewer(seed_workflow_data)
    review = WorkLineReview.query.filter_by(work_line_id=line.id).one()
    review.status = REVIEW_STATUS_APPROVED
    db.session.commit()
    login(client, "test:reviewer")

    client.post(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/reopen",
                data={"note": "try"})

    assert WorkLineReview.query.get(review.id).status == REVIEW_STATUS_APPROVED
