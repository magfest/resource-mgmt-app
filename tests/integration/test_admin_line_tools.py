"""
Integration tests for budget-admin line tools:
expense account correction + admin add-line on in-review requests.
"""
from datetime import datetime, timedelta

from flask import url_for

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


def _checkout(data, user_id="test:reviewer"):
    """Simulate an active checkout (by another reviewer, unless overridden)."""
    item = data["work_item"]
    item.checked_out_by_user_id = user_id
    item.checked_out_at = datetime.utcnow()
    item.checked_out_expires_at = datetime.utcnow() + timedelta(hours=1)
    db.session.commit()


def _make_line_refs():
    """Reference rows required by BudgetLineDetail (conftest's create_all
    skips Alembic data migrations, so tests seed these themselves)."""
    cl = ConfidenceLevel(code="HIGH", name="High", is_active=True)
    fq = FrequencyOption(code="ONE_TIME", name="One Time", is_active=True)
    pr = PriorityLevel(code="MUST", name="Must Have", is_active=True)
    db.session.add_all([cl, fq, pr])
    db.session.commit()
    return cl, fq, pr


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

    def test_allows_own_checkout(self, app, client, seed_draft_work_item):
        # The guard protects OTHER reviewers; the admin's own checkout
        # (Start Reviewing -> spot wrong account -> fix) must not block.
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        _checkout(data, user_id="test:admin")

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="Fixing during my own review",
            user_ctx=_admin_ctx(),
        )
        db.session.commit()
        assert ok, err
        assert data["detail"].expense_account_id == acct2.id

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


class TestAdminAddLine:
    def test_adds_routed_reviewable_line(self, app, client, seed_draft_work_item):
        from app.routes.admin_final.helpers import admin_add_line
        data = seed_draft_work_item
        _make_submitted(data)
        cl, fq, pr = _make_line_refs()

        line, err = admin_add_line(
            work_item=data["work_item"], user_ctx=_admin_ctx(),
            expense_account=data["expense_account"], spend_type=data["spend_type"],
            approval_group=data["approval_group"],
            quantity=2, unit_price_cents=12500,
            confidence_level=cl, frequency=fq, priority=pr,
            warehouse_flag=False, description="Forgotten major item",
            note="Missed during original submission",
        )
        db.session.commit()

        assert err is None
        assert line.line_number == 2
        assert line.status == WORK_LINE_STATUS_PENDING
        assert line.current_review_stage == REVIEW_STAGE_APPROVAL_GROUP
        assert line.budget_detail.routed_approval_group_id == data["approval_group"].id
        review = WorkLineReview.query.filter_by(
            work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        ).one()
        assert review.status == REVIEW_STATUS_PENDING
        assert review.approval_group_id == data["approval_group"].id
        comment = WorkLineComment.query.filter_by(work_line_id=line.id).one()
        assert "[ADMIN LINE ADDED]" in comment.body

    def test_blocked_while_checked_out(self, app, client, seed_draft_work_item):
        from app.routes.admin_final.helpers import admin_add_line
        data = seed_draft_work_item
        _make_submitted(data)
        cl, fq, pr = _make_line_refs()
        _checkout(data)

        line, err = admin_add_line(
            work_item=data["work_item"], user_ctx=_admin_ctx(),
            expense_account=data["expense_account"], spend_type=data["spend_type"],
            approval_group=data["approval_group"],
            quantity=1, unit_price_cents=100,
            confidence_level=cl, frequency=fq, priority=pr,
            warehouse_flag=False, description="", note="x",
        )
        assert line is None
        assert "checked out" in err

    def test_allows_own_checkout(self, app, client, seed_draft_work_item):
        from app.routes.admin_final.helpers import admin_add_line
        data = seed_draft_work_item
        _make_submitted(data)
        cl, fq, pr = _make_line_refs()
        _checkout(data, user_id="test:admin")

        line, err = admin_add_line(
            work_item=data["work_item"], user_ctx=_admin_ctx(),
            expense_account=data["expense_account"], spend_type=data["spend_type"],
            approval_group=data["approval_group"],
            quantity=1, unit_price_cents=100,
            confidence_level=cl, frequency=fq, priority=pr,
            warehouse_flag=False, description="", note="Adding during my own review",
        )
        db.session.commit()
        assert err is None
        assert line.line_number == 2

    def test_blocked_when_not_under_review(self, app, client, seed_draft_work_item):
        from app.routes.admin_final.helpers import admin_add_line
        data = seed_draft_work_item
        data["work_item"].status = WORK_ITEM_STATUS_FINALIZED
        db.session.commit()
        cl, fq, pr = _make_line_refs()

        line, err = admin_add_line(
            work_item=data["work_item"], user_ctx=_admin_ctx(),
            expense_account=data["expense_account"], spend_type=data["spend_type"],
            approval_group=data["approval_group"],
            quantity=1, unit_price_cents=100,
            confidence_level=cl, frequency=fq, priority=pr,
            warehouse_flag=False, description="", note="x",
        )
        assert line is None
        assert "under review" in err


def _url(app, endpoint, data, **kwargs):
    with app.test_request_context():
        return url_for(
            endpoint,
            event=data["cycle"].code, dept=data["department"].code,
            public_id=data["work_item"].public_id, **kwargs,
        )


