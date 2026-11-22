import io
import re

import pytest

from app import db
from app.models import ConfigAuditEvent, InvUnit, SupplyItem
from app.inventory.ledger import Actor, add_stock
from tests.inventory.helpers import login, make_allocation, make_asset, make_container, supply_line


def _has_tab(page, label):
    """Match the label inside a tab button only; the top nav has an Inventory heading too."""
    return re.search(rf'role="tab"[^>]*>\s*{label}\s*<', page) is not None


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


def _about(client, item, **over):
    data = {"item_name": item.item_name, "category_id": item.category_id, "unit": "each",
            "is_active": "1"}
    data.update(over)
    return client.post(f"/inv/admin/items/{item.id}/about", data=data,
                       content_type="multipart/form-data")


def test_supply_admin_saving_about_leaves_tracking_alone(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    _about(client, inv.laptop, is_orderable="1")
    assert db.session.get(SupplyItem, inv.laptop.id).is_serialized is True


def test_inventory_admin_saving_about_leaves_ordering_alone(client, inv):
    login(client, "test:invadmin")
    _about(client, inv.pens, item_name="Blue Pen 2")
    item = db.session.get(SupplyItem, inv.pens.id)
    assert item.is_orderable is True and item.item_name == "Blue Pen 2"


def test_supply_admin_unticked_orderable_means_off(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    _about(client, inv.pens)
    assert db.session.get(SupplyItem, inv.pens.id).is_orderable is False


def test_supply_admin_cannot_post_the_tracking_switch(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    resp = _about(client, inv.pens, item_name="Changed", is_serialized="1")
    assert resp.status_code == 403
    item = db.session.get(SupplyItem, inv.pens.id)
    assert item.is_serialized is False and item.item_name == "Blue Pen"


def test_inventory_admin_cannot_post_the_ordering_switch(client, inv):
    login(client, "test:invadmin")
    assert _about(client, inv.pens, is_orderable="1").status_code == 403


def test_operator_cannot_save_about(client, inv):
    login(client, "test:operator")
    assert client.post(f"/inv/admin/items/{inv.pens.id}/about",
                       data={"item_name": "X"}).status_code == 403


def test_tracking_refused_while_stock_is_counted(client, inv):
    add_stock(inv.pens, make_container(inv, "C-0001", kind="BIN"), 4,
              Actor(user_id="test:invadmin"))
    db.session.commit()
    login(client, "test:invadmin")
    resp = _about(client, inv.pens, item_name="Renamed", is_serialized="1")
    assert resp.headers["Location"].endswith(f"/inv/admin/items/{inv.pens.id}?tab=about")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Count it to zero before tracking each piece as an asset." in page
    item = db.session.get(SupplyItem, inv.pens.id)
    assert item.is_serialized is False and item.item_name == "Blue Pen"


def test_untracking_refused_while_assets_exist(client, inv):
    db.session.add(InvUnit(item_id=inv.laptop.id))
    db.session.commit()
    login(client, "test:invadmin")
    resp = _about(client, inv.laptop)
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "must stay tracked as assets." in page
    assert db.session.get(SupplyItem, inv.laptop.id).is_serialized is True


def test_validation_error_saves_nothing_and_skips_the_picture(client, inv, monkeypatch):
    from app.services import images
    calls = []
    monkeypatch.setattr(images, "process_and_upload_item_image",
                        lambda f, i: calls.append(i) or "https://x/y.png")
    login(client, "test:invadmin")
    resp = _about(client, inv.pens, unit="", image_file=(io.BytesIO(b"x"), "p.png"))
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Item name, category, and unit of measure are required." in page
    assert calls == [] and db.session.get(SupplyItem, inv.pens.id).image_url is None


def test_about_writes_an_audit_row(client, inv):
    login(client, "test:invadmin")
    _about(client, inv.pens, item_name="Black Pen")
    row = ConfigAuditEvent.query.filter_by(entity_type="supply_item", entity_id=inv.pens.id).first()
    assert row is not None


def test_about_picture_upload_sets_url(client, inv, monkeypatch):
    from app.services import images
    monkeypatch.setattr(images, "process_and_upload_item_image",
                        lambda f, i: "https://bucket/pen.png")
    login(client, "test:invadmin")
    _about(client, inv.pens, image_file=(io.BytesIO(b"x"), "p.png"))
    assert db.session.get(SupplyItem, inv.pens.id).image_url == "https://bucket/pen.png"


def test_about_picture_remove_clears_url_and_deletes(client, inv, monkeypatch):
    from app.services import images
    deleted = []
    monkeypatch.setattr(images, "delete_item_image", deleted.append)
    inv.pens.image_url = "https://bucket/old.png"
    db.session.commit()
    login(client, "test:invadmin")
    _about(client, inv.pens, remove_image="1")
    assert db.session.get(SupplyItem, inv.pens.id).image_url is None
    assert deleted == ["https://bucket/old.png"]


def test_about_bad_picture_flashes_and_keeps_fields(client, inv, monkeypatch):
    from app.services import images

    def _raise(f, i):
        raise images.ImageValidationError("too big")
    monkeypatch.setattr(images, "process_and_upload_item_image", _raise)
    login(client, "test:invadmin")
    resp = _about(client, inv.pens, item_name="Red Pen", image_file=(io.BytesIO(b"x"), "p.png"))
    assert "too big" in client.get(resp.headers["Location"]).get_data(as_text=True)
    item = db.session.get(SupplyItem, inv.pens.id)
    assert item.item_name == "Red Pen" and item.image_url is None


def test_about_without_touching_picture_keeps_it(client, inv):
    inv.pens.image_url = "https://bucket/keep.png"
    db.session.commit()
    login(client, "test:invadmin")
    _about(client, inv.pens, item_name="Keep Pen")
    assert db.session.get(SupplyItem, inv.pens.id).image_url == "https://bucket/keep.png"


def test_supply_admin_adds_an_untracked_orderable_item(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    resp = client.post("/inv/admin/items/new",
                       data={"item_name": "Gaffer Tape", "category_id": inv.category.id,
                             "unit": "roll", "is_orderable": "1"})
    item = db.session.query(SupplyItem).filter_by(item_name="Gaffer Tape").one()
    assert item.is_orderable and not item.is_serialized and item.is_expendable
    assert resp.headers["Location"].endswith(f"/inv/admin/items/{item.id}")


def test_supply_admin_adding_without_the_box_gets_a_not_orderable_item(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    client.post("/inv/admin/items/new",
                data={"item_name": "Gaffer Tape", "category_id": inv.category.id, "unit": "roll"})
    item = db.session.query(SupplyItem).filter_by(item_name="Gaffer Tape").one()
    assert item.is_orderable is False


def test_add_an_item_orderable_box_starts_unticked(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    page = client.get("/inv/admin/items").get_data(as_text=True)
    box = re.search(r'<input[^>]*name="is_orderable"[^>]*>', page)
    assert box is not None and "checked" not in box.group(0)


def test_operator_cannot_add_an_item(client, inv):
    login(client, "test:operator")
    resp = client.post("/inv/admin/items/new",
                       data={"item_name": "X", "category_id": inv.category.id, "unit": "each"})
    assert resp.status_code == 403


def test_supply_admin_cannot_add_a_tracked_item(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    resp = client.post("/inv/admin/items/new",
                       data={"item_name": "X", "category_id": inv.category.id,
                             "unit": "each", "is_serialized": "1"})
    assert resp.status_code == 403


def test_supply_admin_saves_ordering_fields(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    resp = client.post(f"/inv/admin/items/{inv.pens.id}/ordering",
                       data={"unit_cost": "3.50", "order_guidance": "One box per desk",
                             "is_popular": "1"})
    pens = db.session.get(SupplyItem, inv.pens.id)
    assert pens.unit_cost_cents == 350 and pens.order_guidance == "One box per desk"
    assert pens.is_popular and not pens.is_limited and not pens.is_expendable
    assert resp.headers["Location"].endswith(f"/inv/admin/items/{inv.pens.id}?tab=ordering")


def test_ordering_rejects_a_bad_unit_cost_and_saves_nothing(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    resp = client.post(f"/inv/admin/items/{inv.pens.id}/ordering",
                       data={"unit_cost": "abc", "order_guidance": "Changed"})
    pens = db.session.get(SupplyItem, inv.pens.id)
    assert pens.order_guidance is None and pens.unit_cost_cents is None
    assert resp.headers["Location"].endswith("tab=ordering")


def test_ordering_fields_survive_orderable_off_and_on(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    client.post(f"/inv/admin/items/{inv.pens.id}/ordering",
                data={"unit_cost": "3.50", "order_guidance": "One box per desk"})
    about = {"item_name": "Blue Pen", "category_id": inv.category.id, "unit": "each",
             "is_active": "1"}
    client.post(f"/inv/admin/items/{inv.pens.id}/about", data=about)
    assert not db.session.get(SupplyItem, inv.pens.id).is_orderable
    client.post(f"/inv/admin/items/{inv.pens.id}/about", data={**about, "is_orderable": "1"})
    pens = db.session.get(SupplyItem, inv.pens.id)
    assert pens.is_orderable and pens.unit_cost_cents == 350
    assert pens.order_guidance == "One box per desk"


def test_inventory_admin_cannot_post_ordering(client, inv):
    login(client, "test:invadmin")
    assert client.post(f"/inv/admin/items/{inv.pens.id}/ordering",
                       data={"unit_cost": "9"}).status_code == 403
    assert db.session.get(SupplyItem, inv.pens.id).unit_cost_cents is None


def test_ordering_tab_shows_the_form_to_supply_admin(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    page = client.get(f"/inv/admin/items/{inv.pens.id}?tab=ordering").get_data(as_text=True)
    assert f"/inv/admin/items/{inv.pens.id}/ordering" in page


def test_inventory_admin_saves_pack_size_on_the_on_hand_tab(client, inv):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/items/{inv.pens.id}/on-hand", data={"pack_size": "12", "barcode": "PEN-12"})
    db.session.expire_all()
    pens = db.session.get(SupplyItem, inv.pens.id)
    assert pens.pack_size == 12 and pens.barcode == "PEN-12"


def test_inventory_admin_saves_asset_tag_and_barcode_on_the_inventory_tab(client, inv):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/items/{inv.laptop.id}/inventory",
                data={"needs_asset_tag": "1", "barcode": "LAP-1"})
    db.session.expire_all()
    laptop = db.session.get(SupplyItem, inv.laptop.id)
    assert laptop.needs_asset_tag and laptop.barcode == "LAP-1"
    assert ConfigAuditEvent.query.filter_by(entity_type="supply_item", entity_id=laptop.id).count()


def test_bad_pack_size_changes_nothing(client, inv):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/items/{inv.pens.id}/on-hand", data={"pack_size": "x", "barcode": "PEN-9"})
    db.session.expire_all()
    assert db.session.get(SupplyItem, inv.pens.id).barcode is None


def test_supply_admin_cannot_post_inventory(client, inv, supply_admin):
    login(client, "test:supplyadmin")
    assert client.post(f"/inv/admin/items/{inv.laptop.id}/inventory",
                       data={"needs_asset_tag": "1"}).status_code == 403
    assert client.post(f"/inv/admin/items/{inv.pens.id}/on-hand",
                       data={"pack_size": "3"}).status_code == 403


def test_operator_sees_inventory_without_forms(client, inv):
    login(client, "test:operator")
    page = client.get(f"/inv/admin/items/{inv.laptop.id}?tab=inventory").get_data(as_text=True)
    assert f'/inv/admin/items/{inv.laptop.id}/inventory"' not in page
    page = client.get(f"/inv/admin/items/{inv.pens.id}?tab=on_hand").get_data(as_text=True)
    assert f'/inv/admin/items/{inv.pens.id}/on-hand"' not in page


def test_assets_tab_lists_assets_with_links(client, inv):
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/items/{inv.laptop.id}?tab=assets").get_data(as_text=True)
    assert "No assets yet." in page
    unit = InvUnit(item_id=inv.laptop.id, name="Front desk laptop")
    db.session.add(unit)
    db.session.commit()
    page = client.get(f"/inv/admin/items/{inv.laptop.id}?tab=assets").get_data(as_text=True)
    assert f'/inv/admin/units/{unit.id}"' in page and "Front desk laptop" in page


def test_on_hand_tab_shows_this_items_stock(client, inv):
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/items/{inv.pens.id}?tab=on_hand").get_data(as_text=True)
    assert "None on hand." in page
    pallet = make_container(inv, "P-1")
    add_stock(inv.pens, pallet, 7, Actor(user_id="test:invadmin"))
    db.session.commit()
    page = client.get(f"/inv/admin/items/{inv.pens.id}?tab=on_hand").get_data(as_text=True)
    assert "P-1" in page and "None on hand." not in page


def test_on_hand_tab_shows_the_last_count_in_local_time(client, inv, monkeypatch):
    from datetime import datetime
    from app.routes.inventory import admin_item_page
    # 03:30 UTC on Nov 8 is the evening of Nov 7 in the local zone.
    row = {"item_id": inv.pens.id, "name": "Blue Pen", "unit": "each", "total": 5,
           "approximate": False, "last_counted": datetime(2026, 11, 8, 3, 30),
           "places": [("P-1", 5, False)]}
    monkeypatch.setattr(admin_item_page, "supplies_on_hand", lambda: [row])
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/items/{inv.pens.id}?tab=on_hand").get_data(as_text=True)
    assert "Nov 7, " in page and "Nov 8, " not in page


@pytest.mark.parametrize("bad", ["Infinity", "-1", "3.999", "NaN"])
def test_ordering_rejects_unusable_costs(client, inv, supply_admin, bad):
    inv.pens.unit_cost_cents = 125
    db.session.commit()
    login(client, "test:supplyadmin")
    resp = client.post(f"/inv/admin/items/{inv.pens.id}/ordering",
                       data={"unit_cost": bad, "order_guidance": "Changed"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "Invalid cost format." in resp.get_data(as_text=True)
    pens = db.session.get(SupplyItem, inv.pens.id)
    assert pens.unit_cost_cents == 125 and pens.order_guidance is None


def test_on_hand_tab_shows_barcode_and_pack_size_to_viewers(client, inv):
    inv.pens.barcode = "012345678905"
    inv.pens.pack_size = 12
    add_stock(inv.pens, make_container(inv, "P-2"), 7, Actor(user_id="test:invadmin"))
    db.session.commit()
    login(client, "test:operator")
    page = client.get(f"/inv/admin/items/{inv.pens.id}?tab=on_hand").get_data(as_text=True)
    assert "012345678905" in page and "Pack size" in page and ">12<" in page.replace(" ", "")


def test_picture_input_names_the_allowed_types(client, inv):
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/items/{inv.pens.id}").get_data(as_text=True)
    assert 'accept="image/jpeg,image/png,image/webp"' in page and "up to 5 MB" in page


def test_saving_ordering_with_every_box_unticked_leaves_other_tabs_alone(client, inv, supply_admin):
    inv.pens.needs_asset_tag = True
    inv.pens.barcode = "012345678905"
    inv.pens.pack_size = 12
    db.session.commit()
    before = {f: getattr(inv.pens, f) for f in (
        "item_name", "category_id", "unit", "is_active", "is_orderable",
        "needs_asset_tag", "barcode", "pack_size", "is_serialized")}
    login(client, "test:supplyadmin")
    client.post(f"/inv/admin/items/{inv.pens.id}/ordering", data={"order_guidance": "Box"})
    pens = db.session.get(SupplyItem, inv.pens.id)
    assert {f: getattr(pens, f) for f in before} == before


def test_inventory_admin_turns_on_pre_allocation_and_picks_a_service(client, alloc):
    login(client, "test:invadmin")
    _about(client, alloc.tv, is_serialized="1", needs_pre_allocation="1",
           techops_service_type_id=str(alloc.printer_service.id))
    item = db.session.get(SupplyItem, alloc.tv.id)
    assert item.needs_pre_allocation is True
    assert item.techops_service_type_id == alloc.printer_service.id


def test_pre_allocation_is_refused_on_a_counted_item(client, inv):
    login(client, "test:invadmin")
    resp = _about(client, inv.pens, needs_pre_allocation="1")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Needs pre-allocation is only for items tracked as assets." in page
    assert db.session.get(SupplyItem, inv.pens.id).needs_pre_allocation is False


def test_unticking_the_switch_clears_the_service(client, alloc):
    login(client, "test:invadmin")
    _about(client, alloc.printer, is_serialized="1",
           techops_service_type_id=str(alloc.printer_service.id))
    item = db.session.get(SupplyItem, alloc.printer.id)
    assert item.needs_pre_allocation is False and item.techops_service_type_id is None


def test_unticking_the_switch_is_refused_while_allocated(client, alloc):
    make_allocation(make_asset(alloc.printer, "U-0001"), supply_line(alloc, alloc.alpha, 1))
    db.session.commit()
    login(client, "test:invadmin")
    resp = _about(client, alloc.printer, is_serialized="1",
                  techops_service_type_id=str(alloc.printer_service.id))
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert ("Test Printer has 1 allocated asset. Release them before turning off "
            "pre-allocation or changing its TechOps service.") in page
    item = db.session.get(SupplyItem, alloc.printer.id)
    assert item.needs_pre_allocation is True
    assert item.techops_service_type_id == alloc.printer_service.id


def test_supply_admin_cannot_post_only_the_service(client, alloc):
    login(client, "test:supplyadmin")
    resp = _about(client, alloc.tv, techops_service_type_id=str(alloc.printer_service.id))
    assert resp.status_code == 403


def test_an_unknown_service_is_refused(client, alloc):
    login(client, "test:invadmin")
    resp = _about(client, alloc.tv, is_serialized="1", needs_pre_allocation="1",
                  techops_service_type_id="999999")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Pick a TechOps service from the list." in page


def test_switch_off_is_refused_while_assets_are_allocated(client, alloc):
    for label in ("U-0001", "U-0002"):
        make_allocation(make_asset(alloc.tv, label), supply_line(alloc, alloc.alpha, 2))
    db.session.commit()
    login(client, "test:invadmin")
    resp = _about(client, alloc.tv, is_serialized="1")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert ("Test TV has 2 allocated assets. Release them before turning off "
            "pre-allocation or changing its TechOps service.") in page
    assert db.session.get(SupplyItem, alloc.tv.id).needs_pre_allocation is True


def test_service_change_is_refused_while_an_asset_is_allocated(client, alloc):
    make_allocation(make_asset(alloc.printer, "U-0001"), supply_line(alloc, alloc.alpha, 1))
    db.session.commit()
    login(client, "test:invadmin")
    resp = _about(client, alloc.printer, is_serialized="1", needs_pre_allocation="1",
                  techops_service_type_id=str(alloc.radio_service.id))
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Test Printer has 1 allocated asset. Release them" in page
    assert (db.session.get(SupplyItem, alloc.printer.id).techops_service_type_id
            == alloc.printer_service.id)


def test_supply_admin_cannot_post_the_pre_allocation_switch(client, alloc):
    login(client, "test:supplyadmin")
    assert _about(client, alloc.tv, needs_pre_allocation="1").status_code == 403


def test_about_audit_records_the_switch(client, alloc):
    db.session.get(SupplyItem, alloc.tv.id).needs_pre_allocation = False
    db.session.commit()
    login(client, "test:invadmin")
    _about(client, alloc.tv, is_serialized="1", needs_pre_allocation="1")
    events = db.session.query(ConfigAuditEvent).filter_by(entity_id=alloc.tv.id).all()
    assert any("needs_pre_allocation" in str(e.changes_json) for e in events)


def test_about_tab_offers_the_switch_and_select(client, alloc):
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/items/{alloc.tv.id}").get_data(as_text=True)
    assert "Needs pre-allocation" in page and "Fills TechOps service" in page
    assert "Printer" in page


def test_about_tab_shows_the_switch_as_text_to_a_reader(client, alloc):
    login(client, "test:supplyadmin")
    page = client.get(f"/inv/admin/items/{alloc.tv.id}").get_data(as_text=True)
    assert "Needs pre-allocation" in page and 'name="needs_pre_allocation"' not in page


def test_deactivating_is_refused_while_assets_are_allocated(client, alloc):
    make_allocation(make_asset(alloc.tv, "U-0001"), supply_line(alloc, alloc.alpha, 2))
    db.session.commit()
    login(client, "test:invadmin")
    resp = client.post(f"/inv/admin/items/{alloc.tv.id}/about", data={
        "item_name": alloc.tv.item_name, "category_id": alloc.tv.category_id, "unit": "each",
        "is_serialized": "1", "needs_pre_allocation": "1"},
        content_type="multipart/form-data")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert ("Test TV has 1 allocated asset. Release it before making the item inactive."
            in page)
    assert db.session.get(SupplyItem, alloc.tv.id).is_active is True
