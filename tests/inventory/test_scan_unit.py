"""Unit station JSON routes: find, number, check, place and queue the label."""
from datetime import datetime

import pytest

from app import db
from app.inventory.devices import create_enroll_code, enroll_device
from app.inventory.ledger import Actor, add_identifier, add_unit
from app.inventory.printing import unit_number
from app.inventory.shifts import start_shift
from app.models import (
    ConfigAuditEvent, EventCycle, InvCheck, InvCheckItem, InvDevice, InvPrintJob, InvPrinter,
    InvPrintRoute, SupplyItem,
)
from app.models.constants import INV_IDENT_MAGFEST_TAG, INV_JOB_OWNED_UNIT
from tests.inventory.helpers import enrolled_client, login, make_container

ACTOR = Actor(user_id="test:invadmin")
BASE = "/inv/scan/api/unit/"
NOT_UNIT = "This scanner is not set up as a Unit station."
NO_HOME = "This scanner has no home container. Ask an inventory admin."
_n = [0]


def post(client, path, body=None, **kw):
    _n[0] += 1
    return client.post(BASE + path, json={"client_request_id": f"unit-{_n[0]}",
                                          **(body or {}), **kw})


def link(client, unit_id, tag):
    return post(client, "link", {"unit_id": unit_id, "tag": tag})


@pytest.fixture
def event(inv):
    cycle = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(cycle)
    db.session.flush()
    return cycle


@pytest.fixture
def printer(inv):
    station, _ = enroll_device("Print PC", "test:invadmin", station_code="PRINT")
    station.last_seen_at = datetime.utcnow()
    p = InvPrinter(name="Perm", dpi=203, width_in=2, height_in=3,
                   device_id=station.id, device_name="Perm")
    db.session.add(p)
    db.session.flush()
    db.session.add(InvPrintRoute(job_type=INV_JOB_OWNED_UNIT, printer_id=p.id))
    db.session.commit()
    return p


@pytest.fixture
def home(inv):
    box = make_container(inv, "C-HOME", kind="LOCATION", name="Warehouse")
    db.session.commit()
    return box


def _unit_client(client, home_container):
    raw = create_enroll_code("Unit 1", "test:invadmin", station_code="UNIT",
                             home_container=home_container)
    db.session.commit()
    with client.session_transaction() as sess:
        sess.clear()
    assert client.post("/inv/scan/enroll", data={"code": raw}).status_code == 302
    start_shift(InvDevice.query.filter_by(name="Unit 1").one(), "Sam")
    db.session.commit()
    return client


@pytest.fixture
def unit_client(client, inv, event, printer, home):
    return _unit_client(client, home)


@pytest.fixture
def two_named_units(inv):
    units = [add_unit(inv.laptop, None, ACTOR, [], name=n) for n in ("TV-X-003", "TV-X-004")]
    db.session.commit()
    return units


@pytest.fixture
def laptop_line(inv):
    line = InvCheckItem(item_id=inv.laptop.id, position=0, prompt="Powers on?",
                        missing_means="FAIL", is_active=True)
    db.session.add(line)
    db.session.commit()
    return line


def _tagged(inv, name, tag, container=None):
    unit = add_unit(inv.laptop, container, ACTOR, [(INV_IDENT_MAGFEST_TAG, tag)], name=name)
    db.session.commit()
    return unit


def test_tv_check_device_is_refused(client, inv, event):
    enrolled_client(client, station_code="TV_CHECK")
    answer = post(client, "scan", {"code": "FEST91002"})
    assert answer.json["ok"] is False and answer.json["message"] == NOT_UNIT


