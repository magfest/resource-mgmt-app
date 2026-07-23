"""
Task 6 (C4): two explicit admin reset actions.

- "Reopen Final Decision" (admin_final.line_reset -> reset_line_for_rereview)
  resets ONLY the admin review to PENDING; the AG recommendation is untouched.
- "Return to Reviewer Group" (admin_final.line_return_to_group ->
  return_line_to_reviewer_group) resets the AG review to PENDING AND clears
  the admin decision; the line goes back to the APPROVAL_GROUP stage.
"""
from datetime import datetime

from app import db
from app.models import (
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
)


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def _seed_decided_line(d):
    """SUBMITTED item with an AG recommendation and a terminal admin decision."""
    work_item = d["work_item"]
    line = d["line"]

    work_item.status = WORK_ITEM_STATUS_SUBMITTED

    ag_review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id,
        status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        approved_amount_cents=4500,
        decided_at=datetime.utcnow(), decided_by_user_id="test:reviewer",
        created_by_user_id="test:reviewer",
    )
    db.session.add(ag_review)

    admin_review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL,
        approval_group_id=None,
        status=REVIEW_STATUS_APPROVED,
        approved_amount_cents=5000,
        decided_at=datetime.utcnow(), decided_by_user_id="test:admin",
        created_by_user_id="test:admin",
    )
    db.session.add(admin_review)

    line.status = WORK_LINE_STATUS_APPROVED
    line.approved_amount_cents = 5000
    line.current_review_stage = REVIEW_STAGE_ADMIN_FINAL
    db.session.commit()

    return ag_review, admin_review


def _checkout(client, d):
    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/checkout",
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_reopen_final_decision_resets_admin_only(client, seed_draft_work_item):
    d = seed_draft_work_item
    ag_review, admin_review = _seed_decided_line(d)

    _login(client, "test:admin")
    _checkout(client, d)

    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/admin-reset",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(ag_review)
    db.session.refresh(admin_review)
    db.session.refresh(d["line"])

    assert admin_review.status == "PENDING"
    assert admin_review.decided_at is None
    assert admin_review.decided_by_user_id is None

    # AG recommendation is untouched.
    assert ag_review.status == REVIEW_STATUS_APPROVED_NEEDS_REVIEW
    assert ag_review.approved_amount_cents == 4500


def test_return_to_reviewer_group_resets_ag_and_clears_admin(client, seed_draft_work_item):
    d = seed_draft_work_item
    ag_review, admin_review = _seed_decided_line(d)

    _login(client, "test:admin")
    _checkout(client, d)

    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/admin-return-to-group",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(ag_review)
    db.session.refresh(admin_review)
    db.session.refresh(d["line"])

    assert ag_review.status == "PENDING"
    assert ag_review.decided_at is None
    assert ag_review.decided_by_user_id is None
    assert ag_review.approved_amount_cents is None

    assert admin_review.status == "PENDING"
    assert admin_review.decided_at is None
    assert admin_review.decided_by_user_id is None

    assert d["line"].status == "PENDING"
    assert d["line"].approved_amount_cents is None
    assert d["line"].needs_requester_action is False
    assert d["line"].current_review_stage == REVIEW_STAGE_APPROVAL_GROUP


def test_return_to_reviewer_group_works_while_admin_still_deciding(client, seed_draft_work_item):
    """The AG has recommended but the admin hasn't decided yet (state.awaiting
    == ADMIN). Return to Reviewer Group must work here too, not just after a
    terminal admin decision — otherwise an admin who disagrees with a bad AG
    recommendation would have to record a real Approve/Reject first just to
    get access to the button, then undo it."""
    d = seed_draft_work_item
    work_item = d["work_item"]
    line = d["line"]

    work_item.status = WORK_ITEM_STATUS_SUBMITTED

    ag_review = WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id,
        status=REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
        approved_amount_cents=4500,
        decided_at=datetime.utcnow(), decided_by_user_id="test:reviewer",
        created_by_user_id="test:reviewer",
    )
    db.session.add(ag_review)
    # No ADMIN_FINAL review at all yet — admin hasn't acted.
    db.session.commit()

    _login(client, "test:admin")
    _checkout(client, d)

    # The Return-to-Reviewer-Group button must actually render in this state
    # (the deciding-state block, before the admin has made a decision), not
    # just in the terminal-decision block. Check this BEFORE the POST below,
    # since the POST itself resets the AG review back to PENDING.
    get_resp = client.get(
        f"/TST2026/TESTDEPT/budget/item/{work_item.public_id}/line/1/review"
    )
    assert get_resp.status_code == 200
    assert b"Return to Reviewer Group" in get_resp.data

    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{work_item.public_id}/line/1/admin-return-to-group",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(ag_review)
    db.session.refresh(line)

    assert ag_review.status == "PENDING"
    assert ag_review.decided_at is None
    assert ag_review.decided_by_user_id is None
    assert ag_review.approved_amount_cents is None

    assert line.status == "PENDING"
    assert line.approved_amount_cents is None
    assert line.current_review_stage == REVIEW_STAGE_APPROVAL_GROUP
