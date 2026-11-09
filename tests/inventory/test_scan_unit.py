"""Unit station JSON routes: find, number, check, place and queue the label."""
import os
import shutil
import subprocess
from datetime import datetime

import pytest

from app import db
from app.inventory.cards import card_for
from app.inventory.checks import finish_check, open_check
from app.inventory.devices import create_enroll_code, enroll_device
from app.inventory.ledger import Actor, add_identifier, add_unit
from app.inventory.printing import local_day, unit_number
from app.inventory.resolver import resolve
from app.inventory.shifts import start_shift
from app.models import (
    ConfigAuditEvent, EventCycle, InvCheck, InvCheckItem, InvDevice, InvPrintJob, InvPrinter,
    InvPrintRoute, InvPrepStatus, SupplyItem,
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


def test_another_stations_device_is_refused(client, inv, event):
    enrolled_client(client, station_code="COUNT")
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
    enrolled_client(client, station_code="COUNT")
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


def _check_under(unit, line, event, answer):
    check = open_check(unit, "UNIT", ACTOR, event)
    finish_check(check, {line.id: answer}, ACTOR, failed_condition="REPAIR" if answer == "NO" else None)
    db.session.commit()
    return check


def _second_event():
    cycle = EventCycle(code="MWEST27", name="MAGWest 2027", is_active=True)
    db.session.add(cycle)
    db.session.flush()
    return cycle


def test_unit_station_asks_again_only_after_a_pass(unit_client, inv, event, laptop_line):
    passed = _tagged(inv, "TV-X-020", "FEST91020")
    failed = _tagged(inv, "TV-X-021", "FEST91021")
    _check_under(passed, laptop_line, event, "YES")
    _check_under(failed, laptop_line, event, "NO")
    assert "already_checked" in post(unit_client, "scan", {"code": "FEST91020"}).json
    answer = post(unit_client, "scan", {"code": "FEST91021"}).json
    assert answer["check_id"] and "already_checked" not in answer


def test_unit_station_asks_again_for_a_check_under_another_event(
        unit_client, inv, event, laptop_line):
    unit = _tagged(inv, "TV-X-022", "FEST91022")
    _check_under(unit, laptop_line, _second_event(), "YES")
    asked = post(unit_client, "scan", {"code": "FEST91022"}).json
    assert asked["already_checked"]["result"] == "Passed"


def test_lookup_card_shows_the_latest_check_from_any_event(inv, event, laptop_line):
    unit = _tagged(inv, "TV-X-023", "FEST91023")
    _check_under(unit, laptop_line, _second_event(), "YES")
    prep = InvPrepStatus.query.filter_by(unit_id=unit.id).one()
    card = card_for(resolve("FEST91023"), event)
    assert f"Works \u00b7 {local_day(prep.created_at)}" in card["lines"]


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


def test_scan_a_rented_unit_with_no_tag_opens_its_check(unit_client, inv, two_named_units):
    a, _ = two_named_units
    a.ownership = "RENTED"
    add_identifier(a, "SERIAL", "SN-RENT-1")
    db.session.commit()
    answer = post(unit_client, "scan", {"code": "SN-RENT-1"}).json
    assert answer["ok"] is True and "needs_number" not in answer
    assert answer["check_id"] and answer["card"]["unit_id"] == a.id


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


def test_link_attaches_the_tag_and_opens_the_check_without_a_label(
        unit_client, two_named_units):
    a, _ = two_named_units
    answer = link(unit_client, a.id, "FEST91001").json
    assert answer["ok"] and answer["check_id"] and "label" not in answer
    assert answer["card"]["code"] == "FEST91001"
    assert InvPrintJob.query.count() == 0


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


def test_give_number_refuses_a_type_that_needs_an_asset_tag(unit_client, two_named_units, inv):
    a, _ = two_named_units
    a.item.needs_asset_tag = True
    db.session.commit()
    answer = post(unit_client, "give-number", {"unit_id": a.id}).json
    assert answer["ok"] is False
    assert answer["message"] == "Put an asset tag on it and scan the tag."
    assert not ConfigAuditEvent.query.filter_by(action="ADD_CODE", entity_id=str(a.id)).count()
    assert post(unit_client, "candidates", {"digits": "3"}).json["candidates"][0]["asset_tag_required"]


def test_set_aside_closes_the_check(unit_client, inv):
    _tagged(inv, "TV-X-040", "FEST91040")
    check = post(unit_client, "scan", {"code": "FEST91040"}).json
    assert post(unit_client, "set-aside", {"check_id": check["check_id"]}).json == {"ok": True}
    assert InvCheck.query.one().status == "SET_ASIDE"
    assert post(unit_client, "set-aside", {"check_id": 9999}).json["ok"] is False


def test_models_lists_active_serialized_items_by_name(unit_client, inv):
    models = post(unit_client, "models").json["models"]
    assert [m["name"] for m in models] == ["Laptop", "Wireless Access Point"]
    assert set(models[0]) == {"id", "name", "needs_asset_tag"}
    assert models[0]["needs_asset_tag"] is False
    inv.laptop.needs_asset_tag = True
    db.session.commit()
    flags = {m["name"]: m["needs_asset_tag"] for m in post(unit_client, "models").json["models"]}
    assert flags["Laptop"] is True


def test_create_with_a_tag_places_the_unit_at_home_and_opens_a_check(unit_client, inv):
    answer = post(unit_client, "create", {"item_id": inv.laptop.id, "tag": "FEST91050"}).json
    assert answer["ok"] and answer["check_id"] and answer["card"]["code"] == "FEST91050"
    assert "label" not in answer and InvPrintJob.query.count() == 0


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


def _finish_works(client, code, line):
    check = post(client, "scan", {"code": code}).json
    return post(client, "finish", {"check_id": check["check_id"],
                                   "answers": {str(line.id): "YES"}}).json


def test_undo_after_a_finish_that_queued_the_label(unit_client, inv, laptop_line):
    _tagged(inv, "TV-X-067", "FEST91067")
    assert _finish_works(unit_client, "FEST91067", laptop_line)["label"] == "queued"
    answer = unit_client.post("/inv/scan/api/undo", json={"client_request_id": "undo-1"}).json
    assert answer["ok"] is True and answer["undone"] == "check"
    assert InvPrintJob.query.one().status == "QUEUED"


def test_a_label_for_another_unit_after_the_finish_ends_the_undo_window(
        unit_client, inv, laptop_line):
    from app.inventory.receive import label_job
    _tagged(inv, "TV-X-068", "FEST91068")
    other = _tagged(inv, "TV-X-069", "FEST91069")
    _finish_works(unit_client, "FEST91068", laptop_line)
    own = InvPrintJob.query.one()
    label_job(other, Actor(shift_id=own.shift_id, device_id=own.device_id), "other-label")
    db.session.commit()
    answer = unit_client.post("/inv/scan/api/undo", json={"client_request_id": "undo-2"}).json
    assert answer["ok"] is False and answer["message"] == "Too late to undo."


def _retire(client, opened, line):
    return post(client, "finish", {"check_id": opened["check_id"],
                                   "answers": {str(line.id): "NO"},
                                   "failed_condition": "RETIRED"}).json


def test_a_unit_linked_then_retired_gets_no_label(unit_client, two_named_units, laptop_line):
    a, _ = two_named_units
    done = _retire(unit_client, link(unit_client, a.id, "FEST91002").json, laptop_line)
    assert done["ok"] is True and done["condition"] == "RETIRED"
    assert InvPrintJob.query.filter_by(unit_id=a.id).count() == 0


def test_a_unit_created_then_retired_gets_no_label(unit_client, inv, laptop_line):
    opened = post(unit_client, "create", {"item_id": inv.laptop.id, "tag": "FEST91051"}).json
    assert _retire(unit_client, opened, laptop_line)["ok"] is True
    assert InvPrintJob.query.filter_by(unit_id=opened["unit_id"]).count() == 0


def test_a_unit_given_a_number_then_retired_gets_no_label(
        unit_client, two_named_units, laptop_line):
    a, _ = two_named_units
    opened = post(unit_client, "give-number", {"unit_id": a.id}).json
    assert _retire(unit_client, opened, laptop_line)["ok"] is True
    assert InvPrintJob.query.filter_by(unit_id=a.id).count() == 0


def test_a_linked_unit_that_works_gets_one_label(unit_client, two_named_units, laptop_line):
    a, _ = two_named_units
    opened = link(unit_client, a.id, "FEST91003").json
    done = post(unit_client, "finish", {"check_id": opened["check_id"],
                                        "answers": {str(laptop_line.id): "YES"}}).json
    assert done["label"] == "queued"
    assert [j.status for j in InvPrintJob.query.filter_by(unit_id=a.id)] == ["QUEUED"]


def test_finish_needs_every_answer(unit_client, inv):
    _tagged(inv, "TV-X-061", "FEST91061")
    check = post(unit_client, "scan", {"code": "FEST91061"}).json
    assert post(unit_client, "finish", {"check_id": check["check_id"],
                                        "answers": "no"}).json["message"] == "Answer every line."


def test_finish_with_a_stale_checklist_is_refused_and_writes_nothing(unit_client, inv, laptop_line):
    _tagged(inv, "TV-X-062", "FEST91062")
    check = post(unit_client, "scan", {"code": "FEST91062"}).json
    db.session.add(InvCheckItem(item_id=inv.laptop.id, position=1, prompt="Screen intact?",
                                missing_means="NOTE", is_active=True))
    db.session.commit()
    done = post(unit_client, "finish", {"check_id": check["check_id"],
                                        "answers": {str(laptop_line.id): "YES"},
                                        "line_ids": [laptop_line.id]}).json
    assert done["ok"] is False and done["changed"] is True
    assert done["message"] == "That checklist changed. Scan the unit again."
    assert InvCheck.query.one().status == "OPEN" and InvPrepStatus.query.count() == 0


def test_a_new_shift_sets_aside_the_ended_shifts_open_check(unit_client, inv):
    _tagged(inv, "TV-X-063", "FEST91063")
    _tagged(inv, "TV-X-064", "FEST91064")
    old = post(unit_client, "scan", {"code": "FEST91063"}).json
    start_shift(InvDevice.query.filter_by(name="Unit 1").one(), "Alex")
    db.session.commit()
    new = post(unit_client, "scan", {"code": "FEST91064"}).json
    assert new["ok"] is True and new["check_id"] != old["check_id"]
    db.session.expire_all()
    assert db.session.get(InvCheck, old["check_id"]).status == "SET_ASIDE"
    assert db.session.get(InvCheck, new["check_id"]).status == "OPEN"


def test_another_device_cannot_finish_or_set_aside_this_devices_check(
        unit_client, inv, home, laptop_line):
    _tagged(inv, "TV-X-065", "FEST91065")
    check = post(unit_client, "scan", {"code": "FEST91065"}).json
    other, _ = enroll_device("Unit 2", "test:invadmin", station_code="UNIT", home_container=home)
    row = InvCheck.query.one()
    row.device_id = other.id
    db.session.commit()
    refusal = "That check is not open on this scanner. Scan the unit again."
    done = post(unit_client, "finish", {"check_id": check["check_id"],
                                        "answers": {str(laptop_line.id): "YES"}}).json
    aside = post(unit_client, "set-aside", {"check_id": check["check_id"]}).json
    assert done["ok"] is False and done["message"] == refusal
    assert aside["ok"] is False and aside["message"] == refusal
    assert InvCheck.query.one().status == "OPEN"


def test_a_repeated_finish_with_the_same_request_id_writes_once(unit_client, inv, laptop_line):
    _tagged(inv, "TV-X-066", "FEST91066")
    check = post(unit_client, "scan", {"code": "FEST91066"}).json
    body = {"check_id": check["check_id"], "answers": {str(laptop_line.id): "YES"}}
    first = post(unit_client, "finish", body, client_request_id="finish-once").json
    again = post(unit_client, "finish", body, client_request_id="finish-once").json
    assert first["ok"] is True and first == again
    assert InvPrepStatus.query.count() == 1
    assert InvCheck.query.filter_by(status="PASSED").count() == 1


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


UNIT_SCREEN_SCRIPT = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const tick = () => new Promise(r => setImmediate(r));

function node(tag) {
  const n = {tag, className: '', children: [], handlers: {}, type: '',
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(e, f) { this.handlers[e] = f; }, focus() {}};
  let text = '';
  Object.defineProperty(n, 'textContent', {
    get() { return text; }, set(v) { text = v == null ? '' : String(v); n.children = []; }});
  return n;
}
function find(root, text) {
  if (root.tag === 'button' && root.textContent === text) { return root; }
  for (const c of root.children) { const hit = find(c, text); if (hit) { return hit; } }
  return null;
}
async function click(root, text) {
  const b = find(root, text);
  assert.ok(b, 'no button ' + text);
  b.handlers.click();
  await tick(); await tick();
}

const panel = node('main');
let reply = {}, writes = [], message = '', pad = null, choice = null, opts = null, n = 0;
const S = {feedback() {}, setFlagSubject() {}, offerUndo() {}, onActivity() {}, holdLeave() {},
  showMessage: t => { message = t; }, start: o => { opts = o; }, requestId: () => 'r' + (++n),
  choose: (options, onPick, note) => { choice = {options, onPick, note}; },
  numberPad: (o, onDone) => { pad = {opts: o, onDone}; }, askText() {},
  read: (path, body) => Promise.resolve(reply[path] ? reply[path](body) : {ok: true, candidates: []}),
  write: (path, body) => { writes.push({path, body});
    return Promise.resolve(reply[path] ? reply[path](body) : {ok: false, message: 'no'}); }};
const document = {currentScript: {dataset: {scanUrl: '/scan', finishUrl: '/finish',
    linkUrl: '/link', candidatesUrl: '/cand', setAsideUrl: '/aside', keepAliveUrl: '/keep'}},
  getElementById: id => id === 'panel' ? panel : node('input'),
  createElement: node};
vm.runInNewContext(fs.readFileSync(process.env.UNIT_JS, 'utf8'),
  {window: {InvScan: S}, document, setInterval() {}, Promise, setImmediate});

const card = id => ({unit_id: id, name: 'TV-X-' + id, type: 'TV', code: 'FEST9' + id});
async function finishOne(id) {
  reply['/scan'] = () => ({ok: true, check_id: 500 + id, unit_id: id, lines: [], card: card(id)});
  await opts.onScan('FEST9' + id); await tick();
  reply['/finish'] = () => ({ok: true, status: 'PASSED', condition: 'IN_SERVICE',
    name: 'TV-X-' + id, placed: 'Warehouse', label: 'none', card: card(id)});
  await click(panel, 'Works');
}
async function palletSends() {
  reply['/scan'] = () => ({ok: false, message: 'x'});
  await opts.onScan('C-0201'); await tick();
  return writes.filter(w => w.path === '/scan').pop().body.last_unit_id;
}
async function scanAnswers(answer) {
  reply['/scan'] = () => answer;
  await opts.onScan('SCAN'); await tick();
}

(async () => {
  await finishOne(1);
  assert.strictEqual(await palletSends(), 1, 'a pallet scan right after a finish places it');

  await finishOne(2);
  await scanAnswers({ok: true, already_checked: {result: 'Passed', when: 'today'},
                     code: 'FEST93', name: 'TV', card: card(3)});
  choice.onPick('cancel');
  assert.strictEqual(await palletSends(), null, 'already checked, then Cancel');

  await finishOne(11);
  await scanAnswers({ok: true, already_checked: {result: 'Passed', when: 'today'},
                     code: 'FEST911', name: 'TV', card: card(11)});
  choice.onPick('cancel');
  assert.strictEqual(await palletSends(), 11, 'a rescan of the same unit keeps it');

  await finishOne(4);
  await scanAnswers({ok: true, needs_number: true, card: card(5), asset_tag_required: false});
  await click(panel, 'Cancel');
  assert.strictEqual(await palletSends(), null, 'needs a number, then Cancel');

  await finishOne(6);
  await scanAnswers({ok: true, candidates: [card(7), card(8)]});
  choice.onPick(null);
  assert.strictEqual(await palletSends(), null, 'two candidates, then Cancel');

  await finishOne(9);
  await scanAnswers({ok: true, which: true, tag: 'SN-UNKNOWN-1'});
  // The script runs in another context, so its arrays are compared as JSON.
  assert.strictEqual(JSON.stringify(pad.opts.lines), JSON.stringify(
    ['Scanned SN-UNKNOWN-1.', 'Which unit is this? Tap the number written on it.']));
  assert.strictEqual(JSON.stringify(pad.opts.actions.map(a => a.label)),
                     JSON.stringify(['Not an asset tag']));
  const before = writes.length;
  pad.opts.actions[0].onPick();
  assert.strictEqual(message, 'Nothing saved. Scan the asset tag, U- label or serial.');
  assert.strictEqual(writes.length, before, 'Not an asset tag saves nothing');
  assert.strictEqual(await palletSends(), null, 'which, then Not an asset tag');

  await finishOne(10);
  await click(panel, 'No code on it');
  assert.ok(!pad.opts.lines && !pad.opts.actions, 'No code on it shows no scanned code');
  pad.onDone({entered: 3}); await tick(); await tick();
  choice.onPick('cancel');
  assert.strictEqual(await palletSends(), null, 'No code on it, number pad, then Cancel');
  console.log('unit screen ok');
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_unit_screen_forgets_the_last_unit_and_shows_the_scanned_code_under_node(tmp_path):
    """Runs station_unit.js on a stub page under node; it does not drive a browser."""
    script = tmp_path / "unit_screen.js"
    script.write_text(UNIT_SCREEN_SCRIPT)
    static = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True,
        env={**os.environ, "UNIT_JS": os.path.abspath(os.path.join(static, "station_unit.js"))})
    assert result.returncode == 0, result.stderr
    assert "unit screen ok" in result.stdout
