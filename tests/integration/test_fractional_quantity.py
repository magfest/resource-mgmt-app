"""Budget line amounts with a fractional quantity round the product, not the quantity."""
from decimal import Decimal

from app import db
from app.models import (
    WorkLineReview,
    REVIEW_ACTION_APPROVE,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
)
from app.routes import UserContext
from app.routes.admin_final.department_report import get_department_data
from app.routes.admin_final.helpers import apply_admin_final_decision


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _admin_ctx():
    return UserContext(
        user_id="test:admin", user=None,
        roles=("SUPER_ADMIN",), is_super_admin=True,
        approval_group_ids=set(),
    )


def _make_fractional(data):
    data["line"].budget_detail.quantity = Decimal("1.5")
    db.session.commit()


def test_finalize_books_fractional_quantity(app, client, seed_draft_work_item):
    data = seed_draft_work_item
    _make_fractional(data)
    data["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    data["line"].status = WORK_LINE_STATUS_APPROVED
    # Finalize refuses an item with no reviewer decision. No amount here, so
    # finalize falls back to the requested amount.
    db.session.add(WorkLineReview(
        work_line_id=data["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED, approved_amount_cents=None,
        created_by_user_id=data["admin"].id))
    db.session.commit()

    _login(client, "test:admin")
    resp = client.post(
        f"/admin/final-review/finalize/{data['work_item'].id}",
        data={"note": "ok"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(data["line"])
    # 1.5 x $50.00 = $75.00; truncating the quantity booked $50.00.
    assert data["line"].approved_amount_cents == 7500


def test_admin_approve_default_amount_uses_fractional_quantity(app, seed_draft_work_item):
    data = seed_draft_work_item
    _make_fractional(data)
    data["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.commit()

    ok, err = apply_admin_final_decision(
        data["line"], data["work_item"], REVIEW_ACTION_APPROVE,
        approved_amount_cents=None, note=None, user_ctx=_admin_ctx(),
    )

    assert ok, err
    assert data["line"].status == WORK_LINE_STATUS_APPROVED
    assert data["line"].approved_amount_cents == 7500


def test_report_sql_amount_uses_fractional_quantity(app, seed_draft_work_item):
    data = seed_draft_work_item
    _make_fractional(data)

    rows = get_department_data(data["work_item"].portfolio.event_cycle_id)

    assert len(rows) == 1
    assert rows[0].draft_cents == 7500
