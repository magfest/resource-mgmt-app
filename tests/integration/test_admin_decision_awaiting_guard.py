"""Admin decisions must be server-side blocked unless it is actually the
admin's turn (state-model `awaiting` in (ADMIN, REVIEWER_GROUP)), mirroring
the AG decision path's `validate_review_transition` guard."""
from app import db
from app.models import (
    WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW, REVIEW_STATUS_NEEDS_INFO,
    WORK_ITEM_STATUS_SUBMITTED,
)


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def test_admin_decision_blocked_when_awaiting_requester(client, seed_draft_work_item):
    """awaiting == REQUESTER (admin already sent this line back for a
    kickback) — a hand-crafted admin-approve POST must not flip the line to
    APPROVED even though the admin holds the checkout lock."""
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.add(WorkLineReview(
        work_line_id=d["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id, status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        created_by_user_id=d["admin"].id))
    db.session.add(WorkLineReview(
        work_line_id=d["line"].id, stage=REVIEW_STAGE_ADMIN_FINAL,
        status=REVIEW_STATUS_NEEDS_INFO, created_by_user_id=d["admin"].id))
    d["line"].needs_requester_action = True
    db.session.commit()

    _login(client, "test:admin")
    checkout_resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/checkout",
        follow_redirects=True,
    )
    assert checkout_resp.status_code == 200

    client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/admin-approve",
        data={"approved_amount": "50.00"}, follow_redirects=True,
    )
    db.session.refresh(d["line"])
    assert d["line"].status != "APPROVED"  # blocked — awaiting the requester, not the admin


def test_admin_decision_allowed_when_awaiting_admin(client, seed_draft_work_item):
    """awaiting == ADMIN (AG has recommended, admin has not yet decided) —
    the normal admin-approve path must still work."""
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.add(WorkLineReview(
        work_line_id=d["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id, status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        created_by_user_id=d["admin"].id))
    db.session.commit()

    _login(client, "test:admin")
    checkout_resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/checkout",
        follow_redirects=True,
    )
    assert checkout_resp.status_code == 200

    client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/admin-approve",
        data={"approved_amount": "50.00"}, follow_redirects=True,
    )
    db.session.refresh(d["line"])
    assert d["line"].status == "APPROVED"  # allowed — it is the admin's turn
