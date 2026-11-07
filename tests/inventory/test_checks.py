"""TV and printer check stations: the check rules, the API, the page and the page script."""
import os
from datetime import datetime
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app import db
from app.inventory import labels
from app.inventory.checks import (
    active_check_items, bind_label, check_serial, create_checked_unit, display_number,
    finish_check, last_result, open_check, prep_status, set_aside, station_models,
)
from app.inventory.ledger import Actor, InventoryError, add_unit
from app.inventory.resolver import KIND_UNKNOWN, resolve
from app.inventory.shifts import start_shift
from app.models import (
    EventCycle, InvCheck, InvCheckItem, InvDevice, InvMovement, InvPrepStatus,
    InvUnit, SupplyItem,
)
from tests.inventory.helpers import enrolled_client, login, make_container

ACTOR = Actor(user_id="test:invadmin")
OPEN = "/inv/scan/api/check/open"
SERIAL = "/inv/scan/api/check/serial"
FINISH = "/inv/scan/api/check/finish"
ASIDE = "/inv/scan/api/check/set-aside"
BIND = "/inv/scan/api/check/bind-label"
CREATE = "/inv/scan/api/check/create-unit"
STATION = "/inv/scan/api/station"
NO_EVENT = "No default event is set. Ask an inventory admin."
_n = [0]


def _rid():
    _n[0] += 1
    return f"chk-{_n[0]}"


@pytest.fixture
def event(inv):
    cycle = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(cycle)
    db.session.flush()
    return cycle


@pytest.fixture
def tv_checklist(inv, event):
    """A TV model with two lines, one TV with a tag and serial, and a second TV."""
    tv_item = SupplyItem(category_id=inv.category.id, item_name="Samsung 55 TV", unit="each",
                         is_serialized=True, is_expendable=False, check_station="TV_CHECK")
    printer_item = SupplyItem(category_id=inv.category.id, item_name="Zebra Printer",
                              unit="each", is_serialized=True, is_expendable=False,
                              check_station="PRINTER_CHECK")
    db.session.add_all([tv_item, printer_item])
    db.session.flush()
    power = InvCheckItem(item_id=tv_item.id, position=0, prompt="Powered on?",
                         missing_means="FAIL", is_active=True)
    remote = InvCheckItem(item_id=tv_item.id, position=1, prompt="Remote present?",
                          missing_means="NOTE", is_active=True)
    db.session.add_all([power, remote])
    where = make_container(inv, "C-0001", kind="BIN")
    tv = add_unit(tv_item, where, ACTOR, [("MAGFEST_TAG", "TECH00412"), ("SERIAL", "SN4471")])
    tv2 = add_unit(tv_item, where, ACTOR, [("MAGFEST_TAG", "TECH00413")])
    printer = add_unit(printer_item, where, ACTOR,
                       [("MAGFEST_TAG", "TECH00500"), ("SERIAL", "PS1")])
    db.session.commit()
    return SimpleNamespace(tv_item=tv_item, printer_item=printer_item, power=power,
                           remote=remote, tv=tv, tv2=tv2, printer=printer, where=where)


# ---- rules ----

def test_nothing_passes_by_default_and_note_lines_pass_with_a_note(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    with pytest.raises(InventoryError, match="Answer every line"):
        finish_check(check, {t.power.id: "YES"}, ACTOR)
    finish_check(check, {t.power.id: "YES", t.remote.id: "NO"}, ACTOR)
    assert check.status == "PASSED" and "Remote present?" in check.note
    assert prep_status(t.tv, event) == "CHECKED"


def test_a_value_other_than_yes_or_no_does_not_count_as_an_answer(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    with pytest.raises(InventoryError, match="Answer every line"):
        finish_check(check, {t.power.id: "YES", t.remote.id: "MAYBE"}, ACTOR)
    assert check.status == "OPEN"


def test_failed_check_changes_condition_never_location(inv, event, tv_checklist):
    t = tv_checklist
    where = t.tv.current_container_id
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    finish_check(check, {t.power.id: "NO", t.remote.id: "YES"}, ACTOR, failed_condition="REPAIR")
    assert t.tv.condition == "REPAIR" and t.tv.current_container_id == where
    assert check.previous_condition == "IN_SERVICE"
    assert prep_status(t.tv, event) == "FAILED_CHECK"
    with pytest.raises(InventoryError, match="repair or retire"):
        finish_check(open_check(t.tv2, "TV_CHECK", ACTOR, event),
                     {t.power.id: "NO", t.remote.id: "YES"}, ACTOR)


def test_a_failed_check_with_a_condition_on_a_passed_check_is_refused(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    with pytest.raises(InventoryError, match="checklist changed"):
        finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, ACTOR,
                     failed_condition="REPAIR")
    assert t.tv.condition == "IN_SERVICE"


def test_a_model_with_no_lines_takes_a_pass_or_a_fail_with_a_note(inv, event, tv_checklist):
    t = tv_checklist
    InvCheckItem.query.filter_by(item_id=t.tv_item.id).delete()
    db.session.commit()
    assert active_check_items(t.tv_item) == []
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    finish_check(check, {}, ACTOR)
    assert check.status == "PASSED"
    second = open_check(t.tv2, "TV_CHECK", ACTOR, event)
    with pytest.raises(InventoryError, match="note"):
        finish_check(second, {}, ACTOR, failed_condition="REPAIR")
    finish_check(second, {}, ACTOR, failed_condition="RETIRED", note="Cracked panel")
    assert second.status == "FAILED" and second.note == "Cracked panel"
    assert t.tv2.condition == "RETIRED"


def test_a_second_open_check_names_the_first(inv, event, tv_checklist):
    t = tv_checklist
    open_check(t.tv, "TV_CHECK", ACTOR, event)
    with pytest.raises(InventoryError, match="TECH00412 is not finished"):
        open_check(t.tv2, "TV_CHECK", ACTOR, event)


def test_scanning_the_open_unit_again_resumes_its_check(inv, event, tv_checklist):
    t = tv_checklist
    first = open_check(t.tv, "TV_CHECK", ACTOR, event)
    assert open_check(t.tv, "TV_CHECK", ACTOR, event).id == first.id
    assert InvCheck.query.count() == 1


def test_set_aside_leaves_the_unit_unchecked_and_frees_the_shift(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    set_aside(check, ACTOR)
    assert check.status == "SET_ASIDE" and prep_status(t.tv, event) is None
    assert open_check(t.tv2, "TV_CHECK", ACTOR, event).status == "OPEN"


def test_a_unit_is_refused_at_a_station_that_does_not_list_its_model(inv, event, tv_checklist):
    t = tv_checklist
    with pytest.raises(InventoryError, match="Zebra Printer is not checked at this station"):
        open_check(t.printer, "TV_CHECK", ACTOR, event)
    laptop = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "LAP1")])
    with pytest.raises(InventoryError, match="Laptop is not checked"):
        open_check(laptop, "TV_CHECK", ACTOR, event)


