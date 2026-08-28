"""
Integration tests for budget-admin line tools:
expense account correction + admin add-line on in-review requests.
"""
import re
from collections import Counter
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
                "description": "Corrected to the right account",
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
        # absent_as_none keeps quantity/unit_price at their current values
        # when the form omits them; description is required now, so it is
        # always written from what was submitted.
        assert data["detail"].quantity == 1
        assert data["detail"].unit_price_cents == 5000
        assert data["detail"].description == "Corrected to the right account"

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
                "description": "Corrected account",
                "note": "x",
            },
        )
        assert resp.status_code == 403

    def test_account_only_change_preserves_quantity_price_and_description(
        self, app, client, seed_draft_work_item
    ):
        """Regression test for the Task 6 review finding: change-account's
        quantity and unit_price inputs are absent-not-zero via
        absent_as_none, so an account-only change must leave them alone.
        Description is no longer part of that contract: it is required on
        every submission and is always written from what was posted."""
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        detail = data["detail"]
        detail.quantity = 4
        detail.unit_price_cents = 25000
        detail.description = "Original important description"
        db.session.commit()
        _login(client, "test:admin")

        resp = client.post(
            _url(app, "admin_final.line_change_account_submit", data, line_num=1),
            data={
                "expense_account_id": str(acct2.id),
                "approval_group_id": str(group2.id),
                "description": "Corrected account, same pricing",
                "note": "Wrong account selected at submission",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        db.session.refresh(detail)
        assert detail.quantity == 4
        assert detail.unit_price_cents == 25000
        assert detail.description == "Corrected account, same pricing"

    def test_post_without_description_is_rejected_and_writes_nothing(
        self, app, client, seed_draft_work_item
    ):
        """Description is required on every change-account submission; a
        POST that omits it must be rejected before anything is written, not
        silently keep the line on its old account with the old value."""
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        detail = data["detail"]
        original_account_id = detail.expense_account_id
        original_description = detail.description
        _login(client, "test:admin")

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
        assert b"Description is required." in resp.data

        db.session.refresh(detail)
        assert detail.expense_account_id == original_account_id
        assert detail.expense_account_id != acct2.id
        assert detail.description == original_description


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

    def test_post_without_description_is_rejected_and_creates_no_line(
        self, app, client, seed_draft_work_item
    ):
        """Description is required on every add-line submission; a POST
        that omits it must be rejected before a line is created."""
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        cl, fq, pr = _make_line_refs()
        _login(client, "test:admin")

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
                "note": "Missed during original submission",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Description is required." in resp.data

        db.session.refresh(data["work_item"])
        assert len(data["work_item"].lines) == 1


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


class TestPriceSnapshotColumn:
    def test_column_defaults_to_none(self, app, seed_draft_work_item):
        data = seed_draft_work_item
        assert data["detail"].account_default_unit_price_cents is None

class TestAdminAccountList:
    def test_includes_fixed_hotel_and_badge_excludes_inactive(
        self, app, seed_draft_work_item
    ):
        from app.models import UI_GROUP_BADGES, UI_GROUP_HOTEL_SERVICES
        from app.routes.admin_final.line_admin import _get_admin_expense_accounts

        data = seed_draft_work_item
        for code, fixed, group, active in [
            ("HTL_DOUBLE_MAGPAID", True, UI_GROUP_HOTEL_SERVICES, True),
            ("BADGE_STAFF", True, UI_GROUP_BADGES, True),
            ("ETH_DROP", True, None, True),
            ("RETIRED_ACCT", True, None, False),
        ]:
            db.session.add(ExpenseAccount(
                code=code, name=code.title(), is_active=active,
                is_fixed_cost=fixed, ui_display_group=group,
                default_unit_price_cents=15900,
                spend_type_mode=SPEND_TYPE_MODE_SINGLE_LOCKED,
                default_spend_type_id=data["spend_type"].id,
            ))
        db.session.commit()

        codes = {
            a.code for a in _get_admin_expense_accounts(data["cycle"].id)
        }
        assert "HTL_DOUBLE_MAGPAID" in codes
        assert "BADGE_STAFF" in codes
        assert "ETH_DROP" in codes
        assert "RETIRED_ACCT" not in codes


class TestSnapshotThroughHelpers:
    def test_add_line_stores_snapshot(self, app, seed_draft_work_item):
        from app.routes.admin_final.helpers import admin_add_line
        data = seed_draft_work_item
        _make_submitted(data)
        cl, fq, pr = _make_line_refs()
        acct2, group2 = _make_target_account(data)

        line, err = admin_add_line(
            work_item=data["work_item"], user_ctx=_admin_ctx(),
            expense_account=acct2, spend_type=data["spend_type"],
            approval_group=group2, quantity=1, unit_price_cents=18900,
            confidence_level=cl, frequency=fq, priority=pr,
            warehouse_flag=False, description="Guest of honor suite",
            note="Negotiated rate", account_default_unit_price_cents=15900,
        )
        db.session.commit()

        assert err is None
        assert line.budget_detail.unit_price_cents == 18900
        assert line.budget_detail.account_default_unit_price_cents == 15900

    def test_change_account_writes_price_and_snapshot(
        self, app, seed_draft_work_item
    ):
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="Wrong account", user_ctx=_admin_ctx(),
            quantity=6, unit_price_cents=18900,
            account_default_unit_price_cents=15900,
            description="2 rooms: Guest of honor",
        )
        db.session.commit()

        assert ok is True
        detail = data["line"].budget_detail
        assert detail.unit_price_cents == 18900
        assert detail.quantity == 6
        assert detail.account_default_unit_price_cents == 15900
        assert detail.description == "2 rooms: Guest of honor"

    def test_omitted_fields_leave_existing_values_but_snapshot_updates(
        self, app, seed_draft_work_item
    ):
        """quantity/price/description are guarded by `is not None`; the
        account snapshot is not, so it always takes the passed-in value."""
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        detail = data["line"].budget_detail
        orig_quantity = detail.quantity
        orig_price = detail.unit_price_cents
        orig_description = detail.description

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="Account only", user_ctx=_admin_ctx(),
            account_default_unit_price_cents=15900,
        )
        db.session.commit()

        assert ok is True
        assert detail.quantity == orig_quantity
        assert detail.unit_price_cents == orig_price
        assert detail.description == orig_description
        assert detail.account_default_unit_price_cents == 15900

    def test_omitted_snapshot_clears_existing_snapshot(
        self, app, seed_draft_work_item
    ):
        """Omitting account_default_unit_price_cents clears any prior
        snapshot; the write is unconditional, unlike quantity/price/description."""
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        detail = data["line"].budget_detail
        detail.account_default_unit_price_cents = 9900
        db.session.commit()

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="Re-booked", user_ctx=_admin_ctx(),
            quantity=6, unit_price_cents=18900,
        )
        db.session.commit()

        assert ok is True
        assert detail.quantity == 6
        assert detail.unit_price_cents == 18900
        assert detail.account_default_unit_price_cents is None

    def test_audit_records_pre_change_values(self, app, seed_draft_work_item):
        """Old values must be captured before the writes, not after, or the
        audit trail would record a no-op instead of the real change."""
        from app.models import WorkLineAuditEvent
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)
        detail = data["line"].budget_detail
        orig_quantity = detail.quantity
        orig_price = detail.unit_price_cents

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="Corrected quantity and rate",
            user_ctx=_admin_ctx(), quantity=6, unit_price_cents=18900,
        )
        db.session.commit()
        assert ok is True

        events = WorkLineAuditEvent.query.filter_by(
            work_line_id=data["line"].id
        ).all()
        qty_event = next(e for e in events if e.field_name == "quantity")
        assert qty_event.old_value == str(orig_quantity)
        assert qty_event.new_value == "6"

        price_event = next(e for e in events if e.field_name == "unit_price")
        assert price_event.old_value == f"${orig_price / 100:,.2f}"
        assert price_event.new_value == "$189.00"

    def test_no_op_account_resubmit_records_no_expense_account_audit_row(
        self, app, seed_draft_work_item
    ):
        """spend_type/review_group/quantity/unit_price are only audited when
        they actually change; expense_account must be guarded the same way,
        or re-submitting without touching the dropdown logs a fake
        "X -> X" change."""
        from app.models import WorkLineAuditEvent
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data)

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=data["expense_account"], new_spend_type=data["spend_type"],
            new_group=data["approval_group"], note="Re-confirming the account is correct",
            user_ctx=_admin_ctx(),
        )
        db.session.commit()
        assert ok is True

        events = WorkLineAuditEvent.query.filter_by(
            work_line_id=data["line"].id, field_name="expense_account",
        ).all()
        assert events == []

    def test_real_account_change_records_expense_account_audit_row(
        self, app, seed_draft_work_item
    ):
        from app.models import WorkLineAuditEvent
        from app.routes.admin_final.helpers import change_line_expense_account
        data = seed_draft_work_item
        _make_submitted(data)
        acct2, group2 = _make_target_account(data)

        ok, err = change_line_expense_account(
            line=data["line"], work_item=data["work_item"],
            new_account=acct2, new_spend_type=data["spend_type"],
            new_group=group2, note="Wrong account selected at submission",
            user_ctx=_admin_ctx(),
        )
        db.session.commit()
        assert ok is True

        events = WorkLineAuditEvent.query.filter_by(
            work_line_id=data["line"].id, field_name="expense_account",
        ).all()
        assert len(events) == 1
        assert events[0].old_value == data["expense_account"].name
        assert events[0].new_value == acct2.name