def test_every_route_but_keepalive_refuses_a_unit_device_with_no_home(
        client, inv, event, two_named_units):
    enrolled_client(client, station_code="LOOKUP")
    device = InvDevice.query.one()
    device.station_code = "UNIT"
    start_shift(device, "Sam")
    db.session.commit()
    for path, body in [("scan", {"code": "X"}), ("candidates", {"digits": "3"}),
                       ("link", {"unit_id": 1, "tag": "FEST1"}),
                       ("give-number", {"unit_id": 1}), ("set-aside", {"check_id": 1}),
                       ("models", {}), ("create", {"item_id": 1}),
                       ("finish", {"check_id": 1, "answers": {}})]:
        assert post(client, path, body).json["message"] == NO_HOME, path
    assert post(client, "keepalive").json == {"ok": True}


def test_keepalive_refuses_another_station(client, inv, event):
    enrolled_client(client, station_code="TV_CHECK")
    assert post(client, "keepalive").json["message"] == NOT_UNIT


def test_scan_a_numbered_unit_opens_a_check_with_its_card(unit_client, inv, laptop_line):
    unit = _tagged(inv, "TV-X-010", "FEST91010")
    answer = post(unit_client, "scan", {"code": "FEST91010"}).json
    assert answer["ok"] is True and answer["unit_id"] == unit.id
    assert [x["prompt"] for x in answer["lines"]] == ["Powers on?"]
    assert answer["card"]["name"] == "TV-X-010" and answer["card"]["code"] == "FEST91010"
    assert InvCheck.query.one().station_code == "UNIT"


def test_scan_a_checked_unit_asks_first_and_again_reopens(unit_client, inv, laptop_line):
    unit = _tagged(inv, "TV-X-011", "FEST91011")
    first = post(unit_client, "scan", {"code": "FEST91011"}).json
    post(unit_client, "finish", {"check_id": first["check_id"],
                                 "answers": {str(laptop_line.id): "YES"}})
    asked = post(unit_client, "scan", {"code": "FEST91011"}).json
    assert asked["already_checked"]["result"] == "Passed" and asked["card"]["unit_id"] == unit.id
    assert "check_id" not in asked
    again = post(unit_client, "scan", {"code": "FEST91011", "again": True}).json
    assert again["check_id"]


def test_scan_blocked_by_another_open_check(unit_client, inv):
    _tagged(inv, "TV-X-012", "FEST91012")
    _tagged(inv, "TV-X-013", "FEST91013")
    post(unit_client, "scan", {"code": "FEST91012"})
    blocked = post(unit_client, "scan", {"code": "FEST91013"}).json
    assert blocked["ok"] is False and blocked["open_check"]["check_id"]


def test_scan_resumes_the_open_check_on_the_same_unit(unit_client, inv):
    _tagged(inv, "TV-X-014", "FEST91014")
    first = post(unit_client, "scan", {"code": "FEST91014"}).json
    assert post(unit_client, "scan", {"code": "FEST91014"}).json["check_id"] == first["check_id"]


def test_scan_unit_with_no_number_needs_one(unit_client, inv, two_named_units):
    a, _ = two_named_units
    add_identifier(a, "SERIAL", "SN-A-1")
    db.session.commit()
    answer = post(unit_client, "scan", {"code": "SN-A-1"}).json
    assert answer["needs_number"] is True and answer["card"]["unit_id"] == a.id
    assert answer["asset_tag_required"] is False
    a.item.needs_asset_tag = True
    db.session.commit()
    assert post(unit_client, "scan", {"code": "SN-A-1"}).json["asset_tag_required"] is True


def test_scan_several_units_lists_candidates_and_unit_id_picks(unit_client, inv):
    a = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "SN-DUP")], name="TV-X-020")
    b = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "SN-DUP")], name="TV-X-021")
    db.session.commit()
    answer = post(unit_client, "scan", {"code": "SN-DUP"}).json
    assert {c["unit_id"] for c in answer["candidates"]} == {a.id, b.id}
    assert answer["candidates"][0]["type"] == "Laptop"
    picked = post(unit_client, "scan", {"code": "SN-DUP", "unit_id": b.id}).json
    assert picked.get("needs_number") is True and picked["card"]["unit_id"] == b.id