def test_every_write_refuses_without_a_default_event(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    event.is_default = False
    db.session.commit()
    with pytest.raises(InventoryError, match=NO_EVENT):
        open_check(t.tv2, "TV_CHECK", ACTOR, None)
    with pytest.raises(InventoryError, match=NO_EVENT):
        finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, ACTOR)
    with pytest.raises(InventoryError, match=NO_EVENT):
        set_aside(check, ACTOR)
    with pytest.raises(InventoryError, match=NO_EVENT):
        check_serial(t.tv, "SN4471")


def test_serial_scan_replaces_a_wrong_imported_serial(inv, event, tv_checklist):
    t = tv_checklist
    assert check_serial(t.tv, "SN4417") == {"result": "replaced", "old": "SN4471"}
    assert resolve("SN4417").unit == t.tv
    assert resolve("SN4471").kind == KIND_UNKNOWN


def test_serial_matches_records_and_refuses_another_units_serial(inv, event, tv_checklist):
    t = tv_checklist
    assert check_serial(t.tv, "sn4471") == {"result": "matches", "old": None}
    assert check_serial(t.tv2, "SN9") == {"result": "recorded", "old": None}
    with pytest.raises(InventoryError, match="TECH00413"):
        check_serial(t.tv, "SN9")
    assert resolve("SN9").unit == t.tv2 and resolve("SN4471").unit == t.tv
    with pytest.raises(InventoryError, match="Scan the SERIAL"):
        check_serial(t.tv, "TECH00412")
    with pytest.raises(InventoryError, match="not a serial"):
        check_serial(t.tv, "TECH00413")
    with pytest.raises(InventoryError, match="not a serial"):
        check_serial(t.tv, "U-00001")
    assert resolve("TECH00413").unit == t.tv2


# ---- unused U- labels ----

def _issue_label():
    code = labels.allocate(labels.PREFIX_UNIT, 1)[0]
    db.session.commit()
    return code


def test_bind_label_binds_a_unit_that_has_the_serial_and_no_number(inv, event, tv_checklist):
    t = tv_checklist
    imported = add_unit(t.tv_item, None, ACTOR, [("SERIAL", "IMP1")])
    code = _issue_label()
    unit = bind_label(code, "imp1", ACTOR)
    assert unit.id == imported.id and resolve(code).unit == unit


def test_bind_label_refuses_a_unit_that_already_has_a_number(inv, event, tv_checklist):
    code = _issue_label()
    with pytest.raises(InventoryError, match="SN4471 already has number TECH00412. Use that label."):
        bind_label(code, "SN4471", ACTOR)
    assert resolve(code).kind == "UNUSED"


def test_bind_label_refuses_two_matches_naming_both_numbers(inv, event, tv_checklist):
    t = tv_checklist
    add_unit(t.printer_item, None, ACTOR, [("MAGFEST_TAG", "TECH00600"), ("SERIAL", "SN4471")])
    code = _issue_label()
    with pytest.raises(InventoryError, match="TECH00412.*TECH00600"):
        bind_label(code, "SN4471", ACTOR)


def test_create_checked_unit_uses_the_label_as_its_one_number(inv, event, tv_checklist):
    t = tv_checklist
    code = _issue_label()
    unit = create_checked_unit(t.tv_item, code, "NEW1", ACTOR)
    assert resolve(code).unit == unit and resolve("NEW1").unit == unit
    assert sorted(i.kind for i in unit.identifiers) == ["FESTOPS_LABEL", "SERIAL"]
    with pytest.raises(InventoryError):
        create_checked_unit(t.tv_item, code, "NEW2", ACTOR)
    with pytest.raises(InventoryError, match="TECH00412"):
        create_checked_unit(t.tv_item, _issue_label(), "SN4471", ACTOR)


def test_the_serial_guard_runs_on_every_path(inv, event, tv_checklist):
    t = tv_checklist
    t.tv_item.barcode = "012345678905"
    db.session.commit()
    code = _issue_label()
    for scanned in ("TECH00413", "012345678905"):
        with pytest.raises(InventoryError):
            create_checked_unit(t.tv_item, code, scanned, ACTOR)
        with pytest.raises(InventoryError):
            bind_label(code, scanned, ACTOR)
        assert resolve(code).kind == "UNUSED"
    assert InvUnit.query.count() == 3
    with pytest.raises(InventoryError, match=r"model barcode. Scan the serial \(S/N\)\."):
        check_serial(t.tv, "012345678905")
    with pytest.raises(InventoryError, match="model barcode"):
        bind_label(code, "012345678905", ACTOR)
    with pytest.raises(InventoryError, match="model barcode"):
        create_checked_unit(t.tv_item, code, "012345678905", ACTOR)
    with pytest.raises(InventoryError, match="not a serial"):
        create_checked_unit(t.tv_item, code, "TECH00413", ACTOR)
    assert [i.value for i in t.tv2.identifiers] == ["TECH00413"]


