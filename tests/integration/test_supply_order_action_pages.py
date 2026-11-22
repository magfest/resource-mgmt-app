"""Supply order actions open on their own pages and return to where they started."""
import re

from app import db
from app.models import WorkLine
from tests.integration.supply_helpers import login
from tests.integration.test_supply_admin_lines import (
    _add, _change, _env, _flashes, _member, _remove,
)


def test_add_page_renders_for_admin_with_origin(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    body = client.get(f"{env.admin_url}/lines/add?return_to=finalize").get_data(as_text=True)
    assert 'name="return_to" value="finalize"' in body
    assert f'href="{env.admin_url}/finalize"' in body
    assert "Foam Board" in body and 'class="form-narrow' in body


def test_add_page_ignores_a_url_in_return_to(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    body = client.get(
        f"{env.admin_url}/lines/add?return_to=https://evil.example").get_data(as_text=True)
    assert 'name="return_to" value="order"' in body
    assert "evil.example" not in body and f'href="{env.order_url}"' in body


def test_add_page_refuses_non_admins(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _member(env, seed_workflow_data)
    for user in ("test:reviewer", "test:member"):
        login(client, user)
        assert client.get(f"{env.admin_url}/lines/add").status_code == 403


def test_add_page_on_a_finalized_order_returns_with_a_message(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert client.post(f"{env.admin_url}/finalize", data={}).status_code == 302
    resp = client.get(f"{env.admin_url}/lines/add")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(env.order_url)
    assert any("submitted" in m.lower() for m in _flashes(client))


def test_add_refusal_rerenders_the_form_with_typed_values(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = _add(client, env, env.board, 0, notes="for the info desk")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Enter a quantity of at least 1." in body
    assert "for the info desk" in body
    assert re.search(rf'<option value="{env.board.id}"[^>]*\bselected\b', body)
    assert WorkLine.query.filter_by(work_item_id=env.order.id).count() == 1


def test_stale_add_after_finalize_rerenders_and_writes_nothing(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert client.post(f"{env.admin_url}/finalize", data={}).status_code == 302
    resp = _add(client, env, env.board, 2)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "Only submitted orders can be edited." in body
    db.session.expire_all()
    assert WorkLine.query.filter_by(work_item_id=env.order.id).count() == 1


def test_close_page_renders_consequence_and_confirm(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    body = client.get(f"{env.admin_url}/close?return_to=finalize").get_data(as_text=True)
    assert "Every line is rejected and the department is emailed this reason." in body
    assert "data-confirm-submit" in body and f'action="{env.admin_url}/close"' in body
    assert 'name="return_to" value="finalize"' in body
    assert f'href="{env.admin_url}/finalize"' in body
    assert not re.search(r"<form\s[^>]*\bdata-confirm=", body)


def test_reopen_page_only_for_finalized_orders(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = client.get(f"{env.admin_url}/reopen")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(env.order_url)
    assert client.post(f"{env.admin_url}/finalize", data={}).status_code == 302
    resp = client.get(f"{env.admin_url}/reopen")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Line decisions stay as they are." in body
    assert f'action="{env.admin_url}/reopen"' in body
    assert "data-confirm-submit" in body
    assert not re.search(r"<form\s[^>]*\bdata-confirm=", body)


def test_reopen_page_for_a_closed_order_uses_the_closed_copy(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    client.post(f"{env.admin_url}/close", data={"reason": "Placed by mistake"})
    body = client.get(f"{env.admin_url}/reopen").get_data(as_text=True)
    assert "Every line goes back to its review group." in body


def test_close_page_on_a_finalized_order_returns_with_a_message(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert client.post(f"{env.admin_url}/finalize", data={}).status_code == 302
    resp = client.get(f"{env.admin_url}/close")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(env.order_url)


def test_close_and_reopen_pages_refuse_non_admins(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _member(env, seed_workflow_data)
    for user in ("test:reviewer", "test:member"):
        login(client, user)
        assert client.get(f"{env.admin_url}/close").status_code == 403
        assert client.get(f"{env.admin_url}/reopen").status_code == 403


def test_close_page_ignores_a_url_in_return_to(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    body = client.get(
        f"{env.admin_url}/close?return_to=https://evil.example").get_data(as_text=True)
    assert 'name="return_to" value="order"' in body and "evil.example" not in body


def test_close_refusal_keeps_the_typed_reason(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    long_reason = "x" * 1001
    resp = client.post(f"{env.admin_url}/close", data={"reason": long_reason})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "1000 characters or fewer" in body
    assert long_reason in body
    db.session.expire_all()
    assert env.order.status == "SUBMITTED"


def test_reopen_refusal_keeps_the_typed_reason(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert client.post(f"{env.admin_url}/finalize", data={}).status_code == 302
    long_reason = "y" * 1001
    resp = client.post(f"{env.admin_url}/reopen", data={"reason": long_reason})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "1000 characters or fewer" in body
    assert long_reason in body
    db.session.expire_all()
    assert env.order.status == "FINALIZED"


def test_admin_sees_change_card_with_origin(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    body = client.get(f"{env.line_url}/1/review?return_to=finalize").get_data(as_text=True)
    assert f'action="{env.admin_url}/lines/1/quantity"' in body
    assert f'action="{env.admin_url}/lines/1/remove"' in body
    assert body.count('name="return_to" value="finalize"') == 2
    assert "Changes here skip the review group." in body


def test_change_card_defaults_to_the_order_origin(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    body = client.get(f"{env.line_url}/1/review?return_to=https://evil.example").get_data(as_text=True)
    assert body.count('name="return_to" value="order"') == 2
    assert "evil.example" not in body
    assert ("Quantity changes here go back to the review group. "
            "The department sees every change and its reason.") in body


def test_line_page_offers_back_to_finalize_only_from_finalize(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    body = client.get(f"{env.line_url}/1/review?return_to=finalize").get_data(as_text=True)
    assert f'href="{env.admin_url}/finalize"' in body and "Back to Finalize" in body
    body = client.get(f"{env.line_url}/1/review").get_data(as_text=True)
    assert "Back to Finalize" not in body


def test_order_page_hints_where_to_change_a_line_for_admins_only(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    _member(env, seed_workflow_data)
    hint = "To change or remove a line, open it with Review."
    assert hint in client.get(env.order_url).get_data(as_text=True)
    for user in ("test:reviewer", "test:member"):
        login(client, user)
        assert hint not in client.get(env.order_url).get_data(as_text=True)


def test_no_change_card_for_reviewer_removed_line_closed_or_finalized_order(
        app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data, quantities=(100, 10))
    url = f"{env.line_url}/1/review"
    login(client, "test:reviewer")
    assert "/lines/1/quantity" not in client.get(url).get_data(as_text=True)
    login(client, "test:admin")
    assert "/lines/1/quantity" in client.get(url).get_data(as_text=True)

    assert _remove(client, env, 1).status_code == 302
    assert "/lines/1/quantity" not in client.get(url).get_data(as_text=True)
    assert "/lines/2/quantity" in client.get(f"{env.line_url}/2/review").get_data(as_text=True)

    assert client.post(f"{env.admin_url}/close", data={"reason": "Placed by mistake"}).status_code == 302
    assert "/lines/2/quantity" not in client.get(f"{env.line_url}/2/review").get_data(as_text=True)


def test_no_change_card_on_a_finalized_order(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    assert client.post(f"{env.admin_url}/finalize", data={}).status_code == 302
    body = client.get(f"{env.line_url}/1/review").get_data(as_text=True)
    assert "/lines/1/quantity" not in body


def test_line_page_refusal_returns_to_line_page_keeping_origin(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = client.post(f"{env.admin_url}/lines/1/quantity",
                       data={"quantity": "5", "reason": "", "return_to": "finalize"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"{env.line_url}/1/review?return_to=finalize")
    assert "Give a reason; the department sees it." in _flashes(client)


def test_line_page_refusal_from_the_order_origin_has_no_query(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = client.post(f"{env.admin_url}/lines/1/remove",
                       data={"reason": "", "return_to": "order"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"{env.line_url}/1/review")


def test_line_page_change_success_returns_to_origin(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = _change(client, env, 1, 50, return_to="finalize")
    assert resp.headers["Location"].endswith(f"{env.admin_url}/finalize")