class TestChangeAccountRoutes:
    def test_get_form_renders_for_admin(self, app, client, seed_draft_work_item):
        data = seed_draft_work_item
        _make_submitted(data)
        _make_target_account(data)
        _login(client, "test:admin")

        resp = client.get(_url(app, "admin_final.line_change_account", data, line_num=1))
        assert resp.status_code == 200
        assert b"Change Expense Account" in resp.data
        assert b"Correct Account" in resp.data

    def test_post_changes_account_and_notifies(self, app, client, seed_draft_work_item, monkeypatch):
        data = seed_draft_work_item
        _make_submitted(data, decided=True)
        acct2, group2 = _make_target_account(data)
        _login(client, "test:admin")

        notified = {}
        monkeypatch.setattr(
            "app.services.notifications.notify_work_item_dispatched",
            lambda work_item, group_ids: notified.update(groups=group_ids) or 0,
        )

        resp = client.post(
            _url(app, "admin_final.line_change_account_submit", data, line_num=1),
            data={
                "expense_account_id": str(acct2.id),
                "approval_group_id": str(group2.id),
                "note": "Wrong account selected at submission",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Route handled the commit in its own session — refresh before asserting
        # (house pattern, see tests/integration/test_supply_review.py:200)
        db.session.refresh(data["detail"])
        db.session.refresh(data["line"])
        assert data["detail"].expense_account_id == acct2.id
        assert data["line"].status == WORK_LINE_STATUS_PENDING
        assert notified["groups"] == [group2.id]

    def test_post_rejected_for_non_admin(self, app, client, seed_draft_work_item):
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        _login(client, "test:reviewer")

        resp = client.post(
            _url(app, "admin_final.line_change_account_submit", data, line_num=1),
            data={
                "expense_account_id": str(acct2.id),
                "approval_group_id": str(group2.id),
                "note": "x",
            },
        )
        assert resp.status_code == 403


class TestAdminAddLineRoutes:
    def test_get_form_renders_for_admin(self, app, client, seed_draft_work_item):
        data = seed_draft_work_item
        _make_submitted(data)
        _make_line_refs()
        _login(client, "test:admin")

        resp = client.get(_url(app, "admin_final.line_add", data))
        assert resp.status_code == 200
        assert b"Add Line (Admin)" in resp.data

    def test_post_creates_routed_line(self, app, client, seed_draft_work_item, monkeypatch):
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        cl, fq, pr = _make_line_refs()
        _login(client, "test:admin")
        monkeypatch.setattr(
            "app.services.notifications.notify_work_item_dispatched",
            lambda work_item, group_ids: 0,
        )

        resp = client.post(
            _url(app, "admin_final.line_add_submit", data),
            data={
                "expense_account_id": str(acct2.id),
                "approval_group_id": str(group2.id),
                "quantity": "3",
                "unit_price": "45.50",
                "confidence_level_id": str(cl.id),
                "frequency_id": str(fq.id),
                "priority_id": str(pr.id),
                "description": "Forgotten major item",
                "note": "Missed during original submission",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(data["work_item"])
        lines = sorted(data["work_item"].lines, key=lambda l: l.line_number)
        assert len(lines) == 2
        new_line = lines[-1]
        assert new_line.budget_detail.unit_price_cents == 4550
        assert new_line.budget_detail.routed_approval_group_id == group2.id
        review = WorkLineReview.query.filter_by(
            work_line_id=new_line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        ).one()
        assert review.status == REVIEW_STATUS_PENDING

    def test_post_rejected_for_non_admin(self, app, client, seed_draft_work_item):
        data = seed_draft_work_item
        _make_submitted(data)
        _make_line_refs()
        _login(client, "test:reviewer")

        resp = client.post(_url(app, "admin_final.line_add_submit", data), data={})
        assert resp.status_code == 403


class TestEntryPoints:
    def test_admin_line_review_shows_edit_link(self, app, client, seed_draft_work_item):
        data = seed_draft_work_item
        _make_submitted(data)
        _login(client, "test:admin")

        resp = client.get(_url(app, "approvals.line_review", data, line_num=1))
        assert resp.status_code == 200
        assert b"change-account" in resp.data

    def test_edit_link_hidden_when_finalized(self, app, client, seed_draft_work_item):
        data = seed_draft_work_item
        _make_submitted(data)
        data["work_item"].status = WORK_ITEM_STATUS_FINALIZED
        db.session.commit()
        _login(client, "test:admin")

        resp = client.get(_url(app, "approvals.line_review", data, line_num=1))
        assert resp.status_code == 200
        assert b"change-account" not in resp.data

    def test_detail_page_shows_add_line_for_admin(self, app, client, seed_draft_work_item):
        data = seed_draft_work_item
        _make_submitted(data)
        _login(client, "test:admin")

        resp = client.get(_url(app, "work.work_item_detail", data))
        assert resp.status_code == 200
        assert b"add-line" in resp.data

    def test_approvals_line_review_shows_edit_link_for_admin(self, app, client, seed_draft_work_item):
        # Admins do their reviewing from the approvals-side line page
        # (budget/line_review.html), so the edit link must appear there too.
        data = seed_draft_work_item
        _make_submitted(data)
        _login(client, "test:admin")

        resp = client.get(_url(app, "approvals.line_review", data, line_num=1))
        assert resp.status_code == 200
        assert b"change-account" in resp.data
