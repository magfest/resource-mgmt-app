"""Feature 2: reviewer applies APPROVED_NEEDS_REVIEW."""
from app import db
from app.models import (
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    WORK_ITEM_STATUS_SUBMITTED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _prep_submitted(data):
    """Move the seeded item to SUBMITTED with a pending AG review."""
    item = data["work_item"]
    line = data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_PENDING, created_by_user_id=data["admin"].id)
    db.session.add(review)
    db.session.commit()
    return item, line, review


def test_approve_needs_review_sets_status_and_amount(client, seed_draft_work_item):
    data = seed_draft_work_item
    item, line, review = _prep_submitted(data)
    _login(client, "test:admin")
    # admins are reviewers for every line, so this checkout succeeds
    client.post(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/checkout")
    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/approve-needs-review",
        data={"note": "cost ok but total may exceed cap", "recommended_amount": "40.00"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    db.session.refresh(review)
    db.session.refresh(line)
    assert review.status == "APPROVED_NEEDS_REVIEW"
    assert line.status == "APPROVED_NEEDS_REVIEW"
    assert review.approved_amount_cents == 4000  # recommended captured


def test_approve_needs_review_requires_note(client, seed_draft_work_item):
    data = seed_draft_work_item
    item, line, review = _prep_submitted(data)
    _login(client, "test:admin")
    client.post(f"/TST2026/TESTDEPT/budget/item/{item.public_id}/checkout")
    client.post(
        f"/TST2026/TESTDEPT/budget/item/{item.public_id}/line/1/approve-needs-review",
        data={"note": ""}, follow_redirects=True)
    db.session.refresh(review)
    assert review.status == "PENDING"  # rejected: note required
