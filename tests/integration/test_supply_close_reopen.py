"""Supply admins close a department's order with a reason, and reopen finalized or closed orders."""
import json
import re

from sqlalchemy import inspect as sa_inspect

from app import db
from app.models import (
    EmailOutbox,
    NotificationLog,
    WorkItem,
    WorkItemAuditEvent,
    WorkItemComment,
    WorkLine,
    WorkLineReview,
    AUDIT_EVENT_ORDER_CLOSED,
    AUDIT_EVENT_ORDER_REOPENED,
    REVIEW_STAGE_APPROVAL_GROUP,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.models.constants import NOTIF_STATUS_CANCELLED, OUTBOX_STATUS_CANCELLED
from app.routes.work.helpers.computations import compute_line_status_summary
from app.routes.work.supply.line_state import supply_display_status
from tests.integration.supply_helpers import login
from tests.integration.test_supply_admin_lines import (
    _add, _admin_review, _ag, _change, _env, _flashes, _line, _member, _remove,
    _reviewer_decides,
)
from tests.integration.test_supply_email import _rows, _seed_templates

REASON = "Ordered under the wrong department"


def _close(client, env, reason=REASON, **extra):
    return client.post(f"{env.admin_url}/close", data={"reason": reason, **extra})


def _reopen(client, env, reason="Department asked for one more item", **extra):
    return client.post(f"{env.admin_url}/reopen", data={"reason": reason, **extra})


def _finalize(client, env, **data):
    assert client.post(f"{env.admin_url}/finalize", data=data).status_code == 302


def _order(env):
    db.session.expire_all()
    return db.session.get(WorkItem, env.order.id)


def _ready(client, data, quantities=(10,)):
    env = _env(client, data, quantities=quantities)
    _seed_templates()
    _member(env, data)
    return env


def _snapshot(env):
    """Everything a refused action must leave unchanged."""
    order = _order(env)
    lines = WorkLine.query.filter_by(work_item_id=order.id).order_by(WorkLine.line_number).all()
    reviews = WorkLineReview.query.filter(
        WorkLineReview.work_line_id.in_([l.id for l in lines])).order_by(WorkLineReview.id).all()
    return (
        order.status,
        order.supply_order_detail.closed_at,
        [(l.line_number, l.status, l.supply_detail.quantity_requested,
          l.supply_detail.quantity_approved, l.supply_detail.removed_at) for l in lines],
        [(r.id, r.stage, r.status, r.decided_at) for r in reviews],
    )


# Close

def test_close_marks_order_closed_and_lines_rejected(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data, quantities=(10, 5, 3))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="6")
    _remove(client, env, 3, reason="venue supplies these")
    resp = _close(client, env)
    assert resp.status_code == 302 and resp.headers["Location"].endswith(env.order_url)

    order = _order(env)
    assert order.status == WORK_ITEM_STATUS_FINALIZED and supply_display_status(order) == "CLOSED"
    assert compute_line_status_summary(order).effective_status == "CLOSED"
    assert order.finalized_at is not None and order.finalized_by_user_id == "test:admin"
    detail = order.supply_order_detail
    assert detail.closed_reason == REASON and detail.closed_by_user_id == "test:admin"
    assert detail.closed_at is not None
    assert all(l.status == "REJECTED" for l in order.lines)
    assert all(l.supply_detail.quantity_approved == 0 for l in order.lines)

    # The group's approval stays on record; the open review closes with the reason.
    assert _ag(_line(env, 1)).status == "APPROVED"
    pending = _ag(_line(env, 2))
    assert pending.status == "REJECTED" and pending.note == f"Order closed: {REASON}"
    assert pending.decided_at is not None
    removed = _line(env, 3).supply_detail
    assert removed.removed_reason == "venue supplies these"

    audit = WorkItemAuditEvent.query.filter_by(
        work_item_id=order.id, event_type=AUDIT_EVENT_ORDER_CLOSED).one()
    assert audit.reason == REASON


