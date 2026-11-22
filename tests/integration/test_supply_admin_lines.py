"""Supply admins add, change and remove lines on submitted department orders."""
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from app import db
from app.models import (
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    SupplyOrderLineDetail,
    User,
    WorkItem,
    WorkItemAuditEvent,
    WorkLine,
    WorkLineAuditEvent,
    WorkLineReview,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STAGE_APPROVAL_GROUP,
    WORK_ITEM_STATUS_FINALIZED,
)
from app.routes.approvals.helpers import build_approval_queues
from tests.integration.supply_helpers import (
    add_line, login, make_draft_order, seed_approval_group, seed_category,
    seed_item, seed_reviewer, seed_supply, set_pickup_details,
)


def _env(client, data, quantities=(100,)):
    wt = seed_supply(data)
    gen = seed_approval_group(wt, "SUPPLY_GEN")
    category = seed_category(gen, "OFFICE")
    sharpie = seed_item(category, "Sharpie Markers", unit_cost_cents=500)
    board = seed_item(category, "Foam Board", unit_cost_cents=200)
    cycle, dept = data["cycle"], data["department"]
    order = make_draft_order(wt, cycle, dept)
    for qty in quantities:
        add_line(order, sharpie, quantity=qty)
    set_pickup_details(order)
    seed_reviewer(data, gen)
    login(client, "test:admin")
    resp = client.post(f"/{cycle.code}/{dept.code}/supply/order/{order.public_id}/submit")
    assert resp.status_code == 302
    return SimpleNamespace(
        wt=wt, gen=gen, category=category, sharpie=sharpie, board=board,
        cycle=cycle, dept=dept, order=order,
        order_url=f"/{cycle.code}/{dept.code}/supply/order/{order.public_id}",
        line_url=f"/{cycle.code}/{dept.code}/supply/item/{order.public_id}/line",
        admin_url=f"/admin/supply/order/{order.public_id}",
    )


def _line(env, number):
    db.session.expire_all()
    return WorkLine.query.filter_by(work_item_id=env.order.id, line_number=number).one()


def _ag(line):
    return WorkLineReview.query.filter_by(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP).one_or_none()


def _admin_review(line):
    return WorkLineReview.query.filter_by(
        work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL).one_or_none()


def _flashes(client):
    with client.session_transaction() as sess:
        return [m for _cat, m in sess.get("_flashes", [])]


def _add(client, env, item, quantity, return_to="order", **extra):
    data = {"item_id": str(item.id), "quantity": str(quantity), "return_to": return_to}
    data.update(extra)
    return client.post(f"{env.admin_url}/lines/add", data=data)


def _change(client, env, number, quantity, reason="venue supplies some", return_to="order"):
    return client.post(f"{env.admin_url}/lines/{number}/quantity", data={
        "quantity": str(quantity), "reason": reason, "return_to": return_to,
    })


def _remove(client, env, number, reason="using venue boards", return_to="order"):
    return client.post(f"{env.admin_url}/lines/{number}/remove", data={
        "reason": reason, "return_to": return_to,
    })


def _reviewer_decides(client, env, number, action, **data):
    login(client, "test:reviewer")
    payload = {"ajax": "1"}
    payload.update(data)
    resp = client.post(f"{env.line_url}/{number}/{action}", data=payload)
    login(client, "test:admin")
    return resp.get_json()


def _member(env, data):
    db.session.add(User(id="test:member", email="member@test.local",
                        display_name="Dept Member", is_active=True))
    membership = DepartmentMembership(
        user_id="test:member", department_id=env.dept.id, event_cycle_id=env.cycle.id,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id, work_type_id=env.wt.id,
        can_view=True, can_edit=True,
    ))
    db.session.commit()


def _row(html, number):
    match = re.search(rf'<tr id="line-{number}".*?</tr>', html, re.S)
    assert match, f"row for line {number} not found"
    return match.group(0)


# Add