def test_a_pass_returns_a_repaired_or_lost_unit_to_service(inv, event, tv_checklist):
    t = tv_checklist
    yes = {t.power.id: "YES", t.remote.id: "YES"}
    for start in ("REPAIR", "LOST"):
        t.tv.condition = start
        check = open_check(t.tv, "TV_CHECK", ACTOR, event)
        finish_check(check, yes, ACTOR)
        assert t.tv.condition == "IN_SERVICE" and check.previous_condition == start
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    finish_check(check, yes, ACTOR)
    assert t.tv.condition == "IN_SERVICE" and check.previous_condition == "IN_SERVICE"


def test_a_serial_replacement_is_written_to_the_check_note(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    check_serial(t.tv, "SN4417", check)
    assert check.note == "Serial changed from SN4471 to SN4417"
    finish_check(check, {t.power.id: "YES", t.remote.id: "NO"}, ACTOR)
    assert check.note.startswith("Serial changed from SN4471 to SN4417; ")
    assert "Remote present?" in check.note


def test_a_new_shift_sets_aside_checks_left_open_by_an_ended_shift(client, tv_device):
    a = _open(client, "TECH00412")
    start_shift(InvDevice.query.one(), "Alex")
    db.session.commit()
    b = _open(client, "TECH00413")
    assert b["ok"] is True and b["check_id"] != a["check_id"]
    db.session.expire_all()
    assert db.session.get(InvCheck, a["check_id"]).status == "SET_ASIDE"
    assert db.session.get(InvCheck, b["check_id"]).status == "OPEN"


def test_a_stale_checklist_asks_for_a_rescan_and_rescan_reloads_the_lines(client, tv_device):
    t = tv_device
    a = _open(client, "TECH00412")
    db.session.add(InvCheckItem(item_id=t.tv_item.id, position=2, prompt="Stand present?",
                                missing_means="NOTE", is_active=True))
    db.session.commit()
    done = _finish(client, a["check_id"], {t.power.id: "YES", t.remote.id: "YES"},
                   line_ids=[t.power.id, t.remote.id])
    assert done["ok"] is False and done["changed"] is True
    assert done["message"] == "That checklist changed. Scan the unit again."
    assert len(_open(client, "TECH00412")["lines"]) == 3
    extra = InvCheckItem.query.filter_by(prompt="Stand present?").one()
    bad = _finish(client, a["check_id"],
                  {t.power.id: "YES", t.remote.id: "YES", extra.id: "YES"},
                  failed_condition="REPAIR")
    assert bad["changed"] is True
    assert InvCheck.query.one().status == "OPEN"


def test_opening_by_serial_counts_the_serial_as_checked(client, tv_device):
    assert _open(client, "SN4471")["serial_checked"] is True
    assert _open(client, "TECH00412")["serial_checked"] is False


def test_an_unused_label_is_refused_while_another_check_is_open(client, tv_device):
    a = _open(client, "TECH00412")
    b = _open(client, _issue_label())
    assert b["ok"] is False and b["open_check"]["check_id"] == a["check_id"]


def test_a_serial_step_scan_of_another_units_number_names_the_open_check(client, tv_device):
    a = _open(client, "TECH00412")
    r = client.post(SERIAL, json={"client_request_id": _rid(), "check_id": a["check_id"],
                                  "code": "TECH00413"}).get_json()
    assert r["ok"] is False and r["message"] == "TECH00412 is not finished. Finish it, or set it aside?"
    assert r["open_check"]["check_id"] == a["check_id"]


def test_the_wrong_gear_switch_keeps_the_home_container_and_logs(client, tv_device, inv):
    from app.models import SecurityAuditLog
    device = InvDevice.query.one()
    device.home_container_id = tv_device.where.id
    db.session.commit()
    r = client.post(STATION, json={"client_request_id": _rid(),
                                   "station_code": "PRINTER_CHECK"}).get_json()
    assert r["ok"] is True
    db.session.expire_all()
    device = InvDevice.query.one()
    assert device.home_container_id == tv_device.where.id
    assert any("inventory_device_station" in (e.details or "")
               for e in SecurityAuditLog.query.all())


def test_a_model_label_is_called_an_item_label(client, tv_device):
    from app.inventory import labels as lab
    code = lab.allocate(lab.PREFIX_ITEM, 1)[0]
    db.session.commit()
    a = _open(client, code)
    assert a["ok"] is False and "item label" in a["message"]


def test_the_check_keepalive_accepts_a_check_device_only(client, tv_device):
    assert client.post("/inv/scan/api/check/keepalive", json={}).get_json()["ok"] is True
    InvDevice.query.one().station_code = "COUNT"
    db.session.commit()
    assert client.post("/inv/scan/api/check/keepalive", json={}).get_json()["ok"] is False


# ---- API ----

@pytest.fixture
def tv_device(client, inv, event, tv_checklist):
    device = enrolled_client(client, station_code="TV_CHECK")
    start_shift(device, "Sam")
    db.session.commit()
    return tv_checklist


def _open(client, code, **kw):
    return client.post(OPEN, json={"client_request_id": _rid(), "code": code, **kw}).get_json()


def _finish(client, check_id, answers, **kw):
    return client.post(FINISH, json={"client_request_id": kw.pop("rid", _rid()),
                                     "check_id": check_id,
                                     "answers": {str(k): v for k, v in answers.items()},
                                     **kw}).get_json()


def test_open_returns_the_lines_and_the_unit(client, tv_device):
    t = tv_device
    a = _open(client, "TECH00412")
    assert a["ok"] is True and a["code"] == "TECH00412" and a["name"] == "Samsung 55 TV"
    assert [(x["prompt"], x["fails"]) for x in a["lines"]] == [
        ("Powered on?", True), ("Remote present?", False)]
    assert a["serial_checked"] is False and InvCheck.query.one().id == a["check_id"]


def test_wrong_gear_asks_to_switch_and_switch_changes_only_this_device(client, tv_device):
    t = tv_device
    other, _ = __import__("app.inventory.devices", fromlist=["x"]).enroll_device(
        "Scanner 9", "test:invadmin", station_code="TV_CHECK")
    db.session.commit()
    a = _open(client, "TECH00500")
    assert a["ok"] is False and a["wrong_station"] == "PRINTER_CHECK"
    assert a["message"] == "This is a printer. Switch this scanner to Printer check?"
    assert a["can_switch"] is True and InvCheck.query.count() == 0
    r = client.post(STATION, json={"client_request_id": _rid(),
                                   "station_code": "PRINTER_CHECK"}).get_json()
    assert r["ok"] is True
    devices = {d.name: d.station_code for d in InvDevice.query}
    assert devices == {"Scanner 1": "PRINTER_CHECK", "Scanner 9": "TV_CHECK"}
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/check")


def test_station_switch_refuses_non_check_stations_and_needs_a_name(client, tv_device):
    bad = client.post(STATION, json={"client_request_id": _rid(), "station_code": "COUNT"})
    assert bad.get_json()["ok"] is False
    assert InvDevice.query.one().station_code == "TV_CHECK"
    from app.inventory.shifts import end_shift
    end_shift(InvDevice.query.one(), "ENDED")
    db.session.commit()
    r = client.post(STATION, json={"client_request_id": _rid(), "station_code": "PRINTER_CHECK"})
    assert r.get_json().get("need_name") is True
    assert InvDevice.query.one().station_code == "TV_CHECK"


def test_a_model_in_neither_list_is_refused_and_serial_less_models_too(client, tv_device, inv):
    add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "LAP1")])
    db.session.commit()
    a = _open(client, "LAP1")
    assert a["ok"] is False and a["message"] == "Laptop is not checked at this station."
    assert "wrong_station" not in a
    assert _open(client, "NOPE-1")["ok"] is False