class TestHotelLineThroughRoute:
    def _hotel_account(self, data):
        from app.models import UI_GROUP_HOTEL_SERVICES
        acct = ExpenseAccount(
            code="HTL_DOUBLE_MAGPAID", name="Double, MAGFest Paid",
            is_active=True, is_fixed_cost=True,
            ui_display_group=UI_GROUP_HOTEL_SERVICES,
            default_unit_price_cents=15900,
            spend_type_mode=SPEND_TYPE_MODE_SINGLE_LOCKED,
            default_spend_type_id=data["spend_type"].id,
            approval_group_id=data["approval_group"].id,
        )
        db.session.add(acct)
        db.session.commit()
        return acct

    def test_add_hotel_line_with_override(self, app, client, seed_draft_work_item):
        data = seed_draft_work_item
        _make_submitted(data)
        cl, fq, pr = _make_line_refs()
        acct = self._hotel_account(data)
        _login(client, "test:admin")

        resp = client.post(
            _url(app, "admin_final.line_add_submit", data),
            data={
                "expense_account_id": acct.id,
                "approval_group_id": data["approval_group"].id,
                "rooms": "2", "nights": "3",
                "unit_price": "189.00", "price_override": "on",
                "confidence_level_id": cl.id,
                "frequency_id": fq.id,
                "priority_id": pr.id,
                "description": "Guest of honor suite",
                "note": "Negotiated rate for a special room",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 302
        new_line = [
            l for l in data["work_item"].lines if l.line_number == 2
        ][0]
        detail = new_line.budget_detail
        assert detail.quantity == 6
        assert detail.unit_price_cents == 18900
        assert detail.account_default_unit_price_cents == 15900
        assert detail.description == "2 rooms: Guest of honor suite"

    def test_add_hotel_line_without_override_uses_default(
        self, app, client, seed_draft_work_item
    ):
        data = seed_draft_work_item
        _make_submitted(data)
        cl, fq, pr = _make_line_refs()
        acct = self._hotel_account(data)
        _login(client, "test:admin")

        client.post(
            _url(app, "admin_final.line_add_submit", data),
            data={
                "expense_account_id": acct.id,
                "approval_group_id": data["approval_group"].id,
                "rooms": "1", "nights": "2",
                "unit_price": "999.00",
                "confidence_level_id": cl.id,
                "frequency_id": fq.id,
                "priority_id": pr.id,
                "description": "Standard crash space",
                "note": "Extra room needed",
            },
        )

        new_line = [
            l for l in data["work_item"].lines if l.line_number == 2
        ][0]
        assert new_line.budget_detail.unit_price_cents == 15900


class TestAdminLineForms:
    def _hotel_account(self, data):
        from app.models import UI_GROUP_HOTEL_SERVICES
        acct = ExpenseAccount(
            code="HTL_STD_MAGPAID", name="Standard, MAGFest Paid",
            is_active=True, is_fixed_cost=True,
            ui_display_group=UI_GROUP_HOTEL_SERVICES,
            default_unit_price_cents=15900,
            spend_type_mode=SPEND_TYPE_MODE_SINGLE_LOCKED,
            default_spend_type_id=data["spend_type"].id,
            approval_group_id=data["approval_group"].id,
        )
        db.session.add(acct)
        db.session.commit()
        return acct

    def _price_override_tag(self, body):
        match = re.search(r'<input[^>]*name="price_override"[^>]*>', body)
        assert match is not None, "price_override checkbox not found in rendered form"
        return match.group(0)

    def _price_input_tag(self, body):
        match = re.search(r'<input[^>]*name="unit_price"[^>]*>', body)
        assert match is not None, "unit_price input not found in rendered form"
        return match.group(0)

    def test_add_form_renders_hotel_and_override_controls(
        self, app, client, seed_draft_work_item
    ):
        data = seed_draft_work_item
        _make_submitted(data)
        _make_line_refs()
        _login(client, "test:admin")

        resp = client.get(_url(app, "admin_final.line_add", data))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert 'name="rooms"' in body
        assert 'name="nights"' in body
        assert 'name="price_override"' in body

        # A value="1" here would submit the string "1", not "on", and
        # resolve_line_pricing only treats the literal "on" as an override
        # request -- see test_price_override_requires_exact_literal_on.
        assert 'value=' not in self._price_override_tag(body)

    def test_change_account_form_renders_description(
        self, app, client, seed_draft_work_item
    ):
        data = seed_draft_work_item
        _make_submitted(data)
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert 'name="description"' in body
        assert 'name="rooms"' in body
        assert 'value=' not in self._price_override_tag(body)

    def test_change_account_renders_the_lines_own_stored_price(
        self, app, client, seed_draft_work_item
    ):
        """Pins the value _line_account_fields_js.html's originalPrice reads
        from the DOM at load. If the server ever stopped rendering the
        line's own price here, the JS fix for switching back to a STANDARD
        account after a defaulted one would have nothing correct to restore."""
        data = seed_draft_work_item
        _make_submitted(data)
        detail = data["detail"]
        detail.unit_price_cents = 30000
        db.session.commit()
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert re.search(r'name="unit_price"[^>]*value="300\.00"', body)

    def test_change_account_renders_current_spend_type_id_for_js(
        self, app, client, seed_draft_work_item
    ):
        """currentSpendTypeId must come from the route, not a template-level
        {% set %}: _line_account_fields_js.html renders in the scripts block,
        a different frame than line_change_account.html's content block, so
        a block-scoped set is invisible there and silently falls back to ''.
        Uses an ALLOW_LIST account, since SINGLE_LOCKED accounts fix the
        spend type regardless and would make this assertion vacuous."""
        data = seed_draft_work_item
        _make_submitted(data)
        assert data["expense_account"].spend_type_mode != SPEND_TYPE_MODE_SINGLE_LOCKED
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert f"const currentSpendTypeId = '{data['spend_type'].id}';" in body

    def test_change_account_renders_locked_styling_for_fixed_cost_account(
        self, app, client, seed_draft_work_item
    ):
        """A readOnly input gets no browser styling on its own, so the field
        must carry the price-locked class hook whenever it's readonly, or it
        looks editable while silently rejecting keystrokes."""
        data = seed_draft_work_item
        _make_submitted(data)
        acct = self._hotel_account(data)
        detail = data["detail"]
        detail.expense_account_id = acct.id
        detail.spend_type_id = acct.default_spend_type_id
        detail.quantity = 1
        detail.unit_price_cents = acct.default_unit_price_cents
        detail.account_default_unit_price_cents = None
        db.session.commit()
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)
        tag = self._price_input_tag(body)

        assert resp.status_code == 200
        assert "price-locked" in tag
        assert "readonly" in tag

    def test_change_account_renders_unlocked_styling_for_standard_account(
        self, app, client, seed_draft_work_item
    ):
        """seed_draft_work_item's line is on a STANDARD account (no account
        default), so the price field must render fully editable."""
        data = seed_draft_work_item
        _make_submitted(data)
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)
        tag = self._price_input_tag(body)

        assert resp.status_code == 200
        assert "price-locked" not in tag
        assert "readonly" not in tag

    def test_change_account_prefills_rooms_and_nights_for_hotel_line(
        self, app, client, seed_draft_work_item
    ):
        """An existing hotel line's room/night split must survive opening the
        change-account form, not collapse to a 1/1 default."""
        data = seed_draft_work_item
        _make_submitted(data)
        acct = self._hotel_account(data)
        detail = data["detail"]
        detail.expense_account_id = acct.id
        detail.spend_type_id = acct.default_spend_type_id
        detail.quantity = 6
        detail.description = "3 rooms: Suite block"
        db.session.commit()
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert re.search(r'name="rooms"[^>]*value="3"', body)
        assert re.search(r'name="nights"[^>]*value="2"', body)

    def test_change_account_strips_rooms_prefix_from_description(
        self, app, client, seed_draft_work_item
    ):
        """The server re-adds "N rooms: " from the Rooms field on submit;
        showing it in the textarea too invites a hand-edit that submit would
        then silently discard."""
        data = seed_draft_work_item
        _make_submitted(data)
        acct = self._hotel_account(data)
        detail = data["detail"]
        detail.expense_account_id = acct.id
        detail.spend_type_id = acct.default_spend_type_id
        detail.quantity = 6
        detail.description = "3 rooms: Suite block"
        db.session.commit()
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        textarea = re.search(
            r'<textarea[^>]*name="description"[^>]*>(.*?)</textarea>', body, re.DOTALL
        )
        assert textarea is not None, "description textarea not found"
        assert "3 rooms:" not in textarea.group(1)
        assert "Suite block" in textarea.group(1)

    def test_change_account_leaves_rooms_and_nights_empty_for_non_hotel_line(
        self, app, client, seed_draft_work_item
    ):
        """seed_draft_work_item's line is on a STANDARD account; there is no
        room/night split to preserve, and 1/1 would be an invented booking."""
        data = seed_draft_work_item
        _make_submitted(data)
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert re.search(r'name="rooms"[^>]*value=""', body)
        assert re.search(r'name="nights"[^>]*value=""', body)

    def test_change_account_prechecks_override_when_price_differs_from_account_default(
        self, app, client, seed_draft_work_item
    ):
        """A stored price that differs from the account's current default is
        a deliberate override; the checkbox must start checked or
        refreshPricing() would silently reset it on page load."""
        data = seed_draft_work_item
        _make_submitted(data)
        acct = self._hotel_account(data)
        detail = data["detail"]
        detail.expense_account_id = acct.id
        detail.spend_type_id = acct.default_spend_type_id
        detail.quantity = 6
        detail.unit_price_cents = 18900
        detail.account_default_unit_price_cents = 15900
        detail.description = "3 rooms: Suite block"
        db.session.commit()
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "checked" in self._price_override_tag(body)

    def test_change_account_prechecks_override_with_null_snapshot(
        self, app, client, seed_draft_work_item
    ):
        """Every requester-created fixed-cost/hotel/badge line has a NULL
        account_default_unit_price_cents; the override check must not depend
        on that column, or every such line's price gets silently reset."""
        data = seed_draft_work_item
        _make_submitted(data)
        acct = self._hotel_account(data)
        detail = data["detail"]
        detail.expense_account_id = acct.id
        detail.spend_type_id = acct.default_spend_type_id
        detail.quantity = 6
        detail.unit_price_cents = 18900
        detail.account_default_unit_price_cents = None
        detail.description = "3 rooms: Suite block"
        db.session.commit()
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "checked" in self._price_override_tag(body)

    def test_change_account_leaves_override_unchecked_when_price_matches_snapshot(
        self, app, client, seed_draft_work_item
    ):
        data = seed_draft_work_item
        _make_submitted(data)
        acct = self._hotel_account(data)
        detail = data["detail"]
        detail.expense_account_id = acct.id
        detail.spend_type_id = acct.default_spend_type_id
        detail.quantity = 6
        detail.unit_price_cents = 15900
        detail.account_default_unit_price_cents = 15900
        detail.description = "3 rooms: Suite block"
        db.session.commit()
        _login(client, "test:admin")

        resp = client.get(_url(
            app, "admin_final.line_change_account", data,
            line_num=data["line"].line_number,
        ))
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "checked" not in self._price_override_tag(body)

    def test_no_duplicate_element_ids_in_either_form(
        self, app, client, seed_draft_work_item
    ):
        """Regression guard: both templates used to carry their own
        expense_account_id/spend_type_id blocks before the shared partial. A
        leftover copy would silently break the shared script."""
        data = seed_draft_work_item
        _make_submitted(data)
        _make_line_refs()
        _login(client, "test:admin")

        bodies = [
            client.get(_url(app, "admin_final.line_add", data)).get_data(as_text=True),
            client.get(_url(
                app, "admin_final.line_change_account", data,
                line_num=data["line"].line_number,
            )).get_data(as_text=True),
        ]

        for body in bodies:
            counts = Counter(re.findall(r'\bid="([^"]+)"', body))
            dupes = {k: v for k, v in counts.items() if v > 1}
            assert dupes == {}, f"duplicate element ids: {dupes}"


