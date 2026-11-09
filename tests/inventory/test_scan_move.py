"""Move station JSON routes: set a destination, place things, create labels, move supplies."""
import pytest

from app import db
from app.inventory import labels
from app.inventory.ledger import add_stock
from app.models import InvContainer, InvDevice, InvFlag, InvMovement
from app.inventory.shifts import start_shift
from tests.inventory.helpers import enrolled_client, login, make_container

BASE = "/inv/scan/api/move/"
SCAN, CREATE, SOURCE = BASE + "scan", BASE + "create", BASE + "source"
FIND, SUPPLIES = BASE + "find", BASE + "supplies"
CARD = "OP-0123456789AB"
NOT_MOVE = "This scanner is not set up for moving."
_n = [0]


def _rid():
    _n[0] += 1
    return f"mv-{_n[0]}"


@pytest.fixture
def mv(client, inv):
    home = make_container(inv, "C-0005", kind="LOCATION", name="Warehouse")
    db.session.commit()
    device = enrolled_client(client, station_code="MOVE")
    device.home_container_id = home.id
    start_shift(device, "test:operator")
    db.session.commit()
    return inv


def test_move_scan_sets_moves_and_a_repeat_id_writes_once(client, mv):
    make_container(mv, "C-0040")
    make_container(mv, "C-0042", kind="BIN")
    db.session.commit()
    dest = client.post(SCAN, json={"client_request_id": "mv-1", "code": "C-0040",
                                   "destination_code": None}).get_json()
    assert dest["ok"] is True and dest["destination"]["code"] == "C-0040"
    body = {"client_request_id": "mv-2", "code": "C-0042", "destination_code": "C-0040"}
    first, again = (client.post(SCAN, json=body).get_json() for _ in range(2))
    assert first == again and first["message"] == "C-0042 is on C-0040."
    move = InvMovement.query.filter_by(action="MOVE").one()
    assert move.shift_id is not None and move.device_id == InvDevice.query.one().id


def test_new_labels_go_in_home_or_on_the_destination(client, mv):
    make_container(mv, "C-0040")
    db.session.commit()
    a, b = labels.allocate("C", 2)
    db.session.commit()
    kind = mv.kinds["BIN"].id
    first = client.post(CREATE, json={"client_request_id": _rid(), "label_code": a,
                                      "kind_id": kind, "destination_code": None}).get_json()
    second = client.post(CREATE, json={"client_request_id": _rid(), "label_code": b,
                                       "kind_id": kind, "destination_code": "C-0040"}).get_json()
    assert first["ok"] is True and second["ok"] is True
    parents = {c.label_code: c.parent.label_code for c in InvContainer.query.all() if c.parent}
    assert parents[a] == "C-0005" and parents[b] == "C-0040"


def test_a_short_supplies_move_retried_writes_once_and_balances(app, client, mv):
    from app.inventory.ledger import Actor
    source = make_container(mv, "C-0040")
    make_container(mv, "C-0042", kind="BIN")
    add_stock(mv.pens, source, 1, Actor(user_id="test:operator"))
    db.session.commit()
    body = {"client_request_id": "mv-short", "source_code": "C-0040",
            "destination_code": "C-0042", "item_id": mv.pens.id, "quantity": 6}
    first, again = (client.post(SUPPLIES, json=body).get_json() for _ in range(2))
    assert first["ok"] is True and first == again
    assert InvMovement.query.filter_by(action="ADJUST", note="short move").count() == 1
    assert InvMovement.query.filter_by(action="MOVE").count() == 1
    assert InvFlag.query.count() == 1
    result = app.test_cli_runner().invoke(args=["inv-check-balances"])
    assert result.exit_code == 0 and "0 mismatch" in result.output


def test_move_source_names_the_place(client, mv):
    make_container(mv, "C-0040")
    make_container(mv, "C-0042", kind="BIN")
    db.session.commit()
    answer = client.post(SOURCE, json={"code": "C-0040", "destination_code": "C-0042"}).get_json()
    assert answer["ok"] is True and answer["source"]["code"] == "C-0040"


def test_move_find_names_items_and_refuses_assets_in_move_words(client, mv):
    labels.allocate("I", 1)
    mv.pens.barcode = "I-0001"
    mv.laptop.barcode = "999"
    make_container(mv, "C-0040")
    db.session.commit()
    ok = client.post(FIND, json={"code": "I-0001"}).get_json()
    assert ok["ok"] is True and [i["name"] for i in ok["items"]] == ["Blue Pen"]
    laptop = client.post(FIND, json={"code": "999"}).get_json()
    assert laptop["ok"] is False and laptop["message"] == \
        "Assets move one by one. Tap Cancel, then scan each asset."
    place = client.post(FIND, json={"code": "C-0040"}).get_json()
    assert place["ok"] is False and place["message"] == "Tap the item, or scan its barcode."
    assert client.post("/inv/scan/api/count/browse", json={}).get_json()["ok"] is True


def test_a_card_is_refused_on_every_move_scan(client, mv):
    make_container(mv, "C-0040")
    db.session.commit()
    for url, body in ((SCAN, {"destination_code": None}), (SCAN, {"destination_code": "C-0040"}),
                      (SOURCE, {"destination_code": "C-0040"})):
        answer = client.post(url, json={"client_request_id": _rid(), "code": CARD, **body}).get_json()
        assert answer["ok"] is False and answer["message"] == "That is a sign-in card."
    assert InvMovement.query.count() == 0


def test_move_routes_refuse_a_device_on_another_station(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    start_shift(device, "test:operator")
    db.session.commit()
    bodies = {SCAN: {"code": "C-0001", "destination_code": None},
              CREATE: {"label_code": "C-0001", "kind_id": 1, "destination_code": None},
              SOURCE: {"code": "C-0001", "destination_code": "C-0002"},
              FIND: {"q": "pen"},
              SUPPLIES: {"source_code": "C-0001", "destination_code": "C-0002",
                         "item_id": 1, "quantity": 1}}
    for url, body in bodies.items():
        answer = client.post(url, json={"client_request_id": _rid(), **body}).get_json()
        assert answer["ok"] is False and answer["message"] == NOT_MOVE, url


def test_a_lead_can_set_a_scanner_to_move(client, inv):
    device = enrolled_client(client, name="Scanner 4", station_code="LOOKUP")
    login(client, "test:invadmin")
    client.post(f"/inv/admin/devices/{device.id}/station", data={"station_code": "MOVE"})
    assert db.session.get(InvDevice, device.id).station_code == "MOVE"
    assert '<option value="MOVE"' in client.get("/inv/admin/devices").get_data(as_text=True)