def test_an_operator_gets_the_wrong_gear_message_without_switch(client, inv, event, tv_checklist):
    login(client, "test:operator")
    a = _open(client, "TECH00500", station_code="TV_CHECK")
    assert a["wrong_station"] == "PRINTER_CHECK" and a["can_switch"] is False
    assert client.post(STATION, json={"client_request_id": _rid(),
                                      "station_code": "PRINTER_CHECK"}).get_json()["ok"] is False
    ok = _open(client, "TECH00412", station_code="TV_CHECK")
    assert ok["ok"] is True and ok["check_id"]


def test_check_routes_refuse_a_device_on_another_station(client, tv_device):
    InvDevice.query.one().station_code = "COUNT"
    db.session.commit()
    assert _open(client, "TECH00412")["ok"] is False
    assert InvCheck.query.count() == 0


def test_full_check_through_the_api_with_put_away(client, tv_device, inv):
    t = tv_device
    bin2 = make_container(inv, "C-0002", kind="BIN")
    db.session.commit()
    a = _open(client, "TECH00412")
    s = client.post(SERIAL, json={"client_request_id": _rid(), "check_id": a["check_id"],
                                  "code": "SN4471"}).get_json()
    assert s["ok"] and s["result"] == "matches" and s["message"] == "Serial matches."
    f = _finish(client, a["check_id"], {t.power.id: "YES", t.remote.id: "YES"},
                put_away_code="C-0002")
    assert f["ok"] is True and f["status"] == "PASSED" and f.get("put_away_error") is None
    db.session.expire_all()
    assert t.tv.current_container_id == bin2.id
    assert InvCheck.query.one().status == "PASSED"


def test_a_failed_put_away_is_reported_and_the_check_stays_finished(client, tv_device, inv):
    t = tv_device
    a = _open(client, "TECH00412")
    f = _finish(client, a["check_id"], {t.power.id: "YES", t.remote.id: "YES"},
                put_away_code="C-0001")
    assert f["ok"] is True and "already in C-0001" in f["put_away_error"]
    db.session.expire_all()
    assert InvCheck.query.one().status == "PASSED"
    assert prep_status(t.tv, db.session.query(EventCycle).one()) == "CHECKED"
    assert InvMovement.query.filter_by(action="MOVE").count() == 0


def test_a_repeat_finish_with_the_same_request_id_writes_once(client, tv_device):
    t = tv_device
    a = _open(client, "TECH00412")
    answers = {t.power.id: "YES", t.remote.id: "YES"}
    first = _finish(client, a["check_id"], answers, rid="same-id")
    again = _finish(client, a["check_id"], answers, rid="same-id")
    assert first == again and InvPrepStatus.query.count() == 1


def test_another_device_cannot_finish_this_check(client, tv_device):
    t = tv_device
    a = _open(client, "TECH00412")
    from app.inventory.devices import enroll_device
    device, _ = enroll_device("Scanner 2", "test:invadmin", station_code="TV_CHECK")
    start_shift(device, "Pat")
    db.session.commit()
    check = InvCheck.query.one()
    check.device_id = device.id
    db.session.commit()
    f = _finish(client, a["check_id"], {t.power.id: "YES", t.remote.id: "YES"})
    assert f["ok"] is False and InvCheck.query.one().status == "OPEN"


def test_a_checked_unit_asks_before_a_second_check(client, tv_device):
    t = tv_device
    a = _open(client, "TECH00412")
    _finish(client, a["check_id"], {t.power.id: "YES", t.remote.id: "YES"})
    again = _open(client, "TECH00412")
    assert again["ok"] is True and again["already_checked"]["result"] == "Passed"
    assert "UTC" not in again["already_checked"]["when"] and "check_id" not in again
    assert InvCheck.query.count() == 1
    forced = _open(client, "TECH00412", again=True)
    assert forced["check_id"] and InvCheck.query.count() == 2