class TestOverrideBadge:
    def _render(self, app, detail):
        from flask import render_template_string
        return render_template_string(
            "{% from 'macros/price_override_badge.html' import price_override_badge"
            " with context %}{{ price_override_badge(d) }}",
            d=detail,
        )

    def test_badge_absent_when_no_snapshot(self, app, seed_draft_work_item):
        data = seed_draft_work_item
        with app.test_request_context():
            assert "Price Overridden" not in self._render(app, data["detail"])

    def test_badge_absent_when_price_matches_default(
        self, app, seed_draft_work_item
    ):
        data = seed_draft_work_item
        data["detail"].account_default_unit_price_cents = 5000
        db.session.commit()
        with app.test_request_context():
            assert "Price Overridden" not in self._render(app, data["detail"])

    def test_badge_present_when_price_differs(self, app, seed_draft_work_item):
        data = seed_draft_work_item
        data["detail"].account_default_unit_price_cents = 15900
        db.session.commit()
        with app.test_request_context():
            body = self._render(app, data["detail"])
            assert "Price Overridden" in body
            assert "159.00" in body


class TestFixedCostSaveRespectsOverride:
    def test_override_survives_a_fixed_cost_save(self, app, seed_draft_work_item):
        """work_item_fixed_costs_save must not reset a price an admin deliberately set.

        Unreachable in production today: requester edit needs DRAFT
        (checkout.py:256) and the admin tools need SUBMITTED or NEEDS_INFO
        (admin_final/helpers.py:783, :875). This asserts the invariant anyway,
        because nothing in either file states it.
        """
        from app.routes.work.helpers import get_effective_fixed_cost_settings

        data = seed_draft_work_item
        detail = data["detail"]
        detail.account_default_unit_price_cents = 15900
        detail.unit_price_cents = 18900
        db.session.commit()

        account = detail.expense_account
        account.is_fixed_cost = True
        account.default_unit_price_cents = 15900
        db.session.commit()

        settings = get_effective_fixed_cost_settings(account, data["cycle"].id)
        assert settings["unit_price_cents"] == 15900

        from app.routes.work.work_items.edit import _keeps_admin_price_override
        assert _keeps_admin_price_override(detail) is True

        detail.account_default_unit_price_cents = 15900
        detail.unit_price_cents = 15900
        db.session.commit()
        assert _keeps_admin_price_override(detail) is False

    def test_route_keeps_override_price_but_applies_quantity_change(
        self, app, client, seed_draft_work_item
    ):
        """End-to-end: the guard must be wired into the handler, not just
        exist as an isolated predicate. Asserts quantity too, since a guard
        that freezes the whole line (not just the price) would also pass a
        price-only check."""
        data = seed_draft_work_item
        detail = data["detail"]
        account = detail.expense_account
        account.is_fixed_cost = True
        account.default_unit_price_cents = 15900
        detail.unit_price_cents = 18900
        detail.account_default_unit_price_cents = 15900
        db.session.commit()
        _login(client, "test:admin")

        resp = client.post(
            _url(app, "work.work_item_fixed_costs_save", data),
            data={f"fixed_qty_{account.id}": "3"},
        )
        assert resp.status_code == 302

        db.session.refresh(detail)
        assert detail.unit_price_cents == 18900
        assert detail.quantity == 3

    def test_route_refreshes_price_for_a_line_with_no_override(
        self, app, client, seed_draft_work_item
    ):
        """Mirror case: a NULL snapshot means no admin tool ever touched this
        line, so the save must still refresh its price to the account
        default, same as before the guard existed."""
        data = seed_draft_work_item
        detail = data["detail"]
        account = detail.expense_account
        account.is_fixed_cost = True
        account.default_unit_price_cents = 15900
        db.session.commit()
        assert detail.account_default_unit_price_cents is None
        _login(client, "test:admin")

        resp = client.post(
            _url(app, "work.work_item_fixed_costs_save", data),
            data={f"fixed_qty_{account.id}": "2"},
        )
        assert resp.status_code == 302

        db.session.refresh(detail)
        assert detail.unit_price_cents == 15900
        assert detail.quantity == 2


