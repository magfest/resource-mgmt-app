"""Supply finalized and closed email: change summary, enqueue, template rows."""
import json
from datetime import datetime, timezone

from app import db
from app.models import (
    EmailOutbox,
    EmailTemplate,
    WorkItem,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.supply.line_state import build_finalize_changes
from app.services.email_templates import render_email_template
from app.services.notifications import (
    notify_needs_attention,
    notify_response_received,
    notify_supply_closed,
)
from migrations.versions.sm7p2c9x4f6b_supply_finalized_closed_email import (
    FINALIZED_BODY_NEW,
    FINALIZED_BODY_OLD,
    downgrade_templates,
    seed_closed_template,
    update_finalized_template,
)
from migrations.versions.ws8841d5f27c_techops_supply_email_templates import (
    seed_work_type_templates,
)
from tests.integration.supply_helpers import login
from tests.integration.test_supply_admin_lines import (
    _add, _change, _env, _line, _member, _remove, _reviewer_decides,
)


def _seed_templates():
    conn = db.session.connection()
    seed_work_type_templates(conn)
    update_finalized_template(conn)
    seed_closed_template(conn)
    db.session.commit()


def _finalize(client, env, **data):
    resp = client.post(f"{env.admin_url}/finalize", data=data)
    assert resp.status_code == 302
    db.session.expire_all()
    return resp


def _rows(key):
    return EmailOutbox.query.filter_by(template_key=key).all()


def _changes(row):
    return json.loads(row.context_json)["changes"]


def _ready(client, data, quantities=(100,)):
    env = _env(client, data, quantities=quantities)
    _seed_templates()
    _member(env, data)
    return env


def test_finalize_queues_supply_finalized_once_per_member(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _finalize(client, env)
    rows = _rows("supply_finalized")
    assert [r.recipient_email for r in rows] == ["member@test.local"]


def test_finalized_without_changes_has_empty_changes(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _finalize(client, env)
    assert _changes(_rows("supply_finalized")[0]) == []


def test_finalized_with_changes_lists_cut_added_removed_and_changed(
        app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(100, 5, 10))
    _change(client, env, 1, 150, reason="more events")
    _remove(client, env, 2, reason="using venue boards")
    _add(client, env, env.board, 3, return_to="finalize")
    _finalize(client, env, **{"approved_qty_3": "7"})
    changes = {c["line_number"]: c for c in _changes(_rows("supply_finalized")[0])}
    assert changes[1]["kind"] == "requested_changed"
    assert changes[1]["department_quantity"] == 100 and changes[1]["requested"] == 150
    assert changes[1]["reason"] == "more events"
    assert changes[2]["kind"] == "removed" and changes[2]["reason"] == "using venue boards"
    assert changes[3]["kind"] == "cut" and changes[3]["final"] == 7
    assert changes[4]["kind"] == "added" and changes[4]["final"] == 3
    assert changes[4]["item_name"] == "Foam Board" and changes[4]["unit"] == "each"
    assert sorted(changes) == [1, 2, 3, 4]


def test_raise_and_cut_compare_against_current_request(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(100, 50))
    _change(client, env, 1, 150)
    _finalize(client, env, **{"approved_qty_1": "150", "approved_qty_2": "60"})
    changes = {c["line_number"]: c for c in _changes(_rows("supply_finalized")[0])}
    assert changes[1]["kind"] == "requested_changed"
    assert changes[2]["kind"] == "raised" and changes[2]["final"] == 60


def test_rejected_line_booked_at_zero_is_a_cut(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(10, 4))
    _reviewer_decides(client, env, 1, "reject", note="not needed")
    _finalize(client, env)
    changes = _changes(_rows("supply_finalized")[0])
    assert [(c["line_number"], c["kind"], c["final"]) for c in changes] == [(1, "cut", 0)]


def test_finalize_succeeds_without_department_members(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _seed_templates()
    _finalize(client, env)
    assert WorkItem.query.get(env.order.id).status == WORK_ITEM_STATUS_FINALIZED
    assert _rows("supply_finalized") == []


def test_refinalize_after_reopen_queues_second_email(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    _finalize(client, env)
    client.post(f"{env.admin_url}/reopen", data={"reason": "One more item"})
    db.session.expire_all()
    assert WorkItem.query.get(env.order.id).status == WORK_ITEM_STATUS_SUBMITTED
    _finalize(client, env)
    assert len(_rows("supply_finalized")) == 2


def test_rendered_finalized_body_shows_what_changed_only_when_changes(
        app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(100, 5))
    _remove(client, env, 2, reason="using venue boards")
    _finalize(client, env)
    item = WorkItem.query.get(env.order.id)
    base = {"work_item": item, "base_url": "https://x.test"}
    with_changes = render_email_template(
        "supply_finalized", {**base, "changes": _changes(_rows("supply_finalized")[0])}).body_text
    assert "What changed from your request:" in with_changes
    assert "Line 2, Sharpie Markers: removed. using venue boards" in with_changes
    assert with_changes.index("What changed") < with_changes.index("View the request:")
    without = render_email_template("supply_finalized", {**base, "changes": []}).body_text
    assert "What changed" not in without
    assert "Your supply order has been finalized." in without


def test_close_queues_supply_closed_with_reason(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    item = WorkItem.query.get(env.order.id)
    closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    assert notify_supply_closed(item, "Event cancelled", closed_at) == 1
    db.session.commit()
    row = _rows("supply_closed")[0]
    assert json.loads(row.context_json)["reason"] == "Event cancelled"
    # A second close at a later time is a new event, not a duplicate.
    assert notify_supply_closed(item, "Again", datetime.utcnow()) == 1
    rendered = render_email_template(
        "supply_closed", {"work_item": item, "base_url": "https://x.test", "reason": "Event cancelled"})
    assert "Reason: Event cancelled" in rendered.body_text


def test_needs_attention_still_queues_for_supply(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    assert notify_needs_attention(WorkItem.query.get(env.order.id)) == 1
    db.session.commit()
    assert len(_rows("supply_needs_attention")) == 1


def test_response_received_still_queues_for_supply(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data)
    item = WorkItem.query.get(env.order.id)
    assert notify_response_received(item, "test:reviewer") == 1
    db.session.commit()
    rows = _rows("supply_response_received")
    assert [r.recipient_email for r in rows] == ["reviewer@test.local"]


def test_build_finalize_changes_is_json_safe_and_ordered(app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(5, 5))
    _remove(client, env, 2)
    _remove(client, env, 1)
    changes = build_finalize_changes(WorkItem.query.get(env.order.id))
    assert [c["line_number"] for c in changes] == [1, 2]
    json.dumps(changes)


# Template rows

def _template(key):
    return EmailTemplate.query.filter_by(template_key=key).one()


def test_migration_updates_untouched_finalized_body(app):
    seed_work_type_templates(db.session.connection())
    db.session.commit()
    assert _template("supply_finalized").body_text == FINALIZED_BODY_OLD
    version = _template("supply_finalized").version
    update_finalized_template(db.session.connection())
    db.session.commit()
    db.session.expire_all()
    assert _template("supply_finalized").body_text == FINALIZED_BODY_NEW
    # A new version marks per-event overrides of the old body as stale.
    assert _template("supply_finalized").version == version + 1


def test_migration_inserts_finalized_when_absent(app):
    update_finalized_template(db.session.connection())
    db.session.commit()
    row = _template("supply_finalized")
    assert row.body_text == FINALIZED_BODY_NEW and row.is_active is True


def test_migration_leaves_edited_finalized_body_alone(app):
    seed_work_type_templates(db.session.connection())
    row = _template("supply_finalized")
    row.body_text = "Edited wording"
    db.session.commit()
    update_finalized_template(db.session.connection())
    db.session.commit()
    assert _template("supply_finalized").body_text == "Edited wording"


def test_migration_inserts_closed_once(app):
    seed_closed_template(db.session.connection())
    seed_closed_template(db.session.connection())
    db.session.commit()
    assert EmailTemplate.query.filter_by(template_key="supply_closed").count() == 1
    assert _template("supply_closed").subject == (
        "[MAGFest Supply] Order closed - {{ work_item.public_id }}")


def test_migration_keeps_edited_closed_template(app):
    seed_closed_template(db.session.connection())
    _template("supply_closed").body_text = "Edited"
    db.session.commit()
    seed_closed_template(db.session.connection())
    db.session.commit()
    assert _template("supply_closed").body_text == "Edited"


def test_downgrade_restores_only_untouched_rows(app):
    seed_work_type_templates(db.session.connection())
    update_finalized_template(db.session.connection())
    seed_closed_template(db.session.connection())
    db.session.commit()
    version = _template("supply_finalized").version
    downgrade_templates(db.session.connection())
    db.session.commit()
    db.session.expire_all()
    assert _template("supply_finalized").body_text == FINALIZED_BODY_OLD
    assert _template("supply_finalized").version == version + 1
    assert EmailTemplate.query.filter_by(template_key="supply_closed").count() == 0

    update_finalized_template(db.session.connection())
    seed_closed_template(db.session.connection())
    _template("supply_finalized").body_text = "Edited"
    _template("supply_closed").body_text = "Edited"
    db.session.commit()
    downgrade_templates(db.session.connection())
    db.session.commit()
    assert _template("supply_finalized").body_text == "Edited"
    assert _template("supply_closed").body_text == "Edited"


# Removed lines carry no reviewer quantity label

def test_removed_line_shows_no_approved_label_on_order_and_finalize(
        app, client, seed_workflow_data):
    env = _ready(client, seed_workflow_data, quantities=(10,))
    _reviewer_decides(client, env, 1, "approve", approved_quantity="6")
    login(client, "test:admin")
    assert "Approved 6 of 10" in client.get(env.order_url).get_data(as_text=True)
    _remove(client, env, 1)
    assert "Approved 6 of 10" not in client.get(env.order_url).get_data(as_text=True)
    assert "Approved 6 of 10" not in client.get(
        f"{env.admin_url}/finalize").get_data(as_text=True)
