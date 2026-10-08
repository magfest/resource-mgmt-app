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
