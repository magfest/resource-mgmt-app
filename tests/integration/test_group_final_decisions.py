"""On group-final work types the routed group, or a work-type admin, decides
without a review session. BUDGET still requires the checkout."""
from app.models import (
    ROLE_WORKTYPE_ADMIN, WorkLineReview,
    REVIEW_STATUS_APPROVED, REVIEW_STATUS_PENDING,
)

from tests.integration.techops_review_helpers import add_user_with_role, line_url, login
from tests.integration.test_reviewer_admin_notes import _seed_review_for_reviewer
from tests.integration.test_techops_review_affordances import techops_review  # noqa: F401


def _review_status(line):
    return WorkLineReview.query.filter_by(work_line_id=line.id).one().status


def test_group_member_approves_without_a_review_session(client, techops_review):
    login(client, "test:reviewer")

    client.post(line_url(1, "approve"))

    assert _review_status(techops_review["line"]) == REVIEW_STATUS_APPROVED


def test_techops_admin_outside_the_group_approves(client, techops_review):
    add_user_with_role("test:techopsadmin", ROLE_WORKTYPE_ADMIN)
    login(client, "test:techopsadmin")

    client.post(line_url(1, "approve"))

    assert _review_status(techops_review["line"]) == REVIEW_STATUS_APPROVED


def test_admin_of_another_work_type_cannot_decide(client, techops_review):
    add_user_with_role("test:budgetadmin", ROLE_WORKTYPE_ADMIN, work_type_code="BUDGET")
    login(client, "test:budgetadmin")

    client.post(line_url(1, "approve"))

    assert _review_status(techops_review["line"]) == REVIEW_STATUS_PENDING


def test_budget_reviewer_still_needs_the_checkout(client, seed_workflow_data):
    item, line = _seed_review_for_reviewer(seed_workflow_data)
    login(client, "test:reviewer")

    client.post(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/approve")

    assert _review_status(line) == REVIEW_STATUS_PENDING
