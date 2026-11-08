from app import db
from app.inventory.ledger import Actor, add_stock
from app.models import SupplyItem
from tests.inventory.helpers import login, make_container


def test_admin_sets_flags_and_normalizes_barcode(client, inv):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/items/{inv.pens.id}", data={
        "is_orderable": "1", "barcode": " i-0001 "})
    item = db.session.get(SupplyItem, inv.pens.id)
    assert item.barcode == "I-0001"
    assert item.is_serialized is False


def test_item_holding_counted_stock_cannot_become_serialized(client, inv):
    add_stock(inv.pens, make_container(inv, "C-0001", kind="BIN"), 5,
              Actor(user_id="test:invadmin"))
    db.session.commit()
    login(client, "test:invadmin")
    client.post(f"/inv/admin/items/{inv.pens.id}", data={
        "is_orderable": "1", "is_serialized": "1"})
    assert db.session.get(SupplyItem, inv.pens.id).is_serialized is False


def test_deploy_only_item_is_created_unorderable(client, inv):
    login(client, "test:invadmin")
    client.post("/inv/admin/items/new", data={
        "item_name": "Digital Sign", "category_id": str(inv.category.id),
        "unit": "each", "is_serialized": "1"})
    sign = SupplyItem.query.filter_by(item_name="Digital Sign").one()
    assert sign.is_orderable is False and sign.is_serialized is True


def _save(client, item, **fields):
    data = {"is_serialized": "1", "is_orderable": "1", "pack_size": "", "check_station": ""}
    data.update(fields)
    return client.post(f"/inv/admin/items/{item.id}", data=data)


def test_items_page_saves_pack_size_and_station(client, inv):
    login(client, "test:invadmin")
    _save(client, inv.laptop, pack_size="10", check_station="TV_CHECK")
    item = db.session.get(SupplyItem, inv.laptop.id)
    assert item.pack_size == 10 and item.check_station == "TV_CHECK"

    _save(client, inv.laptop)
    db.session.refresh(item)
    assert item.pack_size is None and item.check_station is None


def test_items_page_refuses_bad_pack_size_and_saves_nothing(client, inv):
    login(client, "test:invadmin")
    for bad in ("0", "-1", "abc", "2.5"):
        resp = _save(client, inv.laptop, pack_size=bad, check_station="TV_CHECK",
                     barcode="I-9999")
        assert resp.status_code == 302
        item = db.session.get(SupplyItem, inv.laptop.id)
        db.session.refresh(item)
        assert item.pack_size is None and item.check_station is None
        assert item.barcode is None
    with client.session_transaction() as sess:
        flashes = [m for _, m in sess["_flashes"]]
    assert any("pack size" in m.lower() for m in flashes)


def test_items_page_refuses_unknown_station(client, inv):
    login(client, "test:invadmin")
    _save(client, inv.laptop, check_station="LOOKUP")
    assert db.session.get(SupplyItem, inv.laptop.id).check_station is None


def test_items_page_offers_the_check_stations(client, inv):
    login(client, "test:invadmin")
    html = client.get("/inv/admin/items").get_data(as_text=True)
    assert 'value="TV_CHECK"' in html and "TV check" in html
    assert 'value="PRINTER_CHECK"' in html and "Printer check" in html
    assert 'name="pack_size"' in html


def test_admin_sets_gear_kind_and_model_defaults(client, inv):
    from tests.inventory.helpers import seed_gear_kind
    from app.models import InvModelDefault
    kind = seed_gear_kind("TV", "TV", [("SIZE", "Size", "NUMBER", "in")])
    db.session.commit()
    login(client, "test:invadmin")
    client.post(f"/inv/admin/items/{inv.laptop.id}/gear-kind", data={"gear_kind_id": str(kind.id)})
    assert db.session.get(SupplyItem, inv.laptop.id).gear_kind_id == kind.id
    page = client.get("/inv/admin/items").get_data(as_text=True)
    assert f"/inv/admin/items/{inv.laptop.id}/defaults" in page
    assert f"/inv/admin/items/{inv.pens.id}/defaults" not in page
    client.post(f"/inv/admin/items/{inv.laptop.id}/defaults",
                data={f"field_{kind.fields[0].id}": "55"})
    default = db.session.query(InvModelDefault).one()
    assert default.item_id == inv.laptop.id and float(default.number_value) == 55
    client.post(f"/inv/admin/items/{inv.laptop.id}/defaults",
                data={f"field_{kind.fields[0].id}": "big"})
    assert db.session.query(InvModelDefault).one().number_value is not None
    client.post(f"/inv/admin/items/{inv.laptop.id}/gear-kind", data={"gear_kind_id": ""})
    assert db.session.get(SupplyItem, inv.laptop.id).gear_kind_id is None
