"""Finalization must resolve lines the approval group already decided.

An AG decision writes WorkLine.status but leaves approved_amount_cents null and
the line at APPROVAL_GROUP stage. Before this fix, finalize skipped those lines
and they contributed zero to the item total.
"""
from app import db
from app.models import (
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED,
    WORK_LINE_STATUS_APPROVED, WORK_LINE_STATUS_REJECTED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _finalize(client, item_id):
    return client.post(
        f"/admin/final-review/finalize/{item_id}",
        data={"note": "ok"},
        follow_redirects=True,
    )


def test_finalize_carries_forward_ag_approved_amount(app, client, seed_draft_work_item):
    """An AG approval with an override amount keeps that amount at admin final."""
    data = seed_draft_work_item
    item, line = data["work_item"], data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_APPROVED
    line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED,
        approved_amount_cents=3000,
        created_by_user_id=data["admin"].id))
    db.session.commit()

    _login(client, "test:admin")
    assert _finalize(client, item.id).status_code == 200

    db.session.refresh(line)
    db.session.refresh(item)
    assert line.status == WORK_LINE_STATUS_APPROVED
    assert line.approved_amount_cents == 3000
    assert line.current_review_stage == REVIEW_STAGE_ADMIN_FINAL
    assert item.status == WORK_ITEM_STATUS_FINALIZED


def test_finalize_falls_back_to_requested_for_ag_approved(app, client, seed_draft_work_item):
    """An AG approval with no amount falls back to the requested amount."""
    data = seed_draft_work_item
    item, line = data["work_item"], data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_APPROVED
    line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED,
        approved_amount_cents=None,
        created_by_user_id=data["admin"].id))
    db.session.commit()

    _login(client, "test:admin")
    assert _finalize(client, item.id).status_code == 200

    db.session.refresh(line)
    # Fixture line: unit_price_cents=5000, quantity=1 -> requested = 5000
    assert line.approved_amount_cents == 5000
    assert line.current_review_stage == REVIEW_STAGE_ADMIN_FINAL


def test_finalize_stores_zero_for_ag_rejected(app, client, seed_draft_work_item):
    """A rejected line stores 0, not null.

    The line table prints $0.00 for REJECTED from a template branch, which hid
    the null. Anything reading the column instead of rendering saw a hole.
    """
    data = seed_draft_work_item
    item, line = data["work_item"], data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_REJECTED
    line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_REJECTED,
        created_by_user_id=data["admin"].id))
    db.session.commit()

    _login(client, "test:admin")
    assert _finalize(client, item.id).status_code == 200

    db.session.refresh(line)
    assert line.status == WORK_LINE_STATUS_REJECTED
    assert line.approved_amount_cents == 0
    assert line.current_review_stage == REVIEW_STAGE_ADMIN_FINAL
