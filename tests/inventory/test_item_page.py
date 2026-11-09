import re

from app import db
from app.inventory.ledger import Actor, add_stock
from tests.inventory.helpers import login, make_container


def _has_tab(page, label):
    """The tab macro puts the label on its own line inside the button."""
    return re.search(rf">\s*{label}\s*<", page) is not None


def test_inventory_admin_sees_inventory_tabs_for_a_tracked_item(client, inv):
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/items/{inv.laptop.id}").get_data(as_text=True)
    assert "Tracked as assets" in page and _has_tab(page, "Inventory") and _has_tab(page, "Assets")
    assert not _has_tab(page, "Ordering")


def test_supply_admin_sees_ordering_only(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    page = client.get(f"/inv/admin/items/{inv.laptop.id}").get_data(as_text=True)
    assert _has_tab(page, "Ordering") and not _has_tab(page, "Inventory") and not _has_tab(page, "Assets")


def test_counted_item_shows_on_hand_not_assets(client, inv):
    login(client, "test:operator")
    page = client.get(f"/inv/admin/items/{inv.pens.id}").get_data(as_text=True)
    assert _has_tab(page, "On hand") and not _has_tab(page, "Assets") and "Counted" in page


def test_not_orderable_item_hides_ordering_even_for_supply_admin(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    page = client.get(f"/inv/admin/items/{inv.wap.id}").get_data(as_text=True)
    assert "Not orderable" in page and not _has_tab(page, "Ordering")


def test_nobody_gets_403(client, inv):
    login(client, "test:nobody")
    assert client.get(f"/inv/admin/items/{inv.pens.id}").status_code == 403
    assert client.get("/inv/admin/items").status_code == 403


def test_items_list_links_rows_and_opens_to_supply_admins(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    page = client.get("/inv/admin/items").get_data(as_text=True)
    assert f'/inv/admin/items/{inv.pens.id}"' in page


def test_missing_item_is_404(client, inv):
    login(client, "test:invadmin")
    assert client.get("/inv/admin/items/99999").status_code == 404


def test_header_shows_on_hand_total_for_counted_item(client, inv):
    add_stock(inv.pens, make_container(inv, "C-0001", kind="BIN"), 7,
              Actor(user_id="test:invadmin"))
    db.session.commit()
    login(client, "test:operator")
    page = client.get(f"/inv/admin/items/{inv.pens.id}").get_data(as_text=True)
    assert "7 on hand" in page and "Orderable" in page


def test_supply_admin_header_has_no_stock_figure(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    page = client.get(f"/inv/admin/items/{inv.laptop.id}").get_data(as_text=True)
    assert "on hand" not in page and "asset" not in page.split("<h1")[1].split("</h1>")[0]


def test_items_list_defaults_to_active_and_can_show_inactive(client, inv):
    inv.pens.is_active = False
    db.session.commit()
    login(client, "test:invadmin")
    active = client.get("/inv/admin/items").get_data(as_text=True)
    assert "Blue Pen" not in active and "Laptop" in active
    assert "show=inactive" in active
    inactive = client.get("/inv/admin/items?show=inactive").get_data(as_text=True)
    assert "Blue Pen" in inactive and "Laptop" not in inactive


def test_about_tab_shows_both_switch_states_as_text(client, inv):
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/items/{inv.wap.id}").get_data(as_text=True)
    assert "Departments can order this" in page and "Track each piece as an asset" in page