def test_add_line_on_order_page_routes_to_group_pending(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = _add(client, env, env.board, 3, notes="for the info desk")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(env.order_url)
    line = _line(env, 2)
    review = _ag(line)
    assert line.status == "PENDING" and review.status == "PENDING"
    assert review.approval_group_id == env.gen.id
    detail = line.supply_detail
    assert detail.item_id == env.board.id and detail.quantity_requested == 3
    assert detail.added_by_admin_user_id == "test:admin"
    assert detail.routed_approval_group_id == env.gen.id
    assert detail.requester_notes is None
    created = WorkLineAuditEvent.query.filter_by(
        work_line_id=line.id, event_type="LINE_CREATED").one()
    assert created.note == "for the info desk"
    assert WorkItemAuditEvent.query.filter_by(
        work_item_id=env.order.id, event_type="LINE_CREATED").count() == 1


def test_add_line_at_finalize_lands_approved_without_group_review(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = _add(client, env, env.board, 4, return_to="finalize")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"{env.admin_url}/finalize")
    line = _line(env, 2)
    assert line.status == "APPROVED"
    assert line.current_review_stage == REVIEW_STAGE_ADMIN_FINAL
    assert _ag(line) is None
    admin_review = _admin_review(line)
    assert admin_review.status == "APPROVED"
    assert admin_review.approved_amount_cents == 800
    assert line.supply_detail.quantity_approved == 4
    assert line.supply_detail.added_by_admin_user_id == "test:admin"


def test_add_refuses_unavailable_items_and_bad_quantities(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    retired = seed_item(env.category, "Retired Item", is_active=False)
    hidden = seed_item(env.category, "Deploy Only AP")
    hidden.is_orderable = False
    db.session.commit()
    for item, qty in ((retired, 1), (hidden, 1), (env.board, 0), (env.board, "abc"),
                      (env.board, 100001), (env.board, -2)):
        resp = _add(client, env, item, qty)
        assert resp.status_code == 302
    db.session.expire_all()
    assert WorkLine.query.filter_by(work_item_id=env.order.id).count() == 1
    messages = _flashes(client)
    assert any("Remove" in m for m in messages)


def test_new_line_number_counts_removed_lines(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data, quantities=(100, 5))
    assert _remove(client, env, 2).status_code == 302
    _add(client, env, env.board, 2)
    line = _line(env, 3)
    assert line.supply_detail.item_id == env.board.id


# Change requested quantity

def test_change_quantity_on_order_page_resets_review(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert _reviewer_decides(client, env, 1, "approve", approved_quantity="40")["success"]
    resp = _change(client, env, 1, 150)
    assert resp.status_code == 302
    line = _line(env, 1)
    review = _ag(line)
    detail = line.supply_detail
    assert review.status == "PENDING" and detail.quantity_reviewed is None
    assert review.decided_at is None and review.decided_by_user_id is None
    assert line.status == "PENDING" and not line.needs_requester_action
    assert detail.department_quantity == 100 and detail.quantity_requested == 150
    audit = WorkLineAuditEvent.query.filter_by(
        work_line_id=line.id, event_type="FIELD_CHANGE").one()
    assert (audit.field_name, audit.old_value, audit.new_value) == (
        "quantity_requested", "100", "150")
    assert audit.note == "venue supplies some"


def test_change_quantity_on_order_page_clears_kickback(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert _reviewer_decides(client, env, 1, "needs-info", note="which booth?")["success"]
    assert _line(env, 1).needs_requester_action
    _change(client, env, 1, 80)
    line = _line(env, 1)
    assert line.status == "PENDING" and not line.needs_requester_action
    assert _ag(line).status == "PENDING"


def test_change_quantity_at_finalize_keeps_review(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert _reviewer_decides(client, env, 1, "approve", approved_quantity="40")["success"]
    resp = _change(client, env, 1, 150, return_to="finalize")
    assert resp.headers["Location"].endswith(f"{env.admin_url}/finalize")
    line = _line(env, 1)
    review = _ag(line)
    assert review.status == "APPROVED"
    assert line.supply_detail.quantity_reviewed == 40
    assert line.supply_detail.quantity_requested == 150


def test_second_change_keeps_original_department_quantity(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _change(client, env, 1, 150)
    _change(client, env, 1, 120)
    detail = _line(env, 1).supply_detail
    assert detail.department_quantity == 100
    assert detail.quantity_requested == 120


def test_change_quantity_requires_reason(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _change(client, env, 1, 150, reason="   ")
    _change(client, env, 1, 150, reason="x" * 1001)
    _change(client, env, 1, 0)
    _change(client, env, 1, 100)
    detail = _line(env, 1).supply_detail
    assert detail.quantity_requested == 100 and detail.department_quantity is None
    messages = _flashes(client)
    assert any("reason" in m.lower() for m in messages)
    assert any("1000" in m or "1,000" in m for m in messages)
    assert any("Remove" in m for m in messages)
    assert WorkLineAuditEvent.query.filter_by(event_type="FIELD_CHANGE").count() == 0


def test_change_or_remove_refused_on_removed_line(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _remove(client, env, 1)
    _change(client, env, 1, 150)
    _remove(client, env, 1, reason="again")
    detail = _line(env, 1).supply_detail
    assert detail.quantity_requested == 100
    assert detail.removed_reason == "using venue boards"
    assert WorkLineAuditEvent.query.filter_by(event_type="LINE_REMOVED").count() == 1


def test_order_page_change_on_line_added_at_finalize_sends_it_for_review(
    app, client, seed_workflow_data
):
    env = _env(client, seed_workflow_data)
    _add(client, env, env.board, 4, return_to="finalize")
    _change(client, env, 2, 6)
    line = _line(env, 2)
    assert line.status == "PENDING" and line.supply_detail.quantity_approved is None
    assert _ag(line).status == "PENDING" and _ag(line).approval_group_id == env.gen.id
    assert _admin_review(line).status == "PENDING"
    # The group can now decide it; no stale admin decision blocks them.
    assert _reviewer_decides(client, env, 2, "approve", approved_quantity="6")["success"]


def test_remove_line_added_at_finalize_rejects_admin_decision(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _add(client, env, env.board, 4, return_to="finalize")
    _remove(client, env, 2)
    line = _line(env, 2)
    assert line.status == "REJECTED" and line.supply_detail.quantity_approved == 0
    assert _admin_review(line).status == "REJECTED"


# Remove

def test_remove_line_strikes_and_takes_it_out_of_group_queue(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = _remove(client, env, 1)
    assert resp.status_code == 302
    line = _line(env, 1)
    detail = line.supply_detail
    assert line.status == "REJECTED" and detail.removed_reason == "using venue boards"
    assert detail.removed_by_user_id == "test:admin" and detail.removed_at is not None
    assert detail.quantity_approved == 0 and not line.needs_requester_action
    review = _ag(line)
    assert review.status == "REJECTED"
    assert review.note == "Removed by Supply admin: using venue boards"

    queues = build_approval_queues(env.gen.id)
    gen_queue_line_ids = (
        [r.work_item.id for r in queues.pending_requests]
        + [k.work_line.id for k in queues.kicked_back]
    )
    assert gen_queue_line_ids == []
    assert WorkLineAuditEvent.query.filter_by(
        work_line_id=line.id, event_type="LINE_REMOVED").one().note == "using venue boards"
    item_audit = WorkItemAuditEvent.query.filter_by(
        work_item_id=env.order.id, event_type="LINE_REMOVED").one()
    assert item_audit.reason == "using venue boards"


def test_removed_kicked_back_line_no_longer_blocks_finalize(app, client, seed_workflow_data):
    from app.routes.work.supply.admin import next_ready_order
    env = _env(client, seed_workflow_data, quantities=(100, 5))
    assert _reviewer_decides(client, env, 1, "approve", approved_quantity="40")["success"]
    assert _reviewer_decides(client, env, 2, "needs-info", note="why?")["success"]
    _remove(client, env, 2)
    body = client.get(f"/admin/supply/queue/?event={env.cycle.code}").get_data(as_text=True)
    assert "Ready to finalize" in body
    assert next_ready_order(env.cycle.id, exclude_id=-1).id == env.order.id
    resp = client.post(f"{env.admin_url}/finalize", data={})
    assert resp.status_code == 302
    db.session.expire_all()
    assert db.session.get(WorkItem, env.order.id).status == WORK_ITEM_STATUS_FINALIZED
    line2 = _line(env, 2)
    assert line2.status == "REJECTED" and line2.supply_detail.quantity_approved == 0


def test_reason_crlf_normalized(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _remove(client, env, 1, reason="  using venue boards\r\nper hotel  ")
    detail = _line(env, 1).supply_detail
    assert "\r" not in detail.removed_reason
    assert detail.removed_reason == "using venue boards\nper hotel"


def test_reason_over_limit_refused_for_remove(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _remove(client, env, 1, reason="x" * 1001)
    _remove(client, env, 1, reason="")
    assert _line(env, 1).supply_detail.removed_at is None


# Who and when

def test_non_admin_reviewer_cannot_edit_lines(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    login(client, "test:reviewer")
    for resp in (
        _add(client, env, env.board, 1),
        _change(client, env, 1, 150),
        _remove(client, env, 1),
    ):
        assert resp.status_code == 403
    assert _line(env, 1).supply_detail.removed_at is None


def test_edit_refused_on_finalized_order(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert client.post(f"{env.admin_url}/finalize", data={}).status_code == 302
    _add(client, env, env.board, 1)
    _change(client, env, 1, 150)
    _remove(client, env, 1)
    line = _line(env, 1)
    assert line.supply_detail.quantity_requested == 100
    assert line.supply_detail.removed_at is None
    assert WorkLine.query.filter_by(work_item_id=env.order.id).count() == 1
    assert any("submitted" in m.lower() for m in _flashes(client))


def test_edit_refused_on_closed_order(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    env.order.supply_order_detail.closed_at = datetime.now(timezone.utc)
    db.session.commit()
    _remove(client, env, 1)
    _add(client, env, env.board, 1)
    assert _line(env, 1).supply_detail.removed_at is None
    assert WorkLine.query.filter_by(work_item_id=env.order.id).count() == 1


def test_return_to_never_follows_a_url(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = _change(client, env, 1, 150, return_to="https://example.com/")
    assert resp.headers["Location"].endswith(env.order_url)
    # Anything but "finalize" is an order-page edit, so the review resets.
    resp = _remove(client, env, 1, return_to="//example.com")
    assert resp.headers["Location"].endswith(env.order_url)


# What people see

def test_department_sees_removed_added_and_changed_lines(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data, quantities=(100, 5))
    _change(client, env, 1, 150)
    _remove(client, env, 2, reason="using venue boards <b>now</b>")
    _add(client, env, env.board, 3)
    _member(env, seed_workflow_data)
    login(client, "test:member")
    body = client.get(env.order_url).get_data(as_text=True)
    assert "Removed by Supply admin" in body and "using venue boards &lt;b&gt;now&lt;/b&gt;" in body
    assert "Added by Supply admin" in body and "100 → 150" in body
    assert "Changed by Supply admin" in body
    assert "line-through" in _row(body, 2)
    # Department members get no admin controls.
    assert "/lines/add" not in body


def test_order_page_shows_admin_line_controls_only_to_supply_admin(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    body = client.get(env.order_url).get_data(as_text=True)
    assert f"{env.admin_url}/lines/add" in body
    assert f"{env.admin_url}/lines/1/remove" in body
    assert 'name="return_to" value="order"' in body
    login(client, "test:reviewer")
    body = client.get(env.order_url).get_data(as_text=True)
    assert "/lines/add" not in body


def test_finalize_screen_has_line_controls_and_strikes_removed(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data, quantities=(100, 5))
    _remove(client, env, 2)
    body = client.get(f"{env.admin_url}/finalize").get_data(as_text=True)
    assert f"{env.admin_url}/lines/add" in body
    assert 'name="return_to" value="finalize"' in body
    assert 'name="approved_qty_2"' not in body
    assert "Removed by Supply admin" in body and "using venue boards" in body
    assert f"{env.admin_url}/lines/2/remove" not in body
    assert "Foam Board" in body


def test_quick_review_and_line_review_strike_removed_line(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data, quantities=(100, 5))
    _remove(client, env, 2)
    login(client, "test:reviewer")
    qr = client.get(
        f"/{env.cycle.code}/{env.dept.code}/supply/item/{env.order.public_id}/quick-review"
    ).get_data(as_text=True)
    row = _row(qr, 2)
    assert "Removed by Supply admin" in row and "using venue boards" in row
    assert "data-action" not in row and "Undo" not in row
    assert "line-through" in row
    review = client.get(f"{env.line_url}/2/review").get_data(as_text=True)
    assert "Removed by Supply admin" in review and "using venue boards" in review
    assert "Make Decision" not in review and "Undo this decision" not in review
    assert "line-through" in review


# Stale pages (Review Focus 3 and 4)

def test_stale_quick_review_approve_on_removed_line_names_removal(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _remove(client, env, 1)
    data = _reviewer_decides(client, env, 1, "approve", approved_quantity="")
    assert data["success"] is False
    assert data["error"].startswith("Removed by Supply admin")
    assert "Test Admin" in data["error"] and "using venue boards" in data["error"]
    assert data["removed"] is True


def test_removal_message_wins_even_with_stale_pending_review(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _remove(client, env, 1)
    review = _ag(_line(env, 1))
    review.status = "PENDING"
    db.session.commit()
    data = _reviewer_decides(client, env, 1, "approve", approved_quantity="5")
    assert data["success"] is False
    assert data["error"].startswith("Removed by Supply admin")
    line = _line(env, 1)
    assert line.status == "REJECTED" and line.supply_detail.quantity_reviewed is None


def test_stale_finalize_post_for_removed_line_keeps_it_removed(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data, quantities=(100, 5))
    _remove(client, env, 2)
    resp = client.post(f"{env.admin_url}/finalize", data={
        "approved_qty_1": "100", "approved_qty_2": "5", "note_2": "",
    })
    assert resp.status_code == 302
    line = _line(env, 2)
    detail = line.supply_detail
    assert detail.quantity_approved == 0 and line.status == "REJECTED"
    assert _admin_review(line) is None


def test_requester_respond_refused_on_removed_line(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert _reviewer_decides(client, env, 1, "needs-info", note="which booth?")["success"]
    _remove(client, env, 1)
    _member(env, seed_workflow_data)
    login(client, "test:member")
    client.post(f"{env.line_url}/1/respond", data={"response": "booth 4"})
    assert any(m.startswith("Removed by Supply admin") for m in _flashes(client))
    line = _line(env, 1)
    assert line.status == "REJECTED" and _ag(line).status == "REJECTED"


# Queue selector

def test_queue_selector_shows_filtered_event_with_no_orders(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    alt = seed_workflow_data["second_event_cycle"]
    body = client.get(f"/admin/supply/queue/?event={alt.code}").get_data(as_text=True)
    assert re.search(rf'<option value="{alt.code}"\s+selected>', body)
