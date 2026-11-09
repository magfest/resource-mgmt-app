from html.parser import HTMLParser

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


def test_inventory_admin_creates_a_tracked_item_orderable_by_default(client, inv):
    login(client, "test:invadmin")
    client.post("/inv/admin/items/new", data={
        "item_name": "Digital Sign", "category_id": str(inv.category.id),
        "unit": "each", "is_serialized": "1"})
    sign = SupplyItem.query.filter_by(item_name="Digital Sign").one()
    assert sign.is_orderable is True and sign.is_serialized is True and not sign.is_expendable


def _save(client, item, **fields):
    data = {"is_serialized": "1", "is_orderable": "1", "pack_size": ""}
    data.update(fields)
    return client.post(f"/inv/admin/items/{item.id}", data=data)


def test_items_page_saves_pack_size_and_needs_asset_tag(client, inv):
    login(client, "test:invadmin")
    _save(client, inv.laptop, pack_size="10", needs_asset_tag="on")
    item = db.session.get(SupplyItem, inv.laptop.id)
    assert item.pack_size == 10 and item.needs_asset_tag is True

    _save(client, inv.laptop)
    db.session.refresh(item)
    assert item.pack_size is None and item.needs_asset_tag is False


def test_items_page_refuses_bad_pack_size_and_saves_nothing(client, inv):
    login(client, "test:invadmin")
    for bad in ("0", "-1", "abc", "2.5"):
        resp = _save(client, inv.laptop, pack_size=bad, needs_asset_tag="on",
                     barcode="I-9999")
        assert resp.status_code == 302
        item = db.session.get(SupplyItem, inv.laptop.id)
        db.session.refresh(item)
        assert item.pack_size is None and item.needs_asset_tag is False
        assert item.barcode is None
    with client.session_transaction() as sess:
        flashes = [m for _, m in sess["_flashes"]]
    assert any("pack size" in m.lower() for m in flashes)


def test_admin_sets_gear_kind_and_model_defaults(client, inv):
    from tests.inventory.helpers import seed_gear_kind
    from app.models import InvModelDefault
    kind = seed_gear_kind("TV", "TV", [("SIZE", "Size", "NUMBER", "in")])
    db.session.commit()
    login(client, "test:invadmin")
    client.post(f"/inv/admin/items/{inv.laptop.id}/gear-kind", data={"gear_kind_id": str(kind.id)})
    assert db.session.get(SupplyItem, inv.laptop.id).gear_kind_id == kind.id
    client.post(f"/inv/admin/items/{inv.laptop.id}/defaults",
                data={f"field_{kind.fields[0].id}": "55"})
    default = db.session.query(InvModelDefault).one()
    assert default.item_id == inv.laptop.id and float(default.number_value) == 55
    client.post(f"/inv/admin/items/{inv.laptop.id}/defaults",
                data={f"field_{kind.fields[0].id}": "big"})
    assert db.session.query(InvModelDefault).one().number_value is not None
    client.post(f"/inv/admin/items/{inv.laptop.id}/gear-kind", data={"gear_kind_id": ""})
    assert db.session.get(SupplyItem, inv.laptop.id).gear_kind_id is None


def test_counted_item_has_no_gear_kind(client, inv):
    from tests.inventory.helpers import seed_gear_kind
    kind = seed_gear_kind("TV", "TV", [])
    db.session.commit()
    login(client, "test:invadmin")
    resp = client.post(f"/inv/admin/items/{inv.pens.id}/gear-kind",
                       data={"gear_kind_id": str(kind.id)}, follow_redirects=True)
    assert "Only items tracked as assets have an asset type." in resp.get_data(as_text=True)
    assert db.session.get(SupplyItem, inv.pens.id).gear_kind_id is None


class _LabelAudit(HTMLParser):
    """Collect visible controls and which of them a label covers."""

    def __init__(self):
        super().__init__()
        self.controls, self.label_for, self.in_label = [], set(), 0
        self.details = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.in_label += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        elif tag == "details":
            self.details.append(a)
        elif tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.controls.append((tag, a, self.in_label > 0))

    def handle_endtag(self, tag):
        if tag == "label":
            self.in_label -= 1


def _items_page(client):
    login(client, "test:invadmin")
    return client.get("/inv/admin/items").get_data(as_text=True)


def test_items_page_labels_every_visible_control(client, inv):
    audit = _LabelAudit()
    audit.feed(_items_page(client))
    assert audit.controls
    bare = [a.get("name") for _, a, wrapped in audit.controls
            if not wrapped and a.get("id") not in audit.label_for]
    assert bare == []


def test_items_list_shows_tracking_and_asset_type(client, inv):
    from tests.inventory.helpers import seed_gear_kind
    kind = seed_gear_kind("TV", "TV", [])
    inv.laptop.gear_kind_id = kind.id
    db.session.commit()
    page = _items_page(client)
    assert "Tracked as assets" in page and "Counted" in page and ">TV<" in page
    assert "Serialized" not in page


def test_items_page_words(client, inv):
    login(client, "test:invadmin")
    page = client.get("/inv/admin/items").get_data(as_text=True)
    assert "Asset type" in page and "Unit of measure" in page
    assert "Add an item" in page
    assert "Gear kind" not in page and "deploy-only" not in page