def test_a_second_unit_while_one_is_open_names_the_first_and_offers_set_aside(client, tv_device):
    a = _open(client, "TECH00412")
    b = _open(client, "TECH00413")
    assert b["ok"] is False and "TECH00412 is not finished" in b["message"]
    assert b["open_check"]["check_id"] == a["check_id"]
    assert _open(client, "TECH00412")["check_id"] == a["check_id"]
    assert client.post(ASIDE, json={"client_request_id": _rid(),
                                    "check_id": a["check_id"]}).get_json()["ok"] is True
    assert _open(client, "TECH00413")["ok"] is True


def test_unused_label_flow_binds_then_opens(client, tv_device):
    t = tv_device
    imported = add_unit(t.tv_item, None, ACTOR, [("SERIAL", "IMP1")])
    code = _issue_label()
    first = _open(client, code)
    assert first["ok"] is True and first["unused"] is True and first["code"] == code
    b = client.post(BIND, json={"client_request_id": _rid(), "label_code": code,
                                "serial": "IMP1"}).get_json()
    assert b["ok"] is True and b["code"] == code and b["serial_checked"] is True
    assert InvCheck.query.one().unit_id == imported.id


def test_unused_label_with_no_matching_serial_offers_this_stations_models(client, tv_device):
    t = tv_device
    code = _issue_label()
    b = client.post(BIND, json={"client_request_id": _rid(), "label_code": code,
                                "serial": "BRAND-NEW"}).get_json()
    assert b["ok"] is True and b["create"] is True
    assert b["models"] == [{"id": t.tv_item.id, "name": "Samsung 55 TV"}]
    c = client.post(CREATE, json={"client_request_id": _rid(), "label_code": code,
                                  "serial": "BRAND-NEW", "item_id": t.tv_item.id}).get_json()
    assert c["ok"] is True and c["code"] == code and c["serial_checked"] is True
    assert resolve("BRAND-NEW").unit.item_id == t.tv_item.id
    other = client.post(CREATE, json={"client_request_id": _rid(), "label_code": _issue_label(),
                                      "serial": "X2", "item_id": t.printer_item.id}).get_json()
    assert other["ok"] is False and "not checked at this station" in other["message"]


def test_unused_label_bind_refusal_changes_nothing(client, tv_device):
    code = _issue_label()
    b = client.post(BIND, json={"client_request_id": _rid(), "label_code": code,
                                "serial": "SN4471"}).get_json()
    assert b["ok"] is False and "already has number TECH00412" in b["message"]
    assert resolve(code).kind == "UNUSED" and InvCheck.query.count() == 0


def test_the_api_and_page_refuse_without_a_default_event(client, tv_device):
    EventCycle.query.update({"is_default": False})
    db.session.commit()
    a = _open(client, "TECH00412")
    assert a["ok"] is False and a["message"] == NO_EVENT
    page = client.get("/inv/scan/check")
    assert page.status_code == 200 and NO_EVENT.encode() in page.data
    assert b"station_check.js" not in page.data


# ---- page and checklist control ----

def test_the_page_needs_a_check_station_and_a_shift(client, inv, event):
    enrolled_client(client, station_code="TV_CHECK")
    assert "/scan/who" in client.get("/inv/scan/check").headers["Location"]
    device = InvDevice.query.one()
    start_shift(device, "Sam")
    db.session.commit()
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/check")
    page = client.get("/inv/scan/check")
    assert page.status_code == 200 and b"station_check.js" in page.data
    assert b"TV check" in page.data
    device.station_code = "COUNT"
    db.session.commit()
    assert client.get("/inv/scan/check").status_code == 302


def test_an_operator_may_open_the_page_for_either_station(client, inv, event):
    login(client, "test:operator")
    assert b"TV check" in client.get("/inv/scan/check").data
    assert b"Printer check" in client.get("/inv/scan/check?station=PRINTER_CHECK").data


def test_checklist_page_sets_and_clears_the_station(client, inv):
    login(client, "test:invadmin")
    url = f"/inv/admin/checklists/{inv.laptop.id}/station"

    def station():
        db.session.expire_all()
        return db.session.get(SupplyItem, inv.laptop.id).check_station

    client.post(url, data={"station": "TV_CHECK"})
    assert station() == "TV_CHECK"
    client.post(url, data={"station": "PRINTER_CHECK"})
    assert station() == "PRINTER_CHECK"
    page = client.get(f"/inv/admin/checklists?item_id={inv.laptop.id}")
    assert b"Checked at" in page.data
    client.post(url, data={"station": "COUNT"})
    assert station() == "PRINTER_CHECK"
    client.post(url, data={"station": ""})
    assert station() is None


def test_the_items_page_and_the_checklist_page_set_the_same_column(client, inv):
    login(client, "test:invadmin")
    laptop = inv.laptop
    client.post(f"/inv/admin/items/{laptop.id}",
                data={"is_serialized": "1", "pack_size": "", "check_station": "PRINTER_CHECK"})
    db.session.expire_all()
    assert db.session.get(SupplyItem, laptop.id).check_station == "PRINTER_CHECK"
    page = client.get(f"/inv/admin/checklists?item_id={laptop.id}")
    assert b'value="PRINTER_CHECK" selected' in page.data
    client.post(f"/inv/admin/checklists/{laptop.id}/station", data={"station": "TV_CHECK"})
    db.session.expire_all()
    assert db.session.get(SupplyItem, laptop.id).check_station == "TV_CHECK"
    assert laptop in station_models("TV_CHECK")


# ---- page script, run under node on a stub DOM; none of this drives a browser ----

STATIC = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")

