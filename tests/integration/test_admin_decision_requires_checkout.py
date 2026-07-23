"""Admin-final decisions must require the checkout lock, same as AG decisions."""
from app import db
from app.models import (
    WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
    WORK_ITEM_STATUS_SUBMITTED,
)


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def test_admin_approve_blocked_without_checkout(client, seed_draft_work_item):
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.add(WorkLineReview(work_line_id=d["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id, status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        created_by_user_id=d["admin"].id))
    db.session.commit()
    _login(client, "test:admin")  # not checked out
    client.post(f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/admin-approve",
                data={"approved_amount": "50.00"}, follow_redirects=True)
    db.session.refresh(d["line"])
    assert d["line"].status != "APPROVED"  # blocked — no checkout


def test_admin_approve_bypasses_undecided_approval_group_when_checked_out(client, seed_draft_work_item):
    """Admins may bypass approval-group review for lines that don't need it
    (e.g. low-cost office supplies). With no AG review at all, an admin who
    holds the checkout lock can still record a final decision directly."""
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.commit()
    # No WorkLineReview created for this line — the AG never reviewed it.

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
    assert d["line"].status == "APPROVED"  # admin bypassed the AG
