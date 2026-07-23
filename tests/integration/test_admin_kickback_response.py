"""Task 4: requester response routes to whichever review stage kicked the
line back — not always the APPROVAL_GROUP review."""
from app import db
from app.models import (
    WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW, REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_PENDING,
    WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_NEEDS_INFO,
)


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def test_requester_can_respond_to_admin_kickback(client, seed_draft_work_item):
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    d["line"].status = WORK_LINE_STATUS_NEEDS_INFO
    d["line"].needs_requester_action = True
    ag_r = WorkLineReview(work_line_id=d["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id, status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        created_by_user_id=d["admin"].id)
    db.session.add(ag_r)
    admin_r = WorkLineReview(work_line_id=d["line"].id, stage=REVIEW_STAGE_ADMIN_FINAL,
        approval_group_id=None, status=REVIEW_STATUS_NEEDS_INFO, created_by_user_id=d["admin"].id)
    db.session.add(admin_r); db.session.commit()
    # requester = the item creator (test:admin here); post a response
    _login(client, "test:admin")
    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/respond",
        data={"response": "here is the info"}, follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(admin_r)
    assert admin_r.status == REVIEW_STATUS_PENDING  # admin review reopened, not the AG review
    db.session.refresh(ag_r)
    assert ag_r.status == REVIEW_STATUS_APPROVED_NEEDS_REVIEW  # untouched by the admin-kickback response
