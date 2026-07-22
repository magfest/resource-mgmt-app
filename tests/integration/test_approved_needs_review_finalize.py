"""Feature 2: finalize resolves an unresolved concern line to APPROVED."""
from app import db
from app.models import (
    WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP,
    WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED,
    WORK_LINE_STATUS_APPROVED, WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_finalize_resolves_flagged_line_to_recommended(app, client, seed_draft_work_item):
    data = seed_draft_work_item
    item = data["work_item"]
    line = data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        approved_amount_cents=4000,  # reviewer recommended $40
        created_by_user_id=data["admin"].id))
    db.session.commit()

    _login(client, "test:admin")
    resp = client.post(
        f"/admin/final-review/finalize/{item.id}",
        data={"note": "ok"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(line)
    db.session.refresh(item)

    assert line.status == WORK_LINE_STATUS_APPROVED
    assert line.approved_amount_cents == 4000
    assert item.status == WORK_ITEM_STATUS_FINALIZED
