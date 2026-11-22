"""Supply reviewers approve a quantity other than the one requested."""
from datetime import datetime, timedelta

from app import db
from app.models import (
    REVIEW_STAGE_APPROVAL_GROUP, WorkLineReview, WORK_ITEM_STATUS_SUBMITTED,
)
from tests.integration.supply_helpers import login, setup_submitted_order


def _url(cycle, dept, work_item, line, action):
    return (f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}"
            f"/line/{line.line_number}/{action}")


def _order(app, client, seed_workflow_data, quantity=100):
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data, quantity=quantity)
    login(client, "test:reviewer")
    return cycle, dept, work_item, line


def _post(client, cycle, dept, work_item, line, action, **data):
    return client.post(_url(cycle, dept, work_item, line, action),
                       data={"ajax": "1", **data})


def test_approve_with_lower_quantity_sets_quantity_reviewed(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    resp = _post(client, cycle, dept, wi, line, "approve", approved_quantity="40")
    assert resp.get_json()["success"] is True
    db.session.refresh(line)
    assert line.supply_detail.quantity_reviewed == 40 and line.status == "APPROVED"


def test_approve_with_higher_quantity_allowed(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    _post(client, cycle, dept, wi, line, "approve", approved_quantity="120")
    db.session.refresh(line)
    assert line.supply_detail.quantity_reviewed == 120


def test_approve_without_field_uses_requested(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    _post(client, cycle, dept, wi, line, "approve")
    db.session.refresh(line)
    assert line.supply_detail.quantity_reviewed == 100 and line.status == "APPROVED"


def _refused(client, cycle, dept, wi, line, value):
    data = _post(client, cycle, dept, wi, line, "approve", approved_quantity=value).get_json()
    db.session.refresh(line)
    assert data["success"] is False
    assert line.status == "PENDING"
    assert line.supply_detail.quantity_reviewed is None
    return data


def test_approve_blank_quantity_refused(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    data = _refused(client, cycle, dept, wi, line, "")
    assert "whole-number quantity" in data["error"]


def test_approve_non_numeric_quantity_refused(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    for value in ("abc", "4.5", "-3"):
        data = _refused(client, cycle, dept, wi, line, value)
        assert "whole-number quantity" in data["error"]


def test_approve_zero_quantity_refused(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    data = _refused(client, cycle, dept, wi, line, "0")
    assert "reject" in data["error"].lower()


def test_reject_sets_quantity_reviewed_zero(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    _post(client, cycle, dept, wi, line, "reject", note="no")
    db.session.refresh(line)
    assert line.status == "REJECTED" and line.supply_detail.quantity_reviewed == 0


def test_needs_info_clears_quantity_reviewed(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    line.supply_detail.quantity_reviewed = 7
    db.session.commit()
    _post(client, cycle, dept, wi, line, "needs-info", note="why")
    db.session.refresh(line)
    assert line.status == "NEEDS_INFO" and line.supply_detail.quantity_reviewed is None


def test_order_page_shows_approved_n_of_m_to_department(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    _post(client, cycle, dept, wi, line, "approve", approved_quantity="40")
    login(client, "test:admin")
    body = client.get(f"/{cycle.code}/{dept.code}/supply/order/{wi.public_id}").get_data(as_text=True)
    assert "Approved 40 of 100" in body


def test_budget_approve_ignores_approved_quantity_field(client, seed_draft_work_item):
    from app.models import WorkLineReview as R, REVIEW_STATUS_PENDING
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    review = R(work_line_id=d["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
               approval_group_id=d["approval_group"].id, status=REVIEW_STATUS_PENDING,
               created_by_user_id=d["admin"].id)
    db.session.add(review)
    db.session.commit()
    login(client, "test:admin")
    base = f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}"
    client.post(f"{base}/checkout")
    resp = client.post(f"{base}/line/1/approve",
                       data={"ajax": "1", "approved_quantity": "abc"})
    assert resp.get_json()["success"] is True
    db.session.refresh(d["line"])
    assert d["line"].status == "APPROVED"


def test_supply_order_page_hides_stale_checkout_banner(app, client, seed_workflow_data):
    cycle, dept, wi, line = _order(app, client, seed_workflow_data)
    wi.checked_out_by_user_id = "test:reviewer"
    wi.checked_out_at = datetime.utcnow()
    wi.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()
    for user in ("test:admin", "test:reviewer"):
        login(client, user)
        body = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{wi.public_id}").get_data(as_text=True)
        assert "Under Active Review" not in body
        assert "Under Review by Another Reviewer" not in body
