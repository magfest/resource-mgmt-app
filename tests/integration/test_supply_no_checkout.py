"""Supply has no review sessions: reviewers decide without a checkout lock."""
from datetime import datetime, timedelta

from app import db
from app.models import (
    WorkItem, WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STATUS_PENDING,
    WORK_ITEM_STATUS_SUBMITTED,
)
from tests.integration.supply_helpers import login, setup_submitted_order
from tests.integration.test_supply_review_entry import (
    _order_url, _two_group_order, _user, decision_buttons_for_line,
    first_href_containing,
)


def _line_url(cycle, dept, item, line, action):
    return f"/{cycle.code}/{dept.code}/supply/item/{item.public_id}/line/{line.line_number}/{action}"


def _review(line):
    return WorkLineReview.query.filter_by(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP).one()


def test_supply_reviewer_decides_without_checkout(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data)
    login(client, "test:reviewer")

    client.post(_line_url(cycle, dept, work_item, line, "approve"))

    db.session.refresh(line)
    assert line.status == "APPROVED"


def test_supply_checkout_route_refuses(app, client, seed_workflow_data):
    from app.routes import UserContext
    from app.routes.work.helpers.checkout import can_checkout
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data)
    login(client, "test:reviewer")

    client.post(f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/checkout")

    db.session.refresh(work_item)
    assert work_item.checked_out_by_user_id is None
    ctx = UserContext(user_id="test:reviewer", user=None, roles=("APPROVER",),
                      is_super_admin=False, approval_group_ids={group.id})
    ok, reason = can_checkout(work_item, ctx)
    assert not ok and "does not use review sessions" in reason


def test_budget_still_requires_checkout(client, seed_draft_work_item):
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.add(WorkLineReview(
        work_line_id=d["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id, status=REVIEW_STATUS_PENDING,
        created_by_user_id=d["admin"].id))
    db.session.commit()
    login(client, "test:admin")

    resp = client.post(
        f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/approve")

    assert resp.status_code in (302, 403)
    db.session.refresh(d["line"])
    assert d["line"].status == "PENDING"


def test_second_supply_decision_names_first_decider(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data)
    login(client, "test:reviewer")
    client.post(_line_url(cycle, dept, work_item, line, "approve"))

    data = client.post(_line_url(cycle, dept, work_item, line, "reject"),
                       data={"ajax": "1", "note": "too late"}).get_json()

    assert data["success"] is False
    assert data["error"].startswith("Already approved")
    assert "Test Reviewer" in data["error"]
    assert data["current_status"] == "APPROVED"


def test_describe_decided_review_includes_reviewed_quantity(app, client, seed_workflow_data):
    from app.routes.approvals.helpers import describe_decided_review
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data)
    review = _review(line)
    review.status = "APPROVED"
    review.decided_by_user_id = "test:reviewer"
    review.decided_at = datetime(2026, 11, 22, 14, 2)
    from app.line_details import get_line_detail
    get_line_detail(line).quantity_reviewed = 40
    db.session.commit()

    assert describe_decided_review(review, line) == (
        "Already approved (qty 40) by Test Reviewer at 2026-11-22 14:02.")


def test_supply_pages_show_no_start_reviewing_prompt(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data)
    login(client, "test:reviewer")
    base = f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}"

    for url in (f"{base}/quick-review", f"{base}/line/1/review"):
        body = client.get(url).get_data(as_text=True)
        assert "Start Reviewing" not in body and "Checkout Required" not in body
        assert "Review Session Required" not in body
        assert "Approve" in body


def test_stale_checkout_on_supply_order_does_not_block(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data)
    _user("test:other")
    work_item.checked_out_by_user_id = "test:other"
    work_item.checked_out_at = datetime.utcnow()
    work_item.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=20)
    db.session.commit()
    login(client, "test:reviewer")

    body = client.get(
        f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}/quick-review"
    ).get_data(as_text=True)
    assert "Under Active Review" not in body

    client.post(_line_url(cycle, dept, work_item, line, "approve"))
    db.session.refresh(line)
    assert line.status == "APPROVED"