class TestBadgeSaveRespectsOverride:
    """Badge accounts became reachable from the admin line tools in an
    earlier task of this plan, so work_item_badges_save needs the same
    guard as work_item_fixed_costs_save against clobbering an admin's
    deliberate price override."""

    def test_route_keeps_override_price_but_applies_quantity_change(
        self, app, client, seed_draft_work_item
    ):
        from app.models import UI_GROUP_BADGES

        data = seed_draft_work_item
        detail = data["detail"]
        account = detail.expense_account
        account.is_fixed_cost = True
        account.ui_display_group = UI_GROUP_BADGES
        account.default_unit_price_cents = 0
        detail.unit_price_cents = 500
        detail.account_default_unit_price_cents = 0
        db.session.commit()
        _login(client, "test:admin")

        resp = client.post(
            _url(app, "work.work_item_badges_save", data),
            data={f"badge_qty_{account.id}": "3"},
        )
        assert resp.status_code == 302

        db.session.refresh(detail)
        assert detail.unit_price_cents == 500
        assert detail.quantity == 3

    def test_route_refreshes_price_for_a_line_with_no_override(
        self, app, client, seed_draft_work_item
    ):
        from app.models import UI_GROUP_BADGES

        data = seed_draft_work_item
        detail = data["detail"]
        account = detail.expense_account
        account.is_fixed_cost = True
        account.ui_display_group = UI_GROUP_BADGES
        account.default_unit_price_cents = 0
        db.session.commit()
        assert detail.account_default_unit_price_cents is None
        _login(client, "test:admin")

        resp = client.post(
            _url(app, "work.work_item_badges_save", data),
            data={f"badge_qty_{account.id}": "2"},
        )
        assert resp.status_code == 302

        db.session.refresh(detail)
        assert detail.unit_price_cents == 0
        assert detail.quantity == 2