FLOW_SCRIPT = r"""
const assert = require('assert');
const c = require(process.env.CHECK_JS);
const open = {check_id: 5, code: 'TECH00412', name: 'TV', serial_checked: false,
  lines: [{id: 1, prompt: 'Powered on?', fails: true}, {id: 2, prompt: 'Remote?', fails: false}]};
const st = c.createCheck(open);
assert.equal(st.complete(), false);
st.answer(1, 'YES');
assert.equal(st.complete(), false);
st.answer(2, 'NO');
assert.equal(st.complete(), true);
assert.equal(st.isFailed(), false, 'a NO on a NOTE line passes');
assert.equal(st.needsCondition(), false);
const v = st.version();
st.answer(1, 'NO');
assert.equal(st.isFailed(), true);
assert.equal(st.needsCondition(), true);
assert.ok(st.version() > v);
st.setCondition('RETIRED');
assert.equal(st.canPutAway(), false);
assert.deepEqual(st.body(null), {check_id: 5, answers: {1: 'NO', 2: 'NO'}, line_ids: [1, 2], failed_condition: 'RETIRED'});
st.answer(1, 'YES');
assert.equal(st.condition(), null, 'a changed answer clears the condition');
assert.deepEqual(st.body('C-0002'),
  {check_id: 5, answers: {1: 'YES', 2: 'NO'}, line_ids: [1, 2], put_away_code: 'C-0002'});
const bare = c.createCheck({check_id: 6, code: 'X', name: 'Bare', lines: []});
assert.equal(bare.complete(), false);
bare.setFloor('fail', 'Cracked');
assert.equal(bare.complete(), true);
assert.equal(bare.needsCondition(), true);
bare.setCondition('REPAIR');
assert.deepEqual(bare.body(null),
  {check_id: 6, answers: {}, line_ids: [], failed_condition: 'REPAIR', note: 'Cracked'});
bare.setFloor('pass');
assert.deepEqual(bare.body(null), {check_id: 6, answers: {}, line_ids: []});
assert.equal(c.resultText({status: 'FAILED', condition: 'RETIRED'}), 'FAILED: retired');
assert.equal(c.idleText('PRINTER_CHECK'), 'Scan a printer: its asset tag, label or serial.');
console.log('flow ok');
"""

PAGE_PRELUDE = r"""
const assert = require('assert');
function Node(tag) {
  this.tag = tag; this.children = []; this.className = ''; this.style = {};
  this.handlers = {}; this._text = '';
}
Node.prototype = {
  get textContent() { return this._text; },
  set textContent(v) { this._text = v; if (v === '') { this.children = []; } },
  appendChild(c) { this.children.push(c); return c; },
  addEventListener(t, f) { this.handlers[t] = f; },
  focus() {}
};
function all(n, out) { out.push(n); n.children.forEach((c) => all(c, out)); return out; }
function texts() { return all(panel, []).map((x) => x._text); }
function buttons(label) { return all(panel, []).filter((x) => x.tag === 'button' && x._text === label); }
const panel = new Node('div');
globalThis.document = {
  currentScript: {dataset: {station: 'TV_CHECK', openUrl: '/open', serialUrl: '/serial',
    finishUrl: '/finish', setAsideUrl: '/aside', bindUrl: '/bind', createUrl: '/create',
    stationUrl: '/station', homeUrl: '/home'}},
  getElementById: (id) => id === 'panel' ? panel : new Node('input'),
  createElement: (t) => new Node(t)
};
const assigned = [];
globalThis.location = {assign: (u) => assigned.push(u)};
const writes = [];
const messages = [];
let onScan;
let leaveHold = null;
let chosen = null;
let asked = null;
globalThis.InvScan = {
  start: (cfg) => { onScan = cfg.onScan; },
  holdLeave: (fn) => { leaveHold = fn; },
  onActivity: () => {},
  offerUndo: (spec) => { globalThis.offered = spec; },
  setFlagSubject() {},
  requestId: (() => { let n = 0; return () => 'rid-' + (++n); })(),
  feedback() {}, showMessage: (t) => messages.push(t),
  askText: (label, cb) => { asked = cb; },
  choose: (options, pick, note) => { chosen = {options, pick, note}; panel.textContent = ''; },
  read: () => Promise.resolve({ok: false}),
  write: (path, body, rid) => new Promise((resolve) => writes.push({path, body, rid, resolve}))
};
require(process.env.CHECK_JS);
const tick = () => new Promise((r) => setTimeout(r, 0));
const tvOpen = {ok: true, check_id: 5, code: 'TECH00412', name: 'TV', serial_checked: false,
  lines: [{id: 1, prompt: 'Powered on?', fails: true}, {id: 2, prompt: 'Remote?', fails: false}]};
async function openCheck(code, answer) {
  const scan = onScan(code);
  await tick();
  writes[writes.length - 1].resolve(answer);
  await scan;
}
"""