def test_quick_review_decision_buttons_follow_the_row_group(client, seed_workflow_data):
    from app.models import UserRole, ROLE_APPROVER
    wt, gen, tech, order = _two_group_order(client, seed_workflow_data)
    _user("test:techrev")
    db.session.add(UserRole(user_id="test:techrev", role_code=ROLE_APPROVER,
                            approval_group_id=tech.id))
    db.session.commit()
    login(client, "test:techrev")
    href = first_href_containing(
        client.get(_order_url(seed_workflow_data, order)).get_data(as_text=True),
        "/quick-review")

    body = client.get(href).get_data(as_text=True)

    assert decision_buttons_for_line(body, 1) == []
    assert "approve" in decision_buttons_for_line(body, 2)


def test_missing_uses_sessions_keeps_the_lock_ui(app, client, seed_workflow_data, monkeypatch):
    """A render that omits uses_sessions must show the lock, not hide it."""
    from app.routes.approvals import reviews
    from app.routes.work.work_items import view
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data)
    _user("test:other")
    work_item.checked_out_by_user_id = "test:other"
    work_item.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=20)
    db.session.commit()

    for module in (view, reviews):
        real = module.render_template
        monkeypatch.setattr(
            module, "render_template",
            lambda *a, _real=real, **kw: _real(
                *a, **{k: v for k, v in kw.items() if k != "uses_sessions"}),
        )
    login(client, "test:reviewer")
    base = f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}"

    for url in (f"{base}/quick-review", f"{base}/line/1/review"):
        assert "Under Active Review" in client.get(url).get_data(as_text=True)


def _department_member(work_type, data):
    from app.models import DepartmentMembership, DepartmentMembershipWorkTypeAccess
    _user("test:member")
    membership = DepartmentMembership(
        user_id="test:member", department_id=data["department"].id,
        event_cycle_id=data["cycle"].id,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id, work_type_id=work_type.id,
        can_view=True, can_edit=True,
    ))
    db.session.commit()


GENERIC_DECIDED = "This line was already decided."


def test_decided_supply_line_detail_only_for_reviewers(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, work_item, line = setup_submitted_order(
        app, client, seed_workflow_data)
    login(client, "test:reviewer")
    client.post(_line_url(cycle, dept, work_item, line, "approve"))
    _department_member(wt, seed_workflow_data)

    login(client, "test:member")
    data = client.post(_line_url(cycle, dept, work_item, line, "approve"),
                       data={"ajax": "1"}).get_json()
    assert data["success"] is False
    assert data["error"] == GENERIC_DECIDED
    assert "Test Reviewer" not in data["error"]

    login(client, "test:reviewer")
    data = client.post(_line_url(cycle, dept, work_item, line, "approve"),
                       data={"ajax": "1"}).get_json()
    assert data["error"].startswith("Already approved") and "Test Reviewer" in data["error"]


def test_decided_budget_line_detail_only_for_reviewers(client, seed_draft_work_item):
    from app.models import ROLE_APPROVER, UserRole
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.add(WorkLineReview(
        work_line_id=d["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id, status="APPROVED",
        decided_by_user_id="test:reviewer", decided_at=datetime(2026, 11, 22, 14, 2),
        created_by_user_id=d["admin"].id))
    db.session.add(UserRole(user_id="test:reviewer", role_code=ROLE_APPROVER,
                            approval_group_id=d["approval_group"].id))
    db.session.commit()
    _department_member(d["work_type"], d)
    url = f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/approve"

    for user_id in ("test:member", "test:reviewer"):
        # Holding the lock lets the request reach the transition check.
        d["work_item"].checked_out_by_user_id = user_id
        d["work_item"].checked_out_expires_at = datetime.utcnow() + timedelta(minutes=20)
        db.session.commit()
        login(client, user_id)
        data = client.post(url, data={"ajax": "1"}).get_json()
        assert data["success"] is False
        if user_id == "test:member":
            assert data["error"] == GENERIC_DECIDED
        else:
            assert data["error"] == "Already approved by Test Reviewer at 2026-11-22 14:02."