def test_scan_unknown_tag_shaped_code_asks_which(unit_client):
    assert post(unit_client, "scan", {"code": "FEST99999"}).json == {
        "ok": True, "which": True, "tag": "FEST99999"}


def test_scan_unused_u_label_is_refused(unit_client):
    from app.inventory import labels
    code = labels.allocate(labels.PREFIX_UNIT, 1)[0]
    db.session.commit()
    answer = post(unit_client, "scan", {"code": code}).json
    assert answer["message"] == (
        "This U- label is on no unit. Scan the unit's asset tag, or tap No code on it.")


def test_scan_anything_else_is_refused(unit_client):
    answer = post(unit_client, "scan", {"code": "C-9999"}).json
    assert answer["message"] == "No unit has that code. Tap No code on it to find the unit."


def test_scan_a_pallet_without_a_last_unit_is_refused(unit_client, inv):
    make_container(inv, "C-0201", kind="PALLET", name="Pallet 1")
    db.session.commit()
    assert post(unit_client, "scan", {"code": "C-0201"}).json["message"] == (
        "Scan a unit first, then its pallet.")


def test_scan_a_pallet_places_the_last_unit(unit_client, inv, home, laptop_line):
    pallet = make_container(inv, "C-0202", kind="PALLET", name="Pallet 2")
    unit = _tagged(inv, "TV-X-030", "FEST91030")
    db.session.commit()
    check = post(unit_client, "scan", {"code": "FEST91030"}).json
    done = post(unit_client, "finish", {"check_id": check["check_id"],
                                        "answers": {str(laptop_line.id): "YES"}}).json
    assert done["placed"] == "Warehouse" and done["card"]["unit_id"] == unit.id
    answer = post(unit_client, "scan", {"code": "C-0202", "last_unit_id": unit.id}).json
    assert answer["placed"] == "Pallet 2" and answer["card"]["unit_id"] == unit.id
    db.session.expire_all()
    assert unit.current_container_id == pallet.id
    wrong = post(unit_client, "scan", {"code": "C-0202", "last_unit_id": unit.id + 99}).json
    assert wrong["ok"] is False


def test_candidates_by_digits_and_by_name(unit_client, two_named_units):
    a, b = two_named_units
    by_digits = post(unit_client, "candidates", {"digits": "3"}).json
    assert [c["unit_id"] for c in by_digits["candidates"]] == [a.id]
    by_name = post(unit_client, "candidates", {"name": "tv-x"}).json
    assert [c["unit_id"] for c in by_name["candidates"]] == [a.id, b.id]
    assert post(unit_client, "candidates", {}).json["ok"] is False


def test_link_attaches_the_tag_queues_the_label_and_opens_the_check(
        unit_client, two_named_units):
    a, _ = two_named_units
    answer = link(unit_client, a.id, "FEST91001").json
    assert answer["ok"] and answer["check_id"] and answer["label"] == "queued"
    assert answer["card"]["code"] == "FEST91001"
    assert InvPrintJob.query.count() == 1


def test_link_refuses_a_tag_held_by_another_unit(unit_client, two_named_units):
    a, b = two_named_units
    link(unit_client, a.id, "FEST91001")
    answer = link(unit_client, b.id, "FEST91001")
    assert answer.json["ok"] is False and a.name in answer.json["message"]
    assert unit_number(b) is None


def test_give_number_adds_a_u_label_and_records_who(unit_client, two_named_units):
    a, _ = two_named_units
    answer = post(unit_client, "give-number", {"unit_id": a.id}).json
    assert answer["ok"] and answer["code"].startswith("U-") and answer["check_id"]
    row = ConfigAuditEvent.query.filter_by(action="ADD_CODE", entity_id=str(a.id)).one()
    assert row.created_by_user_id.startswith("shift:")
    again = post(unit_client, "give-number", {"unit_id": a.id}).json
    assert again["message"] == "This unit already has a number."


