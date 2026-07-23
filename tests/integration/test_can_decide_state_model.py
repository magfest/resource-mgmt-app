"""Task 5: `can_decide` (the AG decision-form gate) is derived from the
LineReviewState read-model (`state.awaiting == AWAITING_REVIEWER_GROUP`),
replacing the ad-hoc `review.status == PENDING and not admin_final_decided`
check.

Negative case: AG review PENDING but an ADMIN_FINAL review is already
APPROVED -> state.awaiting == DONE, not REVIEWER_GROUP -> the AG decision
form (and its "Approve this line?" button) must not render.

Positive case: only a PENDING AG review exists (no admin decision yet) ->
state.awaiting == REVIEWER_GROUP -> the AG decision form must render for a
checked-out reviewer.
"""
from app import db
from app.models import (
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_APPROVED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
)

AG_APPROVE_BUTTON = b'data-ag-action="Approve this line?"'


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_ag_decision_form_hidden_after_admin_final(client, seed_draft_work_item):
    """AG review PENDING + admin ADMIN_FINAL APPROVED -> awaiting DONE ->
    can_decide must be False, so the AG approve button is absent."""
    data = seed_draft_work_item
    item = data["work_item"]
    line = data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    ag_review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_PENDING, created_by_user_id=data["admin"].id)
    admin_review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL,
        status=REVIEW_STATUS_APPROVED, created_by_user_id=data["admin"].id)
    line.status = WORK_LINE_STATUS_APPROVED
    db.session.add_all([ag_review, admin_review])
    db.session.commit()

    # test:admin is SUPER_ADMIN -> is_reviewer_for_line() is True for every
    # line, so checkout + reviewer-permission checks pass; the only thing
    # gating the button is can_decide itself.
    _login(client, "test:admin")
    checkout_resp = client.post(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/checkout")
    assert checkout_resp.status_code in (200, 302)

    resp = client.get(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/review")
    assert resp.status_code == 200
    assert AG_APPROVE_BUTTON not in resp.data


def test_ag_decision_form_shown_when_only_ag_pending(client, seed_draft_work_item):
    """Only a PENDING AG review exists -> awaiting REVIEWER_GROUP ->
    can_decide must be True, so the AG approve button is present."""
    data = seed_draft_work_item
    item = data["work_item"]
    line = data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    ag_review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_PENDING, created_by_user_id=data["admin"].id)
    db.session.add(ag_review)
    db.session.commit()

    _login(client, "test:admin")
    checkout_resp = client.post(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/checkout")
    assert checkout_resp.status_code in (200, 302)

    resp = client.get(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/review")
    assert resp.status_code == 200
    assert AG_APPROVE_BUTTON in resp.data
