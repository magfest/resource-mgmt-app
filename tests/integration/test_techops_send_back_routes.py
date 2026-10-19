"""The return-to-draft and send-back routes for TechOps orders."""
from datetime import datetime

import pytest

from app import db
from app.models import (
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    EmailOutbox,
    WorkLineReview,
    WorkItemAuditEvent,
    WorkItemComment,
    AUDIT_EVENT_RECALL_TO_DRAFT,
    REVIEW_STATUS_APPROVED,
    ROLE_WORKTYPE_ADMIN,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes import UserContext
from app.routes.work.helpers.lifecycle import submit_work_item
from migrations.versions.sb7q2m4x9k1d_techops_sent_back_email import (
    seed_sent_back_template,
)
from tests.integration.techops_review_helpers import (
    add_user_with_role,
    grant_department_access,
    login,
    set_review_status,
)
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    DETAIL_URL,
    techops_review,
)

PULL_BACK_URL = f"{DETAIL_URL}/return-to-draft"
SEND_BACK_URL = f"{DETAIL_URL}/send-back"

NOT_ALLOWED = ("This request can no longer be returned to draft. "
               "TechOps may have started reviewing it.")
RETURNED_TO_DRAFT = ("This request is back with the department as a draft. "
                     "Reload the page to see where it stands.")
BLANK_REASON = "Say why you are sending this back. The department sees your reason."


@pytest.fixture
def send_back_setup(techops_review):
    seed_sent_back_template(db.session.connection())
    db.session.commit()
    grant_department_access("test:author", techops_review["item"])
    add_user_with_role("test:admin", ROLE_WORKTYPE_ADMIN)
    return techops_review


def _ctx(user_id):
    return UserContext(
        user_id=user_id, user=None, roles=(),
        is_super_admin=False, approval_group_ids=set(),
    )


def _flashes(client):
    with client.session_transaction() as sess:
        return [message for _, message in sess.pop("_flashes", [])]


def _status(item):
    db.session.refresh(item)
    return item.status


def test_pull_back_returns_to_draft(client, send_back_setup):
    item = send_back_setup["item"]
    login(client, "test:author")
    response = client.post(PULL_BACK_URL)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(DETAIL_URL)
    assert _status(item) == WORK_ITEM_STATUS_DRAFT
    assert _flashes(client) == ["Request returned to draft. Submit it again when it is ready."]
    assert WorkItemComment.query.count() == 0
    assert EmailOutbox.query.count() == 0


def test_pull_back_refused_after_decision_lands(client, send_back_setup):
    item = send_back_setup["item"]
    set_review_status(send_back_setup["line"], REVIEW_STATUS_APPROVED)
    login(client, "test:author")
    client.post(PULL_BACK_URL)
    assert _status(item) == WORK_ITEM_STATUS_SUBMITTED
    assert _flashes(client) == [NOT_ALLOWED]


def test_send_back_resets_comments_and_emails(client, send_back_setup):
    item = send_back_setup["item"]
    login(client, "test:admin")
    response = client.post(SEND_BACK_URL, data={"reason": "Wrong rooms"})
    assert response.status_code == 302
    assert _status(item) == WORK_ITEM_STATUS_DRAFT
    comment = WorkItemComment.query.one()
    assert (comment.body, comment.visibility, comment.created_by_user_id) == (
        "Wrong rooms", "PUBLIC", "test:admin")
    assert EmailOutbox.query.filter_by(template_key="techops_sent_back").count() == 1
    event = WorkItemAuditEvent.query.filter_by(
        event_type=AUDIT_EVENT_RECALL_TO_DRAFT).one()
    assert event.snapshot["action"] == "SEND_BACK"
    assert _flashes(client) == [
        "Request sent back to the department. They have been emailed your reason."]


def test_send_back_blank_reason_refused(client, send_back_setup):
    item = send_back_setup["item"]
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": " \r\n "})
    assert _status(item) == WORK_ITEM_STATUS_SUBMITTED
    assert _flashes(client) == [BLANK_REASON]


def test_send_back_reason_crlf_normalized(client, send_back_setup):
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": "Line one\r\nLine two\r\n"})
    assert WorkItemComment.query.one().body == "Line one\nLine two"


def test_send_back_twice_is_refused(client, send_back_setup):
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": "Wrong rooms"})
    _flashes(client)
    client.post(SEND_BACK_URL, data={"reason": "Wrong rooms"})
    assert WorkItemComment.query.count() == 1
    assert WorkItemAuditEvent.query.filter_by(
        event_type=AUDIT_EVENT_RECALL_TO_DRAFT).count() == 1


def test_editor_cannot_send_back(client, send_back_setup):
    item = send_back_setup["item"]
    login(client, "test:author")
    client.post(SEND_BACK_URL, data={"reason": "Mine"})
    assert _status(item) == WORK_ITEM_STATUS_SUBMITTED
    assert _flashes(client) == [NOT_ALLOWED]


def test_approver_cannot_pull_back(client, send_back_setup):
    item = send_back_setup["item"]
    login(client, "test:reviewer")
    client.post(PULL_BACK_URL)
    assert _status(item) == WORK_ITEM_STATUS_SUBMITTED
    assert _flashes(client) == [NOT_ALLOWED]


def test_stale_reviewer_cannot_decide_a_returned_draft(client, send_back_setup):
    item = send_back_setup["item"]
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": "Wrong rooms"})
    _flashes(client)

    login(client, "test:reviewer")
    client.post(f"{DETAIL_URL}/line/1/approve")
    assert _status(item) == WORK_ITEM_STATUS_DRAFT
    assert WorkLineReview.query.count() == 0
    assert _flashes(client) == [RETURNED_TO_DRAFT]


def test_stale_reopen_on_a_returned_draft_is_refused(client, send_back_setup):
    item = send_back_setup["item"]
    set_review_status(send_back_setup["line"], REVIEW_STATUS_APPROVED)
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": "Wrong rooms"})
    _flashes(client)

    response = client.post(f"{DETAIL_URL}/line/1/reopen", data={"note": "Reopen"})
    assert response.status_code == 302
    assert _status(item) == WORK_ITEM_STATUS_DRAFT


def test_second_send_back_within_the_hour_emails_again(client, send_back_setup):
    item = send_back_setup["item"]
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": "First reason"})
    submit_work_item(item, _ctx("test:author"))
    db.session.commit()
    client.post(SEND_BACK_URL, data={"reason": "Second reason"})
    assert EmailOutbox.query.filter_by(template_key="techops_sent_back").count() == 2


def test_send_back_with_no_recipients_says_so(client, send_back_setup):
    DepartmentMembershipWorkTypeAccess.query.delete()
    DepartmentMembership.query.delete()
    db.session.commit()
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": "Wrong rooms"})
    assert _flashes(client) == [
        "Request sent back to the department. No email address was found for "
        "them, so tell them in #super-techops-requests."]


def test_send_back_of_finalized_clears_the_finalize_stamp(client, send_back_setup):
    item = send_back_setup["item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.finalized_at = datetime.utcnow()
    item.finalized_by_user_id = "test:reviewer"
    db.session.commit()
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": "Rooms moved"})
    db.session.refresh(item)
    assert item.finalized_at is None and item.finalized_by_user_id is None