def test_set_aside_closes_the_check(unit_client, inv):
    _tagged(inv, "TV-X-040", "FEST91040")
    check = post(unit_client, "scan", {"code": "FEST91040"}).json
    assert post(unit_client, "set-aside", {"check_id": check["check_id"]}).json == {"ok": True}
    assert InvCheck.query.one().status == "SET_ASIDE"
    assert post(unit_client, "set-aside", {"check_id": 9999}).json["ok"] is False


def test_models_lists_active_serialized_items_by_name(unit_client, inv):
    models = post(unit_client, "models").json["models"]
    assert [m["name"] for m in models] == ["Laptop", "Wireless Access Point"]
    assert set(models[0]) == {"id", "name"}


def test_create_with_a_tag_places_the_unit_at_home_and_opens_a_check(unit_client, inv):
    answer = post(unit_client, "create", {"item_id": inv.laptop.id, "tag": "FEST91050"}).json
    assert answer["ok"] and answer["check_id"] and answer["card"]["code"] == "FEST91050"
    assert answer["label"] == "queued"


def test_create_refuses_a_tag_another_unit_holds_and_names_it(unit_client, inv):
    from app.models import InvUnit
    _tagged(inv, "TV-X-080", "FEST91080")
    before = InvUnit.query.count()
    answer = post(unit_client, "create", {"item_id": inv.laptop.id, "tag": "FEST91080"}).json
    assert answer["ok"] is False and "TV-X-080" in answer["message"]
    assert InvUnit.query.count() == before


def test_create_without_a_tag_gives_a_generated_number(unit_client, inv):
    answer = post(unit_client, "create", {"item_id": inv.laptop.id}).json
    assert answer["ok"] and answer["card"]["code"].startswith("U-") and answer["check_id"]


def test_create_without_a_tag_is_refused_when_the_model_needs_one(unit_client, inv):
    inv.laptop.needs_asset_tag = True
    db.session.commit()
    answer = post(unit_client, "create", {"item_id": inv.laptop.id}).json
    assert answer["ok"] is False and "asset tag" in answer["message"]


def test_create_refuses_an_unknown_model(unit_client):
    assert post(unit_client, "create", {"item_id": 99999}).json["message"] == "Pick a model."


def test_finish_places_the_unit_and_queues_the_label(unit_client, inv, home, laptop_line):
    unit = _tagged(inv, "TV-X-060", "FEST91060")
    check = post(unit_client, "scan", {"code": "FEST91060"}).json
    done = post(unit_client, "finish", {"check_id": check["check_id"],
                                        "answers": {str(laptop_line.id): "YES"}}).json
    assert done["status"] == "PASSED" and done["placed"] == "Warehouse"
    assert done["label"] == "queued" and done["card"]["name"] == "TV-X-060"
    db.session.expire_all()
    assert unit.current_container_id == home.id


def test_finish_needs_every_answer(unit_client, inv):
    _tagged(inv, "TV-X-061", "FEST91061")
    check = post(unit_client, "scan", {"code": "FEST91061"}).json
    assert post(unit_client, "finish", {"check_id": check["check_id"],
                                        "answers": "no"}).json["message"] == "Answer every line."


def test_an_operator_has_no_home_and_places_nothing(client, inv, event, printer, laptop_line):
    unit = _tagged(inv, "TV-X-070", "FEST91070")
    login(client, "test:operator")
    check = post(client, "scan", {"code": "FEST91070"}).json
    done = post(client, "finish", {"check_id": check["check_id"],
                                   "answers": {str(laptop_line.id): "YES"}}).json
    assert done["ok"] and done["placed"] is None
    created = post(client, "create", {"item_id": inv.laptop.id, "tag": "FEST91071"}).json
    assert created["ok"]
    db.session.expire_all()
    assert unit.current_container_id is None
