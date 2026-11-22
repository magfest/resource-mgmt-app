"""Supply review pages opt into the phone card layout.

The layout is CSS in layout/base.html and is checked by eye at 390px. These
pin the markup it depends on, so a later edit cannot silently drop it.
"""
import re
from types import SimpleNamespace

from tests.integration.supply_helpers import (
    add_line, login, make_draft_order, seed_approval_group, seed_category,
    seed_item, seed_reviewer, seed_supply, set_pickup_details,
)


def _stacked(html, label):
    return re.search(rf'<td[^>]*class="[^"]*cell-stack[^"]*"[^>]*data-label="{label}"', html) or \
        re.search(rf'<td[^>]*data-label="{label}"[^>]*class="[^"]*cell-stack', html)


def _env(client, data):
    wt = seed_supply(data)
    gen = seed_approval_group(wt, "SUPPLY_GEN")
    category = seed_category(gen, "OFFICE")
    sharpie = seed_item(category, "Sharpie Markers", unit_cost_cents=500)
    board = seed_item(category, "Foam Board", unit_cost_cents=200)
    cycle, dept = data["cycle"], data["department"]
    order = make_draft_order(wt, cycle, dept)
    add_line(order, sharpie, quantity=4)
    add_line(order, board, quantity=2)
    set_pickup_details(order)
    seed_reviewer(data, gen)
    login(client, "test:admin")
    assert client.post(
        f"/{cycle.code}/{dept.code}/supply/order/{order.public_id}/submit").status_code == 302
    return SimpleNamespace(
        order_url=f"/{cycle.code}/{dept.code}/supply/order/{order.public_id}",
        quick_url=f"/{cycle.code}/{dept.code}/supply/item/{order.public_id}/quick-review",
        admin_url=f"/admin/supply/order/{order.public_id}",
    )


def test_quick_review_table_becomes_cards(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)

    html = client.get(env.quick_url).get_data(as_text=True)

    assert re.search(r'<table[^>]*class="responsive-cards"', html)
    for label in ("Line", "Item", "Qty", "Cost", "Status"):
        assert f'data-label="{label}"' in html
    assert _stacked(html, "Notes")
    assert _stacked(html, "Actions")


def test_removed_line_labels_sit_in_a_stacked_cell(app, client, seed_workflow_data):
    env = _env(client, seed_workflow_data)
    resp = client.post(f"{env.admin_url}/lines/2/remove",
                       data={"reason": "using venue boards", "return_to": "order"})
    assert resp.status_code == 302

    html = client.get(env.order_url).get_data(as_text=True)

    assert re.search(r'<table[^>]*class="responsive-cards"', html)
    row = re.search(r'<tr id="line-2".*?</tr>', html, re.S).group(0)
    assert _stacked(row, "Item")
    assert "Removed by Supply admin" in row
    assert "using venue boards" in row
