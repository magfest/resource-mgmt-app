"""TechOps has no review sessions, and a second decision on a line is refused
with a message a reviewer can act on."""
from app.models import WorkItem, WorkLineReview, REVIEW_STATUS_APPROVED

from tests.integration.techops_review_helpers import line_url, login
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    CHECKOUT_URL, _hold_lock, techops_review,
)


def test_checkout_is_refused_on_a_techops_request(client, techops_review):
    login(client, "test:reviewer")

    client.post(CHECKOUT_URL)

    assert WorkItem.query.get(techops_review["item"].id).checked_out_by_user_id is None


def test_second_decision_says_the_line_was_already_decided(client, techops_review):
    login(client, "test:reviewer")
    client.post(line_url(1, "approve"), data={"ajax": "1"})
    login(client, "test:other")

    data = client.post(line_url(1, "reject"),
                       data={"ajax": "1", "note": "too late"}).get_json()

    assert data["success"] is False
    assert data["error"] == "This line was already decided."
    assert data["current_status"] == "APPROVED"


def test_a_lock_left_from_before_deploy_does_not_block(client, techops_review):
    _hold_lock(techops_review["item"], "test:other")
    login(client, "test:reviewer")

    client.post(line_url(1, "approve"))

    review = WorkLineReview.query.filter_by(work_line_id=techops_review["line"].id).one()
    assert review.status == REVIEW_STATUS_APPROVED
