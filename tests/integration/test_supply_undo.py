"""Supply reviewers undo a decision while the order is still under review."""
from datetime import datetime, timezone

from app import db
from app.models import (
    ApprovalGroup, User, UserRole, WorkLineAuditEvent, WORK_ITEM_STATUS_FINALIZED,
    ROLE_APPROVER, ROLE_WORKTYPE_ADMIN,
)
from tests.integration.supply_helpers import login, setup_submitted_order


def _base(cycle, dept, work_item, line):
    return (f"/{cycle.code}/{dept.code}/supply/item/{work_item.public_id}"
            f"/line/{line.line_number}")


def _approved_order(app, client, seed_workflow_data, quantity=10):
    parts = setup_submitted_order(app, client, seed_workflow_data, quantity=quantity)
    wt, cycle, dept, group, item, work_item, line = parts
    login(client, "test:reviewer")
    resp = client.post(_base(cycle, dept, work_item, line) + "/approve",
                       data={"ajax": "1", "approved_quantity": "4"})
    assert resp.get_json()["success"] is True
    db.session.refresh(line)
    assert line.status == "APPROVED"
    return parts


def _reopen(client, cycle, dept, work_item, line, **data):
    return client.post(_base(cycle, dept, work_item, line) + "/reopen", data=data)


def _flashes(client):
    with client.session_transaction() as sess:
        return [m for _cat, m in sess.get("_flashes", [])]


def test_group_member_undoes_approval_on_submitted_supply_order(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    resp = _reopen(client, cycle, dept, wi, line)
    assert resp.status_code == 302
    db.session.refresh(line)
    assert line.status == "PENDING" and line.supply_detail.quantity_reviewed is None
    last = (WorkLineAuditEvent.query.filter_by(work_line_id=line.id)
            .order_by(WorkLineAuditEvent.id.desc()).first())
    assert last.event_type == "REVIEW_DECISION"


def test_undo_redirects_to_line_review_by_default(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    resp = _reopen(client, cycle, dept, wi, line)
    assert resp.headers["Location"].endswith("/line/1") or "/line/" in resp.headers["Location"]
    assert "quick" not in resp.headers["Location"]


def test_undo_returns_to_quick_review_when_asked(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    resp = _reopen(client, cycle, dept, wi, line, return_to="quick_review")
    assert "quick" in resp.headers["Location"]
    assert resp.headers["Location"].startswith("/")


def test_undo_ignores_other_return_to_values(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    resp = _reopen(client, cycle, dept, wi, line, return_to="https://evil.example/")
    assert "evil.example" not in resp.headers["Location"]
    assert "quick" not in resp.headers["Location"]


def test_other_group_member_cannot_undo(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    other_group = ApprovalGroup(work_type_id=wt.id, code="OTHER", name="Other", is_active=True)
    other = User(id="test:other", email="o@test.local", display_name="Other", is_active=True)
    db.session.add_all([other_group, other])
    db.session.flush()
    db.session.add(UserRole(user_id=other.id, role_code=ROLE_APPROVER,
                            approval_group_id=other_group.id))
    db.session.commit()
    login(client, "test:other")
    _reopen(client, cycle, dept, wi, line)
    db.session.refresh(line)
    assert line.status == "APPROVED"


def test_supply_admin_can_undo(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    admin = User(id="test:sadmin", email="s@test.local", display_name="SA", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_WORKTYPE_ADMIN, work_type_id=wt.id))
    db.session.commit()
    login(client, "test:sadmin")
    _reopen(client, cycle, dept, wi, line)
    db.session.refresh(line)
    assert line.status == "PENDING"


def test_undo_refused_on_finalized_supply_order(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    wi.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()
    _reopen(client, cycle, dept, wi, line)
    db.session.refresh(line)
    assert line.status == "APPROVED"
    error = " ".join(_flashes(client))
    assert "admin" in error.lower() and "reopen" in error.lower()


def test_undo_refused_on_removed_line(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    line.status = "REJECTED"
    line.supply_detail.removed_at = datetime.now(timezone.utc)
    db.session.commit()
    _reopen(client, cycle, dept, wi, line)
    db.session.refresh(line)
    assert line.status == "REJECTED" and line.supply_detail.removed_at is not None


def test_undo_shown_only_on_decided_rows_user_may_undo(app, client, seed_workflow_data):
    wt, cycle, dept, group, item, wi, line = _approved_order(app, client, seed_workflow_data)
    quick = f"/{cycle.code}/{dept.code}/supply/item/{wi.public_id}/quick-review"
    line_page = _base(cycle, dept, wi, line) + "/review"
    for url in (quick, line_page):
        body = client.get(url).get_data(as_text=True)
        assert "/reopen" in body, url
    # Pending line: no undo.
    _reopen(client, cycle, dept, wi, line)
    for url in (quick, line_page):
        assert "/reopen" not in client.get(url).get_data(as_text=True), url


def test_budget_reopen_still_refused(client, seed_draft_work_item):
    from app.models import (
        WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STATUS_APPROVED,
        WORK_ITEM_STATUS_SUBMITTED,
    )
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    d["line"].status = "APPROVED"
    db.session.add(WorkLineReview(
        work_line_id=d["line"].id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=d["approval_group"].id, status=REVIEW_STATUS_APPROVED,
        created_by_user_id=d["admin"].id))
    db.session.commit()
    login(client, "test:admin")
    _reopen_url = f"/TST2026/TESTDEPT/budget/item/{d['work_item'].public_id}/line/1/reopen"
    client.post(_reopen_url, data={"note": "x"})
    db.session.refresh(d["line"])
    assert d["line"].status == "APPROVED"
