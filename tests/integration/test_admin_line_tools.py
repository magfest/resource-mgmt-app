"""
Integration tests for budget-admin line tools:
expense account correction + admin add-line on in-review requests.
"""
from datetime import datetime, timedelta

from app import db
from app.models import (
    ApprovalGroup,
    ConfidenceLevel,
    ExpenseAccount,
    FrequencyOption,
    PriorityLevel,
    WorkLineComment,
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_PENDING,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_PENDING,
)
from app.routes import UserContext


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _admin_ctx():
    return UserContext(
        user_id="test:admin", user=None, roles=("SUPER_ADMIN",),
        is_super_admin=True, approval_group_ids=set(),
    )


def _make_submitted(data, decided=False):
    """Promote seed_draft_work_item's item to SUBMITTED with an AG review,
    mirroring what dispatch_to_queue creates (dispatch/dashboard.py:263-277)."""
    data["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    data["line"].current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    review = WorkLineReview(
        work_line_id=data["line"].id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED if decided else REVIEW_STATUS_PENDING,
        created_by_user_id="test:admin",
    )
    db.session.add(review)
    if decided:
        data["line"].status = WORK_LINE_STATUS_APPROVED
        data["line"].approved_amount_cents = 5000
    db.session.commit()
    return review


def _make_target_account(data):
    """A second account (SINGLE_LOCKED to the seeded spend type) + second group,
    simulating 'the account it should have been'."""
    group2 = ApprovalGroup(
        work_type_id=data["work_type"].id,
        code="HOTEL", name="Hotel Team", is_active=True,
    )
    db.session.add(group2)
    db.session.flush()
    acct2 = ExpenseAccount(
        code="TEST_ACC_2", name="Correct Account", is_active=True,
        spend_type_mode=SPEND_TYPE_MODE_SINGLE_LOCKED,
        default_spend_type_id=data["spend_type"].id,
        approval_group_id=group2.id,
    )
    db.session.add(acct2)
    db.session.commit()
    return acct2, group2


def _checkout(data):
    """Simulate an active reviewer checkout."""
    item = data["work_item"]
    item.checked_out_by_user_id = "test:reviewer"
    item.checked_out_at = datetime.utcnow()
    item.checked_out_expires_at = datetime.utcnow() + timedelta(hours=1)
    db.session.commit()


class TestChangeLineExpenseAccount:
    def test_change_resets_line_and_reroutes(self, app, client, seed_draft_work_item):
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data, decided=True)
        acct2, group2 = _make_target_account(data)

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="Picked the wrong account originally",
            user_ctx=_admin_ctx(),
        )
        db.session.commit()

        assert ok, err
        assert data["detail"].expense_account_id == acct2.id
        assert data["detail"].routed_approval_group_id == group2.id
        assert data["line"].status == WORK_LINE_STATUS_PENDING
        assert data["line"].approved_amount_cents is None
        assert data["line"].current_review_stage == REVIEW_STAGE_APPROVAL_GROUP
        ag = WorkLineReview.query.filter_by(
            work_line_id=data["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        ).one()
        assert ag.status == REVIEW_STATUS_PENDING
        assert ag.approval_group_id == group2.id
        assert ag.decided_at is None
        comment = WorkLineComment.query.filter_by(work_line_id=data["line"].id).one()
        assert "[ADMIN ACCOUNT CHANGE]" in comment.body

    def test_blocked_while_checked_out(self, app, client, seed_draft_work_item):
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        _checkout(data)

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="x", user_ctx=_admin_ctx(),
        )
        assert not ok
        assert "checked out" in err

    def test_blocked_when_not_under_review(self, app, client, seed_draft_work_item):
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        data["work_item"].status = WORK_ITEM_STATUS_FINALIZED
        db.session.commit()
        acct2, group2 = _make_target_account(data)

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="x", user_ctx=_admin_ctx(),
        )
        assert not ok
        assert "under review" in err