def test_close_sends_closed_email_not_finalized(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _close(client, env)
    rows = _rows("supply_closed")
    assert [r.recipient_email for r in rows] == ["member@test.local"]
    assert json.loads(rows[0].context_json)["reason"] == REASON
    assert _rows("supply_finalized") == []


def test_close_email_failure_does_not_block_close(app, client, seed_workflow_data, monkeypatch):
    env = _ready(client, seed_workflow_data)

    def boom(*args, **kwargs):
        raise RuntimeError("SES down")

    monkeypatch.setattr("app.routes.work.supply.order_lifecycle.notify_supply_closed", boom)
    _close(client, env)
    assert supply_display_status(_order(env)) == "CLOSED"


def test_finalize_email_failure_does_not_block_finalize(app, client, seed_workflow_data,
                                                        monkeypatch):
    env = _ready(client, seed_workflow_data)

    def boom(*args, **kwargs):
        raise RuntimeError("SES down")

    monkeypatch.setattr("app.routes.work.supply.admin.notify_supply_finalized", boom)
    _finalize(client, env)
    assert _order(env).status == WORK_ITEM_STATUS_FINALIZED


def test_closed_order_shows_reason_to_department(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _close(client, env, reason="Wrong <b>department</b>")
    login(client, "test:member")
    body = client.get(env.order_url).get_data(as_text=True)
    assert "Closed" in body
    assert "Wrong &lt;b&gt;department&lt;/b&gt;" in body
    assert "Wrong <b>department</b>" not in body
    # A department member gets no admin controls.
    assert f"{env.admin_url}/reopen" not in body


def test_closed_order_stays_in_department_and_admin_lists(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _close(client, env)
    pid = env.order.public_id
    login(client, "test:member")
    landing = client.get(f"/{env.cycle.code}/{env.dept.code}/supply").get_data(as_text=True)
    assert pid in landing and "Closed" in landing
    login(client, "test:admin")
    for url in ("/admin/supply/orders/", "/admin/supply/orders/?status=FINALIZED"):
        body = client.get(url).get_data(as_text=True)
        assert pid in body and "Closed" in body, url


def test_home_and_department_cards_show_closed(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _close(client, env)
    login(client, "test:member")
    dept_home = client.get(f"/{env.cycle.code}/{env.dept.code}/").get_data(as_text=True)
    assert "pill-muted" in dept_home and ">Closed<" in dept_home
    home = client.get("/").get_data(as_text=True)
    assert "pill-muted pill-link" in home and ">Closed<" in home


def test_close_requires_reason_and_submitted_status(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    before = _snapshot(env)
    for reason in ("", "  \r\n  ", "x" * 1001):
        _close(client, env, reason=reason)
        assert _snapshot(env) == before, repr(reason)

    _finalize(client, env)
    finalized = _snapshot(env)
    _close(client, env)
    assert _snapshot(env) == finalized
    assert _order(env).supply_order_detail.closed_reason is None
    assert _rows("supply_closed") == []


def test_close_and_reopen_refused_for_non_admin(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    login(client, "test:reviewer")
    assert _close(client, env).status_code == 403
    login(client, "test:admin")
    _close(client, env)
    login(client, "test:reviewer")
    assert _reopen(client, env).status_code == 403
    assert supply_display_status(_order(env)) == "CLOSED"


def test_close_reason_crlf_normalized(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _close(client, env, reason="  line one\r\nline two  ")
    assert _order(env).supply_order_detail.closed_reason == "line one\nline two"


def test_close_redirect_never_follows_a_url(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    resp = _close(client, env, return_to="https://example.com/")
    assert resp.headers["Location"].endswith(env.order_url)
    resp = _reopen(client, env, return_to="finalize")
    assert resp.headers["Location"].endswith(f"{env.admin_url}/finalize")


# Reopen

def test_reopen_finalized_keeps_decisions_and_posts_public_comment(
        app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(10, 4))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="6")
    _finalize(client, env, **{"approved_qty_2": "0", "note_2": "out of stock"})
    _reopen(client, env, reason="Add tape\r\nplease")

    order = _order(env)
    assert order.status == WORK_ITEM_STATUS_SUBMITTED
    assert order.finalized_at is None and order.finalized_by_user_id is None
    line1, line2 = _line(env, 1), _line(env, 2)
    assert line1.status == "APPROVED" and line1.supply_detail.quantity_approved == 6
    assert line2.status == "REJECTED"
    assert _admin_review(line1).status == "APPROVED"

    comment = WorkItemComment.query.filter_by(work_item_id=order.id).one()
    assert comment.visibility == "PUBLIC" and comment.body == "[REOPENED] Add tape\nplease"
    audit = WorkItemAuditEvent.query.filter_by(
        work_item_id=order.id, event_type=AUDIT_EVENT_ORDER_REOPENED).one()
    assert audit.reason == "Add tape\nplease"


def test_reopen_closed_returns_lines_to_pending_except_removed(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(10, 5, 3))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="6")
    _remove(client, env, 3, reason="venue supplies these")
    _add(client, env, env.board, 2, return_to="finalize")
    assert _ag(_line(env, 4)) is None
    _close(client, env)
    _reopen(client, env)

    order = _order(env)
    assert order.status == WORK_ITEM_STATUS_SUBMITTED and order.finalized_at is None
    detail = order.supply_order_detail
    assert detail.closed_at is None and detail.closed_by_user_id is None
    assert detail.closed_reason is None
    assert supply_display_status(order) == "UNDER_REVIEW"
    for number in (1, 2, 4):
        line = _line(env, number)
        assert line.status == "PENDING", number
        assert line.current_review_stage == REVIEW_STAGE_APPROVAL_GROUP
        assert line.supply_detail.quantity_approved is None
        assert line.supply_detail.quantity_reviewed is None
        review = _ag(line)
        assert review.status == "PENDING" and review.decided_at is None, number
        assert review.note is None
    # The line added at finalize carried an admin decision; it no longer stands.
    admin = _admin_review(_line(env, 4))
    assert admin.status == "PENDING" and admin.decided_at is None
    assert admin.approved_amount_cents is None
    removed = _line(env, 3)
    assert removed.status == "REJECTED" and removed.supply_detail.removed_at is not None
    assert removed.supply_detail.quantity_approved == 0


def test_reopen_closed_resets_admin_final_from_an_earlier_finalize(
        app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _finalize(client, env)
    _reopen(client, env)
    _close(client, env)
    _reopen(client, env)
    line = _line(env, 1)
    assert line.status == "PENDING"
    admin = _admin_review(line)
    assert admin.status == "PENDING" and admin.decided_at is None
    assert admin.decided_by_user_id is None and admin.note is None
    assert admin.approved_amount_cents is None
    # The group can decide again and finalize books the new quantity.
    _reviewer_decides(client, env, 1, "approve", approved_quantity="7")
    _finalize(client, env)
    assert _line(env, 1).supply_detail.quantity_approved == 7


def test_reopen_sends_no_email(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _close(client, env)
    count = EmailOutbox.query.count()
    _reopen(client, env)
    assert _order(env).status == WORK_ITEM_STATUS_SUBMITTED
    assert EmailOutbox.query.count() == count


def test_reopen_cancels_unsent_finalized_email(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _finalize(client, env)
    _reopen(client, env)
    row = _rows("supply_finalized")[0]
    assert row.status == OUTBOX_STATUS_CANCELLED and row.dedup_key is None
    assert NotificationLog.query.filter_by(
        template_key="supply_finalized", status=NOTIF_STATUS_CANCELLED).count() == 1
    # A re-finalize queues a fresh email.
    _finalize(client, env)
    assert sorted(r.status for r in _rows("supply_finalized")) == ["CANCELLED", "QUEUED"]


def test_reopen_cancels_unsent_closed_email(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _close(client, env)
    _reopen(client, env)
    assert [r.status for r in _rows("supply_closed")] == [OUTBOX_STATUS_CANCELLED]


def test_reopen_requires_reason_and_finalized_status(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    before = _snapshot(env)
    _reopen(client, env)
    assert _snapshot(env) == before
    assert WorkItemComment.query.count() == 0
    _close(client, env)
    closed = _snapshot(env)
    for reason in ("", "  \r\n ", "x" * 1001):
        _reopen(client, env, reason=reason)
        assert _snapshot(env) == closed, repr(reason)
    assert WorkItemComment.query.count() == 0


def test_reviewer_undo_hidden_and_refused_after_reopening_finalized(
        app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _reviewer_decides(client, env, 1, "approve", approved_quantity="6")
    _finalize(client, env)
    _reopen(client, env)
    assert _order(env).status == WORK_ITEM_STATUS_SUBMITTED

    login(client, "test:reviewer")
    quick = f"/{env.cycle.code}/{env.dept.code}/supply/item/{env.order.public_id}/quick-review"
    for url in (quick, f"{env.line_url}/1/review"):
        assert '/reopen"' not in client.get(url).get_data(as_text=True), url
    client.post(f"{env.line_url}/1/reopen")
    assert _line(env, 1).status == "APPROVED" and _ag(_line(env, 1)).status == "APPROVED"


# A closed order changes only through Reopen

def test_actions_on_closed_order_refused(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(10, 5))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="6")
    _close(client, env)
    before = _snapshot(env)

    resps = [
        _reviewer_decides_resp(client, env, 2, "approve", approved_quantity="5"),
        _reviewer_decides_resp(client, env, 1, "reopen"),
        client.post(f"{env.line_url}/1/reopen"),
        _add(client, env, env.board, 1),
        _add(client, env, env.board, 1, return_to="finalize"),
        _change(client, env, 1, 20),
        _change(client, env, 1, 20, return_to="finalize"),
        _remove(client, env, 2),
        client.post(f"{env.admin_url}/finalize", data={"approved_qty_1": "6"}),
        _close(client, env),
    ]
    assert all(r.status_code in (302, 400, 403) for r in resps), [r.status_code for r in resps]
    assert _snapshot(env) == before
    assert supply_display_status(_order(env)) == "CLOSED"
    assert len(_rows("supply_closed")) == 1


def _reviewer_decides_resp(client, env, number, action, **data):
    login(client, "test:reviewer")
    resp = client.post(f"{env.line_url}/{number}/{action}", data=data)
    login(client, "test:admin")
    return resp


def test_finalize_refuses_closed_order_even_if_status_were_submitted(
        app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _close(client, env)
    order = _order(env)
    # A row edited by hand must still not finalize while closed.
    order.status = WORK_ITEM_STATUS_SUBMITTED
    db.session.commit()
    client.post(f"{env.admin_url}/finalize", data={})
    assert _order(env).status == WORK_ITEM_STATUS_SUBMITTED
    assert _rows("supply_finalized") == []


# Controls

def test_admin_sees_close_on_submitted_and_reopen_on_finalized(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    page = client.get(env.order_url).get_data(as_text=True)
    final = client.get(f"{env.admin_url}/finalize").get_data(as_text=True)
    for body in (page, final):
        assert f'action="{env.admin_url}/close"' in body
        assert f'action="{env.admin_url}/reopen"' not in body
    _close(client, env)
    page = client.get(env.order_url).get_data(as_text=True)
    final = client.get(f"{env.admin_url}/finalize").get_data(as_text=True)
    for body in (page, final):
        assert f'action="{env.admin_url}/reopen"' in body
        assert f'action="{env.admin_url}/close"' not in body
        assert REASON in body
    login(client, "test:reviewer")
    body = client.get(env.order_url).get_data(as_text=True)
    assert "/close\"" not in body and "/reopen\"" not in body


# Other work types

def test_budget_finalized_card_unchanged(app, seed_draft_work_item):
    from datetime import datetime, timezone
    item = seed_draft_work_item["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    db.session.expire_all()
    item = db.session.get(WorkItem, item.id)
    assert compute_line_status_summary(item).effective_status == "FINALIZED"
    # The closed check never loads a Supply detail for another work type.
    assert "supply_order_detail" in sa_inspect(item).unloaded
    item.board_released_at = None
    db.session.commit()
    assert compute_line_status_summary(item).effective_status == "PENDING_BOARD_APPROVAL"


def test_supply_finalized_not_closed_still_finalized(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _finalize(client, env)
    order = _order(env)
    assert compute_line_status_summary(order).effective_status == "FINALIZED"
    assert supply_display_status(order) == "FINALIZED"


def test_close_and_reopen_forms_confirm_on_submit_only(app, client, seed_workflow_data):
    """base.html confirms any click inside a [data-confirm] element, so the forms must not carry it."""
    env = _ready(client, seed_workflow_data)

    def form_tag(body, action):
        match = re.search(rf'<form[^>]*action="{re.escape(env.admin_url)}/{action}"[^>]*>', body)
        assert match, action
        return match.group(0)

    for url in (env.order_url, f"{env.admin_url}/finalize"):
        tag = form_tag(client.get(url).get_data(as_text=True), "close")
        assert "data-confirm=" not in tag and "data-confirm-submit=" in tag, url
    _close(client, env)
    for url in (env.order_url, f"{env.admin_url}/finalize"):
        tag = form_tag(client.get(url).get_data(as_text=True), "reopen")
        assert "data-confirm=" not in tag and "data-confirm-submit=" in tag, url


def test_kicked_back_line_survives_close_reopen_and_finalizes(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(10, 4))
    assert _reviewer_decides(client, env, 1, "needs-info", note="which booth?")["success"]
    line = _line(env, 1)
    assert _ag(line).status == "NEEDS_INFO" and line.status == "NEEDS_INFO"
    assert line.needs_requester_action is True

    _close(client, env)
    line = _line(env, 1)
    review = _ag(line)
    assert review.status == "REJECTED" and review.note == f"Order closed: {REASON}"
    assert line.status == "REJECTED" and line.needs_requester_action is False
    assert line.supply_detail.quantity_approved == 0

    _reopen(client, env)
    line = _line(env, 1)
    review = _ag(line)
    assert review.status == "PENDING" and review.decided_at is None and review.note is None
    assert line.status == "PENDING" and line.needs_requester_action is False
    assert line.supply_detail.quantity_approved is None

    assert _reviewer_decides(client, env, 1, "approve", approved_quantity="8")["success"]
    line = _line(env, 1)
    assert _ag(line).status == "APPROVED" and line.status == "APPROVED"
    assert line.needs_requester_action is False
    assert line.supply_detail.quantity_approved is None

    _finalize(client, env)
    order = _order(env)
    assert order.status == WORK_ITEM_STATUS_FINALIZED and supply_display_status(order) == "FINALIZED"
    line = _line(env, 1)
    assert line.status == "APPROVED" and line.needs_requester_action is False
    assert line.supply_detail.quantity_approved == 8
    assert _admin_review(line).status == "APPROVED"


# Refinalizing a reopened order starts from the admin's last decisions

def _finalize_inputs(client, env):
    """Map each finalize input name to the value the screen prefills."""
    body = client.get(f"{env.admin_url}/finalize").get_data(as_text=True)
    values = {}
    for tag in re.findall(r'<input[^>]*name="(?:approved_qty|note)_\d+"[^>]*>', body):
        name = re.search(r'name="([^"]+)"', tag).group(1)
        value = re.search(r'\svalue="([^"]*)"', tag)
        values[name] = value.group(1) if value else ""
    return values


def _latest_changes():
    rows = sorted(_rows("supply_finalized"), key=lambda r: r.id)
    return {c["line_number"]: c for c in json.loads(rows[-1].context_json)["changes"]}


def _pre_deploy_reopened_order(client, data):
    """Finalized with a cut and a zero, as orders decided before quantity_reviewed existed."""
    env = _ready(client, data, quantities=(8, 5))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="8")
    _reviewer_decides(client, env, 2, "approve", approved_quantity="5")
    _finalize(client, env, approved_qty_1="7", note_1="short on stock",
              approved_qty_2="0", note_2="none left")
    for number in (1, 2):
        _line(env, number).supply_detail.quantity_reviewed = None
    db.session.commit()
    _reopen(client, env)
    assert _order(env).status == WORK_ITEM_STATUS_SUBMITTED
    return env


def _assert_pre_deploy_decisions_kept(env):
    line1, line2 = _line(env, 1), _line(env, 2)
    assert line1.supply_detail.quantity_approved == 7 and line1.status == "APPROVED"
    assert line2.supply_detail.quantity_approved == 0 and line2.status == "REJECTED"
    assert _admin_review(line1).note == "short on stock"
    assert _admin_review(line2).note == "none left"
    changes = _latest_changes()
    assert (changes[1]["kind"], changes[1]["final"]) == ("cut", 7)
    assert (changes[2]["kind"], changes[2]["final"]) == ("cut", 0)


def test_reopened_pre_deploy_order_prefills_admin_decisions(app, client, seed_workflow_data):
    env = _pre_deploy_reopened_order(client, seed_workflow_data)
    shown = _finalize_inputs(client, env)
    assert shown == {
        "approved_qty_1": "7", "note_1": "short on stock",
        "approved_qty_2": "0", "note_2": "none left",
    }
    _finalize(client, env, **shown)
    _assert_pre_deploy_decisions_kept(env)


def test_reopened_pre_deploy_order_finalized_with_no_fields_keeps_decisions(
        app, client, seed_workflow_data):
    env = _pre_deploy_reopened_order(client, seed_workflow_data)
    _finalize(client, env)
    _assert_pre_deploy_decisions_kept(env)


def test_reopened_order_refinalized_as_shown_keeps_admin_quantity(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(12,))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="10")
    _finalize(client, env, approved_qty_1="8")
    _reopen(client, env)
    shown = _finalize_inputs(client, env)
    assert shown == {"approved_qty_1": "8", "note_1": ""}
    _finalize(client, env, **shown)
    assert _line(env, 1).supply_detail.quantity_approved == 8
    assert (_latest_changes()[1]["kind"], _latest_changes()[1]["final"]) == ("cut", 8)


def test_reopened_closed_order_prefills_from_review_group(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(12,))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="10")
    _finalize(client, env, approved_qty_1="8", note_1="tight budget")
    _reopen(client, env)
    _close(client, env)
    _reopen(client, env)
    # The admin decision was reset with the close, so the group decides again.
    assert _admin_review(_line(env, 1)).status == "PENDING"
    assert _finalize_inputs(client, env) == {"approved_qty_1": "12", "note_1": ""}
    _reviewer_decides(client, env, 1, "approve", approved_quantity="9")
    assert _finalize_inputs(client, env) == {"approved_qty_1": "9", "note_1": ""}


def test_line_added_then_changed_at_finalize_prefills_new_quantity(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _add(client, env, env.board, 3, return_to="finalize")
    _change(client, env, 2, 5, return_to="finalize")
    line = _line(env, 2)
    admin = _admin_review(line)
    assert line.supply_detail.quantity_approved == 5
    assert admin.status == "APPROVED" and admin.approved_amount_cents == 5 * 200
    assert line.approved_amount_cents == 5 * 200
    assert _finalize_inputs(client, env)["approved_qty_2"] == "5"
    _finalize(client, env)
    assert _line(env, 2).supply_detail.quantity_approved == 5


def test_reopened_cut_line_changed_at_finalize_prefills_new_quantity(
        app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(8,))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="8")
    _finalize(client, env, approved_qty_1="7", note_1="short on stock")
    _reopen(client, env)
    _change(client, env, 1, 10, return_to="finalize")
    admin = _admin_review(_line(env, 1))
    assert admin.status == "APPROVED" and admin.note == "short on stock"
    assert admin.decided_by_user_id == "test:admin"
    assert _finalize_inputs(client, env) == {"approved_qty_1": "10", "note_1": "short on stock"}


def test_rejected_line_changed_at_finalize_still_prefills_zero(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(8,))
    _finalize(client, env, approved_qty_1="0", note_1="none left")
    _reopen(client, env)
    _change(client, env, 1, 10, return_to="finalize")
    line = _line(env, 1)
    assert _admin_review(line).status == "REJECTED" and line.supply_detail.quantity_approved == 0
    assert _finalize_inputs(client, env)["approved_qty_1"] == "0"