PAGE_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  await openCheck('TECH00412', tvOpen);
  assert.equal(writes[0].path, '/open');
  assert.deepEqual(writes[0].body.station_code, 'TV_CHECK');
  assert.equal(leaveHold(), true, 'an open check guards the page');
  assert.ok(texts().includes('Scan the serial barcode.'));
  const serial = onScan('SN1');
  await tick();
  assert.equal(writes[1].path, '/serial');
  writes[1].resolve({ok: true, result: 'matches', message: 'Serial matches.'});
  assert.equal((await serial).ok, true, 'a serial scan finished something');
  assert.equal(messages[messages.length - 1], 'Serial matches.');

  assert.equal(buttons('YES').length, 2, 'every line has YES and NO');
  assert.equal(buttons('NO').length, 2);
  buttons('YES')[0].handlers.click();
  buttons('Finish')[0].handlers.click();
  assert.equal(messages[messages.length - 1], 'Answer every line.');
  assert.equal(writes.length, 2, 'an incomplete check sends nothing');
  buttons('NO')[1].handlers.click();
  buttons('Finish')[0].handlers.click();
  await tick();
  assert.ok(texts().includes('Scan where it goes, or Skip.'));
  assert.equal(writes.length, 2, 'a pass with a put-away step has not written yet');

  assert.equal((await onScan('TECH00413')).ok, false);
  assert.equal(messages[messages.length - 1], 'Scan a container label, or tap Skip.');
  assert.equal(writes.length, 2);

  const put = onScan('C-0002');
  await tick();
  assert.equal(writes.length, 3);
  assert.equal(writes[2].path, '/finish');
  assert.deepEqual(writes[2].body, {check_id: 5, answers: {1: 'YES', 2: 'NO'}, line_ids: [1, 2], put_away_code: 'C-0002'});
  const rid = writes[2].rid;
  const during = onScan('TECH00413');
  buttons('Skip')[0].handlers.click();
  buttons('Skip')[0].handlers.click();
  await tick();
  assert.equal(writes.length, 3, 'taps and a scan during the save send nothing');
  writes[2].resolve({ok: true, status: 'PASSED', condition: 'IN_SERVICE', code: 'TECH00412'});
  assert.equal((await put).ok, true);
  await tick();
  assert.equal(writes.length, 4, 'the waiting scan runs after the save');
  assert.equal(writes[3].path, '/open');
  assert.equal(writes[3].body.code, 'TECH00413');
  assert.ok(rid);
  writes[3].resolve({ok: false, message: 'nope'});
  await during;
  assert.equal(leaveHold(), false, 'a finished check holds nothing');
  console.log('page ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""

FAILED_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  await openCheck('TECH00412', {...tvOpen, serial_checked: true});
  assert.ok(texts().includes('Powered on?'), 'a bound unit skips the serial step');
  buttons('NO')[0].handlers.click();
  buttons('YES')[1].handlers.click();
  buttons('Finish')[0].handlers.click();
  assert.ok(chosen.options.map((o) => o.value).includes('REPAIR'));
  assert.equal(writes.length, 1);
  chosen.pick('REPAIR');
  buttons('Skip')[0].handlers.click();
  await tick();
  assert.equal(writes.length, 2);
  assert.deepEqual(writes[1].body,
    {check_id: 5, answers: {1: 'NO', 2: 'YES'}, line_ids: [1, 2], failed_condition: 'REPAIR'});
  writes[1].resolve({ok: true, status: 'FAILED', condition: 'REPAIR', code: 'TECH00412',
    put_away_error: 'C-0001 is full'});
  await tick();
  assert.ok(messages[messages.length - 1].indexOf('C-0001 is full') >= 0);
  assert.ok(texts().includes('FAILED: needs repair'));
  assert.equal(leaveHold(), false);

  // Retire skips the put-away step and writes at once.
  await openCheck('TECH00413', {...tvOpen, check_id: 8, serial_checked: true});
  buttons('NO')[0].handlers.click();
  buttons('NO')[1].handlers.click();
  buttons('Finish')[0].handlers.click();
  chosen.pick('RETIRED');
  assert.equal(chosen.note, 'Retire TECH00412? Its tag stops working.');
  assert.deepEqual(chosen.options.map((o) => o.label), ['Retire', 'Back']);
  chosen.pick(null);
  assert.deepEqual(chosen.options.map((o) => o.value), ['REPAIR', 'RETIRED', null], 'Back returns to the choice');
  chosen.pick('RETIRED');
  chosen.pick('RETIRED');
  await tick();
  assert.equal(writes[writes.length - 1].path, '/finish');
  assert.equal(writes[writes.length - 1].body.failed_condition, 'RETIRED');
  assert.equal(writes[writes.length - 1].body.put_away_code, undefined);
  console.log('failed ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""

GEAR_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  const wrong = {ok: false, wrong_station: 'PRINTER_CHECK', can_switch: true, what: 'printer',
    message: 'This is a printer. Switch this scanner to Printer check?'};
  assert.equal((await (async () => { const s = onScan('TECH00500'); await tick();
    writes[0].resolve(wrong); return s; })()).ok, false);
  assert.equal(chosen.note, wrong.message);
  assert.deepEqual(chosen.options.map((o) => o.label), ['Switch', 'Cancel']);
  chosen.pick('switch');
  await tick();
  assert.equal(writes[1].path, '/station');
  assert.equal(writes[1].body.station_code, 'PRINTER_CHECK');
  writes[1].resolve({ok: true});
  await tick();
  assert.deepEqual(assigned, ['/home']);

  // With a check open, the prompt is a plain message and nothing switches.
  await openCheck('TECH00412', tvOpen);
  buttons('No serial barcode')[0].handlers.click();
  const s2 = onScan('TECH00500');
  await tick();
  writes[writes.length - 1].resolve(wrong);
  await s2;
  assert.equal(messages[messages.length - 1], 'This is a printer. Finish this check first.');
  assert.deepEqual(assigned, ['/home']);

  // A second unit names the open check and offers Set aside, which then opens the new unit.
  const s3 = onScan('TECH00413');
  await tick();
  writes[writes.length - 1].resolve({ok: false, message: 'TECH00412 is not finished.',
    open_check: {check_id: 5, code: 'TECH00412', name: 'TV'}});
  await s3;
  assert.deepEqual(chosen.options.map((o) => o.label), ['Set aside', 'Keep checking']);
  chosen.pick('aside');
  await tick();
  const aside = writes[writes.length - 1];
  assert.equal(aside.path, '/aside');
  assert.equal(aside.body.check_id, 5);
  aside.resolve({ok: true});
  await tick();
  assert.equal(writes[writes.length - 1].path, '/open');
  assert.equal(writes[writes.length - 1].body.code, 'TECH00413');
  console.log('gear ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""

LABEL_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  const first = onScan('U-00001');
  await tick();
  writes[0].resolve({ok: true, unused: true, code: 'U-00001'});
  assert.equal((await first).ok, false);
  assert.ok(texts().includes('New label U-00001'));
  const bind = onScan('NEWSN');
  await tick();
  assert.equal(writes[1].path, '/bind');
  assert.deepEqual(writes[1].body.label_code, 'U-00001');
  assert.deepEqual(writes[1].body.serial, 'NEWSN');
  writes[1].resolve({ok: true, create: true, label_code: 'U-00001', serial: 'NEWSN',
    models: [{id: 7, name: 'Samsung 55 TV'}]});
  await bind;
  assert.deepEqual(chosen.options.map((o) => o.label), ['Samsung 55 TV', 'Cancel']);
  chosen.pick(7);
  await tick();
  assert.equal(writes[2].path, '/create');
  assert.deepEqual(writes[2].body, {label_code: 'U-00001', serial: 'NEWSN', item_id: 7});
  writes[2].resolve({ok: true, check_id: 9, code: 'U-00001', name: 'Samsung 55 TV',
    serial_checked: true, lines: []});
  await tick();
  assert.equal(buttons('PASSED').length, 1, 'a model with no lines gets a pass or fail');
  buttons('PASSED')[0].handlers.click();
  buttons('Finish')[0].handlers.click();
  buttons('Skip')[0].handlers.click();
  await tick();
  assert.deepEqual(writes[3].body, {check_id: 9, answers: {}, line_ids: []});
  writes[3].resolve({ok: true, status: 'PASSED', condition: 'IN_SERVICE', code: 'U-00001'});
  await tick();

  await openCheck('U-00002', {ok: true, check_id: 10, code: 'U-00002', name: 'Bare',
    serial_checked: true, lines: []});
  buttons('FAILED')[0].handlers.click();
  asked('');
  assert.equal(messages[messages.length - 1], 'Add a note saying what failed.');
  buttons('FAILED')[0].handlers.click();
  asked('Cracked panel');
  buttons('Finish')[0].handlers.click();
  chosen.pick('REPAIR');
  buttons('Skip')[0].handlers.click();
  await tick();
  const last = writes[writes.length - 1];
  assert.deepEqual(last.body,
    {check_id: 10, answers: {}, line_ids: [], failed_condition: 'REPAIR', note: 'Cracked panel'});
  console.log('label ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""


STALE_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  await openCheck('TECH00412', {...tvOpen, serial_checked: true});
  buttons('YES')[0].handlers.click();
  buttons('YES')[1].handlers.click();
  buttons('Finish')[0].handlers.click();
  buttons('Skip')[0].handlers.click();
  await tick();
  assert.deepEqual(writes[1].body.line_ids, [1, 2]);
  writes[1].resolve({ok: false, changed: true, message: 'That checklist changed. Scan the unit again.'});
  await tick();
  assert.equal(messages[messages.length - 1], 'That checklist changed. Scan the unit again.');
  const again = onScan('TECH00412');
  await tick();
  writes[2].resolve({...tvOpen, serial_checked: false,
    lines: [...tvOpen.lines, {id: 3, prompt: 'Stand?', fails: false}]});
  await again;
  assert.ok(texts().includes('Stand?'), 'a rescan reloads the lines');
  assert.equal(buttons('YES').length, 3);
  assert.ok(!texts().includes('Scan the serial barcode.'), 'the serial stays done');

  // Repeat taps of one model button reuse a request id.
  const c = onScan('U-00001');
  await tick();
  writes[3].resolve({ok: true, unused: true, code: 'U-00001'});
  await c;
  const bind = onScan('NEWSN');
  await tick();
  writes[4].resolve({ok: true, create: true, label_code: 'U-00001', serial: 'NEWSN',
    models: [{id: 7, name: 'TV'}]});
  await bind;
  chosen.pick(7);
  await tick();
  const first = writes[5].rid;
  writes[5].resolve({ok: false, message: 'busy'});
  await tick();
  chosen.pick(7);
  await tick();
  assert.equal(writes[6].path, '/create');
  assert.equal(writes[6].rid, first);
  console.log('stale ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""

KEEPALIVE_SCRIPT = r"""
const assert = require('assert');
const c = require(process.env.CHECK_JS);
let t = 0; let sent = 0;
const k = c.createKeepAlive({now: () => t, everyMs: 300000, send: () => { sent += 1; }});
t = 400000;
assert.equal(k.check(), false, 'no tap, no touch');
k.tap();
assert.equal(k.check(), true);
assert.equal(sent, 1);
k.tap(); t += 1000;
assert.equal(k.check(), false);
console.log('keepalive ok');
"""


def _node(tmp_path, script, name):
    path = tmp_path / name
    path.write_text(script)
    return subprocess.run(
        ["node", str(path)], capture_output=True, text=True,
        env={**os.environ, "CHECK_JS": os.path.abspath(os.path.join(STATIC, "station_check.js"))})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script,name,marker", [
    (FLOW_SCRIPT, "flow_check.js", "flow ok"),
    (PAGE_SCRIPT, "page_check.js", "page ok"),
    (FAILED_SCRIPT, "failed_check.js", "failed ok"),
    (GEAR_SCRIPT, "gear_check.js", "gear ok"),
    (LABEL_SCRIPT, "label_check.js", "label ok"),
    (STALE_SCRIPT, "stale_check.js", "stale ok"),
    (KEEPALIVE_SCRIPT, "keepalive_check.js", "keepalive ok"),
], ids=["flow", "page", "failed", "gear", "label", "stale", "keepalive"])
def test_check_page_logic_under_node(tmp_path, script, name, marker):
    """Runs the pure logic and the page script on a stub DOM; it does not drive a browser."""
    result = _node(tmp_path, script, name)
    assert result.returncode == 0, result.stderr
    assert marker in result.stdout


def test_already_checked_shows_the_local_time_not_utc(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "TV_CHECK", ACTOR, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, ACTOR)
    check.finished_at = datetime(2026, 11, 8, 2, 23)
    db.session.commit()
    # INV_LOCAL_TZ is US Eastern, five hours behind UTC in November.
    assert last_result(t.tv, event)["when"] == "Nov 7, 9:23 PM"


def test_a_unit_with_only_a_serial_is_named_by_its_serial(inv, event, tv_checklist):
    bare = add_unit(tv_checklist.tv_item, tv_checklist.where, ACTOR, [("SERIAL", "SN7777")])
    db.session.commit()
    assert display_number(bare) == "SN7777"
