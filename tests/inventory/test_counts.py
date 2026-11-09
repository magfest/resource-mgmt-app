"""Count station: blind counts, discards, stale lines, the API and the page logic."""
import json
import os
import shutil
import subprocess

import pytest

from app import db
from app.inventory import labels
from app.inventory.checks import find_balance_mismatches
from app.inventory.counts import (
    CountLineIn, count_payload, count_prompt, item_choice, start_count, submit_count,
)
from app.inventory.ledger import Actor, InventoryError, add_stock, move_stock, stock_quantity
from app.inventory.shifts import start_shift
from app.models import (
    InvContainer, InvCount, InvCountLine, InvDevice, InvMovement, InvStationItem, InvStock,
)
from tests.inventory.helpers import enrolled_client, make_container

ACTOR = Actor(user_id="test:invadmin")
START = "/inv/scan/api/count/start"
SUBMIT = "/inv/scan/api/count/submit"
FIND = "/inv/scan/api/count/find"
KEEPALIVE = "/inv/scan/api/count/keepalive"
BROWSE = "/inv/scan/api/count/browse"
_n = [0]


def _rid():
    _n[0] += 1
    return f"cnt-{_n[0]}"


def test_first_count_is_blind_and_the_second_shows_expected(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    first = start_count(tote, ACTOR)
    assert count_payload(first)["blind"] is True
    submit_count(first, [CountLineIn(inv.pens.id, kept=20)], ACTOR)
    second = start_count(tote, ACTOR)
    payload = count_payload(second)
    assert payload["blind"] is False and payload["lines"][0]["expected"] == 20


def test_an_unsubmitted_count_does_not_end_blindness(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    start_count(tote, ACTOR)
    assert count_payload(start_count(tote, ACTOR))["blind"] is True


def test_discard_records_found_then_thrown_out(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    c = start_count(tote, ACTOR)
    submit_count(c, [CountLineIn(inv.pens.id, kept=20, discarded=5, discard_reason="DRIED_OUT")],
                 ACTOR)
    assert stock_quantity(inv.pens, tote) == 20
    actions = [m.action for m in InvMovement.query.order_by(InvMovement.id)]
    assert actions == ["ADJUST", "DISCARD"]
    assert find_balance_mismatches() == []
    line = InvCountLine.query.one()
    assert (line.counted_quantity, line.discarded_quantity, line.discard_reason) == \
        (25, 5, "DRIED_OUT")


def test_adjusts_are_written_before_discards_across_lines(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    other = inv.pens.__class__(category_id=inv.category.id, item_name="Tape", unit="each")
    db.session.add(other)
    db.session.flush()
    c = start_count(tote, ACTOR)
    submit_count(c, [CountLineIn(inv.pens.id, kept=2, discarded=1, discard_reason="BROKEN"),
                     CountLineIn(other.id, kept=3)], ACTOR)
    actions = [m.action for m in InvMovement.query.order_by(InvMovement.id)]
    assert actions == ["ADJUST", "ADJUST", "DISCARD"]


def _tape(inv, tote, quantity=4, name="Tape"):
    tape = inv.pens.__class__(category_id=inv.category.id, item_name=name, unit="each")
    db.session.add(tape)
    db.session.flush()
    add_stock(tape, tote, quantity, ACTOR)
    return tape


def _line(count, item):
    return InvCountLine.query.filter_by(count_id=count.id, item_id=item.id).one()


def _held_three(inv):
    from datetime import datetime
    tote = make_container(inv, "C-0001", kind="BIN")
    tape, glue = _tape(inv, tote, 4), _tape(inv, tote, 6, "Glue")
    add_stock(inv.pens, tote, 10, ACTOR)
    first = start_count(tote, ACTOR)
    submit_count(first, [CountLineIn(inv.pens.id, kept=10), CountLineIn(tape.id, kept=4),
                         CountLineIn(glue.id, kept=6)], ACTOR)
    first.submitted_at = datetime(2026, 11, 1, 15, 0)
    db.session.flush()
    return tote, tape, glue


def test_a_partial_recount_writes_only_the_touched_line(inv):
    tote, tape, glue = _held_three(inv)
    before = InvMovement.query.count()
    c = start_count(tote, ACTOR)
    result = submit_count(c, [CountLineIn(inv.pens.id, kept=7)], ACTOR)
    assert result == {"applied": 1, "stale": []}
    assert InvMovement.query.count() == before + 1
    assert stock_quantity(inv.pens, tote) == 7
    assert stock_quantity(tape, tote) == 4 and stock_quantity(glue, tote) == 6
    assert _line(c, tape).counted_quantity is None and _line(c, tape).outcome is None
    payload = {l["item_id"]: l for l in count_payload(start_count(tote, ACTOR))["lines"]}
    assert payload[tape.id]["last_counted"] == "Nov 1, 10:00 AM"
    assert payload[glue.id]["last_counted"] == "Nov 1, 10:00 AM"
    assert payload[inv.pens.id]["last_counted"] != "Nov 1, 10:00 AM"


def test_everything_else_matches_lines_are_answered_and_dated(inv):
    tote, tape, glue = _held_three(inv)
    before = InvMovement.query.count()
    c = start_count(tote, ACTOR)
    submit_count(c, [CountLineIn(inv.pens.id, kept=7), CountLineIn(tape.id, kept=4),
                     CountLineIn(glue.id, kept=6)], ACTOR)
    assert InvMovement.query.count() == before + 1
    assert _line(c, tape).outcome == "UNCHANGED" and _line(c, tape).counted_quantity == 4
    from datetime import datetime
    c.submitted_at = datetime(2026, 11, 5, 17, 0)
    db.session.flush()
    payload = {l["item_id"]: l for l in count_payload(start_count(tote, ACTOR))["lines"]}
    assert {l["last_counted"] for l in payload.values()} == {"Nov 5, 12:00 PM"}


def test_a_count_with_nothing_touched_is_refused(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    add_stock(inv.pens, tote, 4, ACTOR)
    first = start_count(tote, ACTOR)
    with pytest.raises(InventoryError, match="Count at least one item first."):
        submit_count(first, [], ACTOR)
    assert first.submitted_at is None and stock_quantity(inv.pens, tote) == 4


def test_blindness_stays_ended_after_a_partial_recount(inv):
    tote, tape, glue = _held_three(inv)
    c = start_count(tote, ACTOR)
    submit_count(c, [CountLineIn(inv.pens.id, kept=7)], ACTOR)
    assert count_payload(start_count(tote, ACTOR))["blind"] is False


def test_stale_line_is_refused_and_applied_lines_stay(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    spare = make_container(inv, "C-0002", kind="BIN")
    tape = inv.pens.__class__(category_id=inv.category.id, item_name="Tape", unit="each")
    db.session.add(tape)
    db.session.flush()
    add_stock(inv.pens, spare, 9, ACTOR)
    c = start_count(tote, ACTOR)
    move_stock(inv.pens, spare, tote, 2, ACTOR)
    result = submit_count(c, [CountLineIn(inv.pens.id, kept=7), CountLineIn(tape.id, kept=5)],
                          ACTOR)
    assert result["stale"] == ["Blue Pen"] and result["applied"] == 1
    assert stock_quantity(inv.pens, tote) == 2
    assert stock_quantity(tape, tote) == 5
    outcomes = {l.item_id: l.outcome for l in InvCountLine.query}
    assert outcomes == {inv.pens.id: "STALE", tape.id: "APPLIED"}
    assert find_balance_mismatches() == []


def test_serialized_items_negative_numbers_and_bad_reasons_are_refused(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    c = start_count(tote, ACTOR)
    with pytest.raises(InventoryError, match="own station"):
        submit_count(c, [CountLineIn(inv.laptop.id, kept=1)], ACTOR)
    with pytest.raises(InventoryError):
        submit_count(c, [CountLineIn(inv.pens.id, kept=-1)], ACTOR)
    with pytest.raises(InventoryError, match="reason"):
        submit_count(c, [CountLineIn(inv.pens.id, kept=1, discarded=1)], ACTOR)
    with pytest.raises(InventoryError, match="once"):
        submit_count(c, [CountLineIn(inv.pens.id, kept=1), CountLineIn(inv.pens.id, kept=2)],
                     ACTOR)


def test_a_finished_count_cannot_be_submitted_again(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    c = start_count(tote, ACTOR)
    submit_count(c, [CountLineIn(inv.pens.id, kept=1)], ACTOR)
    with pytest.raises(InventoryError, match="finished"):
        submit_count(c, [CountLineIn(inv.pens.id, kept=1)], ACTOR)


def test_the_approximate_flag_is_stored_and_a_later_exact_count_clears_it(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    submit_count(start_count(tote, ACTOR),
                 [CountLineIn(inv.pens.id, kept=200, is_approximate=True)], ACTOR)
    assert InvCountLine.query.one().is_approximate is True
    assert InvStock.query.one().is_approximate is True
    submit_count(start_count(tote, ACTOR), [CountLineIn(inv.pens.id, kept=200)], ACTOR)
    assert InvStock.query.one().is_approximate is False


def test_usual_items_list_station_items_with_pack_size(inv):
    inv.pens.pack_size = 12
    db.session.add(InvStationItem(station_code="COUNT", item_id=inv.pens.id, position=1))
    db.session.add(InvStationItem(station_code="COUNT", item_id=inv.laptop.id, position=2))
    db.session.flush()
    tote = make_container(inv, "C-0001", kind="BIN")
    usual = count_payload(start_count(tote, ACTOR))["usual"]
    assert usual == [{"item_id": inv.pens.id, "name": "Blue Pen", "pack_size": 12,
                      "prompt": "How many Blue Pen? Count each one, or tap Packs of 12."}]


def test_count_prompt_names_packs_and_item_choice_carries_it(inv):
    inv.pens.pack_size = 12
    expected = "How many Blue Pen? Count each one, or tap Packs of 12."
    assert count_prompt(inv.pens) == expected
    assert item_choice(inv.pens)["prompt"] == expected


@pytest.mark.parametrize("size", [None, 0, 1])
def test_count_prompt_is_short_without_a_real_pack_size(inv, size):
    inv.pens.pack_size = size
    assert count_prompt(inv.pens) == "How many Blue Pen? Count each one."


# ---- API ----

@pytest.fixture
def ct(client, inv):
    home = make_container(inv, "C-0100", kind="LOCATION", name="Warehouse")
    db.session.commit()
    device = enrolled_client(client, station_code="COUNT")
    device.home_container_id = home.id
    start_shift(device, "test:operator")
    db.session.commit()
    return inv


def _start(client, code):
    return client.post(START, json={"client_request_id": _rid(), "container_code": code}).get_json()


def _submit(client, count_id, lines, rid=None):
    return client.post(SUBMIT, json={"client_request_id": rid or _rid(), "count_id": count_id,
                                     "lines": lines}).get_json()


def test_start_answers_blind_with_no_expected_numbers_anywhere(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    add_stock(ct.pens, tote, 37, ACTOR)
    db.session.commit()
    answer = _start(client, "C-0001")
    assert answer["ok"] is True and answer["blind"] is True and answer["lines"] == []
    assert answer["held"] == [{"item_id": ct.pens.id, "name": "Blue Pen",
                               "pack_size": ct.pens.pack_size,
                               "prompt": count_prompt(ct.pens)}]
    assert "37" not in json.dumps(answer) and "expected" not in json.dumps(answer)
    page = client.get("/inv/scan/count")
    assert b"37" not in page.data


def test_second_count_start_shows_expected(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    submit_count(start_count(tote, ACTOR), [CountLineIn(ct.pens.id, kept=20)], ACTOR)
    db.session.commit()
    answer = _start(client, "C-0001")
    assert answer["blind"] is False and answer["lines"][0]["expected"] == 20


def test_start_refuses_inactive_containers_units_and_unknown_codes(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    tote.is_active = False
    db.session.commit()
    assert "inactive" in _start(client, "C-0001")["message"]
    assert _start(client, "C-9999")["ok"] is False
    before = InvCount.query.count()
    assert _start(client, "")["ok"] is False
    assert InvCount.query.count() == before


def test_unused_container_label_returns_kinds_and_the_home_without_writing(client, ct):
    labels.allocate("C", 1)  # C-0001 issued
    before = InvContainer.query.count()
    answer = _start(client, "C-0001")
    assert answer["ok"] is True and answer["unused"] is True
    assert answer["home"] == "C-0100 Warehouse"
    assert {k["name"] for k in answer["kinds"]} == {"Location", "Pallet", "Bin"}
    assert InvContainer.query.count() == before and InvCount.query.count() == 0


def test_unused_label_with_no_home_container_says_so(client, ct):
    InvDevice.query.one().home_container_id = None
    db.session.commit()
    labels.allocate("C", 1)
    assert _start(client, "C-0001")["home"] is None


def test_creating_the_container_then_starting_a_count(client, ct):
    labels.allocate("C", 1)
    kind = ct.kinds["BIN"]
    made = client.post("/inv/scan/api/container", json={
        "client_request_id": _rid(), "label_code": "C-0001", "kind_id": kind.id}).get_json()
    assert made["ok"] is True
    assert _start(client, "C-0001")["blind"] is True
    assert InvContainer.query.filter_by(label_code="C-0001").one().parent.label_code == "C-0100"


def test_find_resolves_items_and_refuses_units_and_serialized(client, ct):
    labels.allocate("I", 1)
    ct.pens.barcode = "I-0001"
    ct.laptop.barcode = "999"
    db.session.commit()
    ok = client.post(FIND, json={"code": "I-0001"}).get_json()
    assert ok["ok"] is True and ok["items"] == [
        {"id": ct.pens.id, "name": "Blue Pen", "pack_size": None,
         "prompt": "How many Blue Pen? Count each one."}]
    bad = client.post(FIND, json={"code": "999"}).get_json()
    assert bad["ok"] is False and bad["message"] == "Count assets at their own station."
    assert client.post(FIND, json={"code": "nope"}).get_json()["ok"] is False
    found = client.post(FIND, json={"q": "pen"}).get_json()
    assert [i["name"] for i in found["items"]] == ["Blue Pen"]
    assert found["items"][0]["prompt"] == "How many Blue Pen? Count each one."


def test_find_during_a_count_refuses_a_container_scan(client, ct):
    make_container(ct, "C-0001", kind="BIN")
    db.session.commit()
    answer = client.post(FIND, json={"code": "C-0001"}).get_json()
    assert answer["ok"] is False and "count" in answer["message"].lower()


def test_submit_applies_and_a_repeat_request_writes_once(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    db.session.commit()
    count_id = _start(client, "C-0001")["count_id"]
    lines = [{"item_id": ct.pens.id, "kept": 20, "discarded": 5, "discard_reason": "BROKEN"}]
    first = _submit(client, count_id, lines, rid="same")
    again = _submit(client, count_id, lines, rid="same")
    assert first["ok"] is True and first["applied"] == 1 and first["stale"] == []
    assert again == first
    assert stock_quantity(ct.pens, tote) == 20
    assert InvMovement.query.filter(InvMovement.action.in_(["ADJUST", "DISCARD"])).count() == 2


def test_submit_saves_the_touched_line_and_leaves_the_rest(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    add_stock(ct.pens, tote, 4, ACTOR)
    db.session.commit()
    count_id = _start(client, "C-0001")["count_id"]
    empty = _submit(client, count_id, [])
    assert empty["ok"] is False and empty["message"] == "Count at least one item first."
    assert InvCount.query.one().submitted_at is None
    other = ct.pens.__class__(category_id=ct.category.id, item_name="Tape", unit="each")
    db.session.add(other)
    db.session.commit()
    fixed = _submit(client, count_id, [{"item_id": other.id, "kept": 2}])
    assert fixed["ok"] is True and stock_quantity(ct.pens, tote) == 4
    assert stock_quantity(other, tote) == 2


def test_submit_reports_stale_by_name(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    spare = make_container(ct, "C-0002", kind="BIN")
    add_stock(ct.pens, spare, 5, ACTOR)
    db.session.commit()
    count_id = _start(client, "C-0001")["count_id"]
    move_stock(ct.pens, spare, tote, 1, ACTOR)
    db.session.commit()
    answer = _submit(client, count_id, [{"item_id": ct.pens.id, "kept": 9}])
    assert answer["ok"] is True and answer["stale"] == ["Blue Pen"] and answer["applied"] == 0
    assert stock_quantity(ct.pens, tote) == 1


def test_another_device_cannot_submit_this_count(client, ct):
    make_container(ct, "C-0001", kind="BIN")
    db.session.commit()
    count_id = _start(client, "C-0001")["count_id"]
    from app.inventory.devices import enroll_device
    other, _ = enroll_device("Scanner 2", "test:invadmin", station_code="COUNT")
    db.session.get(InvCount, count_id).device_id = other.id
    db.session.commit()
    answer = _submit(client, count_id, [{"item_id": ct.pens.id, "kept": 1}])
    assert answer["ok"] is False


def test_submit_needs_a_name_first(client, inv):
    make_container(inv, "C-0001", kind="BIN")
    db.session.commit()
    device = enrolled_client(client, station_code="COUNT")
    answer = client.post(START, json={"client_request_id": _rid(),
                                      "container_code": "C-0001"}).get_json()
    assert answer["ok"] is False and answer.get("need_name") is True
    assert device.station_code == "COUNT"


def test_the_page_needs_the_count_station_and_a_shift(client, inv):
    enrolled_client(client, station_code="COUNT")
    assert "/scan/who" in client.get("/inv/scan/count").headers["Location"]
    device = InvDevice.query.one()
    start_shift(device, "test:operator")
    db.session.commit()
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/count")
    page = client.get("/inv/scan/count")
    assert page.status_code == 200 and b"station_count.js" in page.data
    device.station_code = "LOOKUP"
    db.session.commit()
    assert client.get("/inv/scan/count").status_code == 302


def test_a_signed_in_operator_may_use_the_station(client, inv):
    from tests.inventory.helpers import login
    login(client, "test:operator")
    assert client.get("/inv/scan/count").status_code == 200
    make_container(inv, "C-0001", kind="BIN")
    db.session.commit()
    assert _start(client, "C-0001")["ok"] is True


def test_a_second_submit_of_a_finished_count_is_refused_marked_and_writes_nothing(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    db.session.commit()
    count_id = _start(client, "C-0001")["count_id"]
    assert _submit(client, count_id, [{"item_id": ct.pens.id, "kept": 5}])["ok"] is True
    moves = InvMovement.query.count()
    again = _submit(client, count_id, [{"item_id": ct.pens.id, "kept": 9}])
    assert again["ok"] is False and again["finished"] is True
    assert InvMovement.query.count() == moves and stock_quantity(ct.pens, tote) == 5


def test_other_refusals_are_not_marked_finished(client, ct):
    make_container(ct, "C-0001", kind="BIN")
    db.session.commit()
    count_id = _start(client, "C-0001")["count_id"]
    answer = _submit(client, count_id, [{"item_id": ct.pens.id, "kept": -1}])
    assert answer["ok"] is False and "finished" not in answer


def test_a_quantity_above_the_limit_is_refused(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    c = start_count(tote, ACTOR)
    with pytest.raises(InventoryError, match="too large"):
        submit_count(c, [CountLineIn(inv.pens.id, kept=100001)], ACTOR)
    with pytest.raises(InventoryError, match="too large"):
        submit_count(c, [CountLineIn(inv.pens.id, kept=1, discarded=100001,
                                     discard_reason="BROKEN")], ACTOR)
    submit_count(c, [CountLineIn(inv.pens.id, kept=100000)], ACTOR)
    assert stock_quantity(inv.pens, tote) == 100000


def test_the_count_start_answer_carries_each_held_items_pack_size(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    ct.pens.pack_size = 12
    add_stock(ct.pens, tote, 24, ACTOR)
    db.session.commit()
    answer = _start(client, "C-0001")
    assert answer["held"][0]["pack_size"] == 12


def test_count_routes_refuse_a_device_on_another_station(client, ct):
    make_container(ct, "C-0001", kind="BIN")
    device = InvDevice.query.one()
    device.station_code = "RECEIVE"
    db.session.commit()
    assert _start(client, "C-0001")["ok"] is False
    assert client.post(FIND, json={"q": "pen"}).get_json()["ok"] is False
    assert client.post(SUBMIT, json={"client_request_id": _rid(), "count_id": 1,
                                     "lines": []}).get_json()["ok"] is False
    assert client.post(KEEPALIVE, json={}).get_json()["ok"] is False
    assert InvCount.query.count() == 0
    device.station_code = "LOOKUP"
    db.session.commit()
    assert _start(client, "C-0001")["ok"] is True


def test_keepalive_extends_the_open_shift(client, ct):
    from datetime import datetime, timedelta
    from app.models import InvShift
    shift = InvShift.query.one()
    shift.last_activity_at = datetime.utcnow() - timedelta(minutes=10)
    db.session.commit()
    before = shift.last_activity_at
    assert client.post(KEEPALIVE, json={}).get_json()["ok"] is True
    assert db.session.get(InvShift, shift.id).last_activity_at > before


def test_the_second_count_dates_each_line_in_local_time(inv):
    from datetime import datetime
    tote = make_container(inv, "C-0001", kind="BIN")
    first = start_count(tote, ACTOR)
    submit_count(first, [CountLineIn(inv.pens.id, kept=20)], ACTOR)
    first.submitted_at = datetime(2026, 11, 8, 2, 23)  # 9:23 PM on Nov 7 in New York
    db.session.flush()
    line = count_payload(start_count(tote, ACTOR))["lines"][0]
    assert line["expected"] == 20 and line["last_counted"] == "Nov 7, 9:23 PM"


def test_a_stale_line_keeps_the_earlier_date(inv):
    from datetime import datetime
    tote = make_container(inv, "C-0001", kind="BIN")
    spare = make_container(inv, "C-0002", kind="BIN")
    add_stock(inv.pens, spare, 9, ACTOR)
    old = start_count(tote, ACTOR)
    submit_count(old, [CountLineIn(inv.pens.id, kept=5)], ACTOR)
    old.submitted_at = datetime(2026, 11, 1, 15, 5)
    stale = start_count(tote, ACTOR)
    move_stock(inv.pens, spare, tote, 2, ACTOR)
    assert submit_count(stale, [CountLineIn(inv.pens.id, kept=7)], ACTOR)["stale"] == ["Blue Pen"]
    stale.submitted_at = datetime(2026, 11, 3, 17, 0)
    db.session.flush()
    line = count_payload(start_count(tote, ACTOR))["lines"][0]
    assert line["last_counted"] == "Nov 1, 10:05 AM"


def test_the_date_comes_from_the_latest_submitted_count_with_that_item(inv):
    from datetime import datetime
    tote = make_container(inv, "C-0001", kind="BIN")
    old = start_count(tote, ACTOR)
    submit_count(old, [CountLineIn(inv.pens.id, kept=5)], ACTOR)
    old.submitted_at = datetime(2026, 11, 1, 15, 5)
    new = start_count(tote, ACTOR)
    submit_count(new, [CountLineIn(inv.pens.id, kept=6)], ACTOR)
    new.submitted_at = datetime(2026, 11, 3, 17, 0)
    start_count(tote, ACTOR)  # open and unsubmitted: ignored
    db.session.flush()
    line = count_payload(start_count(tote, ACTOR))["lines"][0]
    assert line["last_counted"] == "Nov 3, 12:00 PM"


def test_a_blind_payload_carries_no_expected_number_and_no_date(client, ct):
    tote = make_container(ct, "C-0001", kind="BIN")
    add_stock(ct.pens, tote, 37, ACTOR)
    db.session.commit()
    answer = _start(client, "C-0001")
    assert answer["blind"] is True
    body = json.dumps(answer)
    assert "expected" not in body and "last_counted" not in body


def test_browse_lists_categories_with_countable_items_only(client, ct):
    from app.models import SupplyCategory, SupplyItem
    empty = SupplyCategory(code="EMPTY", name="Empty", is_active=True)
    off = SupplyCategory(code="OFF", name="Off", is_active=False)
    units = SupplyCategory(code="UNITS", name="Units", is_active=True)
    db.session.add_all([empty, off, units])
    db.session.flush()
    db.session.add_all([
        SupplyItem(category_id=off.id, item_name="Hidden", unit="each"),
        SupplyItem(category_id=units.id, item_name="Laptop 2", unit="each", is_serialized=True),
        SupplyItem(category_id=empty.id, item_name="Retired", unit="each", is_active=False)])
    db.session.commit()
    answer = client.post(BROWSE, json={}).get_json()
    assert answer["ok"] is True
    assert answer["categories"] == [{"id": ct.category.id, "name": "General"}]


def test_browse_lists_a_categorys_active_items_by_name_without_quantities(client, ct):
    from app.models import SupplyItem
    tote = make_container(ct, "C-0001", kind="BIN")
    ct.pens.pack_size = 12
    add_stock(ct.pens, tote, 37, ACTOR)
    db.session.add_all([
        SupplyItem(category_id=ct.category.id, item_name="Aardvark", unit="each"),
        SupplyItem(category_id=ct.category.id, item_name="Gone", unit="each", is_active=False)])
    db.session.commit()
    answer = client.post(BROWSE, json={"category_id": ct.category.id}).get_json()
    assert [i["name"] for i in answer["items"]] == ["Aardvark", "Blue Pen"]
    assert answer["items"][1] == {"id": ct.pens.id, "name": "Blue Pen", "pack_size": 12,
                                "prompt": "How many Blue Pen? Count each one, or tap Packs of 12."}
    assert "37" not in json.dumps(answer) and "quantity" not in json.dumps(answer)


def test_browse_uses_a_fixed_number_of_queries(client, ct):
    from sqlalchemy import event
    from app.models import SupplyCategory, SupplyItem
    for n in range(5):
        cat = SupplyCategory(code=f"X{n}", name=f"Cat {n}", is_active=True)
        db.session.add(cat)
        db.session.flush()
        db.session.add_all([SupplyItem(category_id=cat.id, item_name=f"I{n}{k}", unit="each")
                            for k in range(4)])
    db.session.commit()
    seen = []
    engine = db.engine
    hook = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
    event.listen(engine, "before_cursor_execute", hook)
    try:
        client.post(BROWSE, json={})
        categories = len(seen)
        seen.clear()
        client.post(BROWSE, json={"category_id": ct.category.id})
    finally:
        event.remove(engine, "before_cursor_execute", hook)
    assert categories <= 8 and len(seen) <= 8


def test_browse_refuses_a_device_on_another_station_and_bad_ids(client, ct):
    assert client.post(BROWSE, json={"category_id": "x"}).get_json()["ok"] is False
    device = InvDevice.query.one()
    device.station_code = "RECEIVE"
    db.session.commit()
    assert client.post(BROWSE, json={}).get_json()["ok"] is False


# ---- page logic, run under node; no browser is driven ----

STATIC = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")

FLOW_SCRIPT = r"""
const assert = require('assert');
const c = require(process.env.COUNT_JS);
const st = c.createCount({count_id: 7, code: 'C-0001', name: '', blind: true,
  held: [{item_id: 1, name: 'Pens'}, {item_id: 2, name: 'Tape'}], lines: [], usual: []});
assert.deepEqual(st.untouched().map((h) => h.name), ['Pens', 'Tape']);
st.setLine({id: 1, name: 'Pens'}, 20, false);
assert.deepEqual(st.untouched().map((h) => h.name), ['Tape']);
assert.equal(st.unsent(), true);
st.markNone(2);
assert.equal(st.untouched().length, 0);
st.setDiscard(1, 5, 'DRIED_OUT');
st.setLine({id: 1, name: 'Pens'}, 22, true);   // a recount keeps the discard
assert.deepEqual(st.payload(), [
  {item_id: 1, kept: 22, is_approximate: true, discarded: 5, discard_reason: 'DRIED_OUT'},
  {item_id: 2, kept: 0, is_approximate: false, discarded: 0, discard_reason: null}]);
assert.equal(st.lineCount(), 2);
assert.equal(c.staleText(['Pens', 'Tape']),
  'Pens changed while you counted. Count it again. Tape changed while you counted. Count it again.');
assert.equal(c.lineText({kept: 0, is_approximate: false, discarded: 0}), 'none');
assert.equal(c.lineText({kept: 20, is_approximate: true, discarded: 5, discard_reason: 'DRIED_OUT'}),
  'about 20, 5 thrown out (Dried out)');
console.log('flow ok');
"""

REST_SCRIPT = r"""
const assert = require('assert');
const c = require(process.env.COUNT_JS);
const st = c.createCount({count_id: 1, code: 'C-1', blind: false,
  held: [{item_id: 1, name: 'Pens'}, {item_id: 2, name: 'Tape'}, {item_id: 3, name: 'Glue'}],
  lines: [{item_id: 1, expected: 20}, {item_id: 2, expected: 5}, {item_id: 3, expected: 9}]});
assert.equal(st.canMatchRest(), true);
st.setLine({id: 1, name: 'Pens'}, 18, false);
st.matchRest();
assert.equal(st.canMatchRest(), false, 'nothing is left to confirm');
assert.deepEqual(st.payload().map((l) => [l.item_id, l.kept]), [[1, 18], [2, 5], [3, 9]]);
st.markNone(3);   // an answered line can be changed afterwards
assert.equal(st.line(3).kept, 0);
const blind = c.createCount({count_id: 2, code: 'C-2', blind: true,
  held: [{item_id: 1, name: 'Pens'}], lines: []});
assert.equal(blind.canMatchRest(), false, 'a blind count has no expected numbers');
console.log('rest ok');
"""

PAGE_PRELUDE = r"""
const assert = require('assert');
function Node(tag) {
  this.tag = tag; this.children = []; this.className = ''; this.style = {};
  this.handlers = {}; this._text = ''; this.disabled = false;
}
Node.prototype = {
  get textContent() { return this._text; },
  set textContent(v) { this._text = v; if (v === '') { this.children = []; } },
  appendChild(c) { this.children.push(c); return c; },
  insertBefore(c) { this.children.unshift(c); return c; },
  get firstChild() { return this.children[0] || null; },
  addEventListener(t, f) { this.handlers[t] = f; },
  setAttribute() {}, focus() {}
};
function all(n, out) { out.push(n); n.children.forEach((c) => all(c, out)); return out; }
function texts(n) { return all(n, []).map((x) => x._text); }
function find(n, label) { return all(n, []).find((x) => x.tag === 'button' && x._text === label); }
const panel = new Node('div');
globalThis.document = {
  currentScript: {dataset: {startUrl: '/start', submitUrl: '/submit', findUrl: '/find',
                            containerUrl: '/container'}},
  getElementById: (id) => id === 'panel' ? panel : new Node('input'),
  createElement: (t) => new Node(t)
};
const writes = [];
const reads = [];
const messages = [];
let onScan;
let leaveHold = null;
let padCb = null;
globalThis.InvScan = {
  start: (cfg) => { onScan = cfg.onScan; },
  holdLeave: (fn) => { leaveHold = fn; },
  onActivity: () => {},
  offerUndo: (spec) => { globalThis.offered = spec; },
  setFlagSubject() {},
  requestId: (() => { let n = 0; return () => 'rid-' + (++n); })(),
  feedback() {}, showMessage: (t) => messages.push(t),
  numberPad: (opts, cb) => { padCb = cb; },
  askText: (l, cb) => cb(null),
  choose: (opts, pick) => { panel.textContent = ''; },
  read: (path, body) => {
    reads.push([path, body]);
    if (body.code === 'I-1') {
      return Promise.resolve({ok: true, items: [{id: 1, name: 'Pens', pack_size: null}]});
    }
    return Promise.resolve({ok: false, message: 'nope'});
  },
  write: (path, body, rid) => new Promise((resolve) => writes.push({path, body, rid, resolve}))
};
globalThis.confirm = () => globalThis.confirmAnswer;
globalThis.confirmAnswer = true;
require(process.env.COUNT_JS);
const tick = () => new Promise((r) => setTimeout(r, 0));
const startAnswer = {ok: true, count_id: 5, code: 'C-0001', name: '', blind: true,
  held: [{item_id: 1, name: 'Pens', pack_size: 12}], lines: [], usual: []};
"""

REST_PAGE_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  const started = onScan('C-0001');
  await tick();
  writes[0].resolve({ok: true, count_id: 5, code: 'C-0001', name: '', blind: false,
    held: [{item_id: 1, name: 'Pens'}, {item_id: 2, name: 'Tape'}], usual: [],
    lines: [{item_id: 1, expected: 20}, {item_id: 2, expected: 5}]});
  await started;
  assert.ok(texts(panel).includes('In here (2)'));
  const before = messages.length;
  find(panel, 'Submit count (0 lines)').handlers.click();
  await tick();
  assert.equal(writes.length, 1, 'nothing touched sends nothing');
  assert.equal(messages[messages.length - 1], 'Count at least one item first.');
  find(panel, 'Everything else matches').handlers.click();
  assert.equal(find(panel, 'Everything else matches'), undefined, 'gone once nothing is untouched');
  assert.ok(find(panel, 'Submit count (2 lines)'));
  console.log('rest page ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""

PAGE_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  const first = onScan('C-0001');
  await tick();
  assert.equal(writes.length, 1);
  writes[0].resolve({ok: true, count_id: 5, code: 'C-0001', name: '', blind: true,
    held: [{item_id: 1, name: 'Pens'}, {item_id: 2, name: 'Tape'}], lines: [], usual: []});
  assert.equal((await first).ok, true, 'a started count finished something');
  assert.equal(leaveHold(), false, 'no unsent lines yet');

  // Recounting one item twice in a row: neither item scan reports ok, so the repeat guard stays empty.
  for (let i = 0; i < 2; i++) {
    const r = await onScan('I-1');
    assert.equal(r.ok, false);
    padCb({quantity: 10 + i, about: false});
  }
  assert.equal(leaveHold(), true, 'an unsent line guards the page');

  // An untouched held item does not block Submit.
  assert.ok(texts(panel).includes('Tape'));

  // Submit, then a scan during the write, then a second tap on Submit.
  find(panel, 'Submit count (1 line)').handlers.click();
  await tick();
  assert.equal(writes.length, 2);
  const rid = writes[1].rid;
  const during = onScan('I-1');
  find(panel, 'Submit count (1 line)').handlers.click();
  await tick();
  assert.equal(writes.length, 2, 'a second tap does not send a second submit');
  assert.equal(reads.filter((x) => x[0] === '/find').length, 2, 'the scan waits for the submit');
  writes[1].resolve({ok: true, applied: 1, stale: ['Pens']});
  const waited = await during;
  assert.equal(waited.ok, false);
  assert.equal(reads.filter((x) => x[0] === '/find').length, 2, 'the waiting scan did not open a pad');
  assert.ok(texts(panel).some((t) => t.indexOf('Pens changed while you counted. Count it again.') >= 0));
  assert.equal(leaveHold(), false, 'a finished count holds nothing');
  assert.equal(writes[1].path, '/submit');
  assert.ok(rid);
  console.log('page ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""


FINISHED_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  // A held item's Count button opens the pad with that item's real pack size.
  let pad = null;
  globalThis.InvScan.numberPad = (opts, cb) => { pad = opts; };
  const started = onScan('C-0001');
  await tick();
  writes[0].resolve(startAnswer);
  await started;
  find(panel, 'Count').handlers.click();
  assert.equal(pad.packSize, 12);
  find(panel, 'Cancel count');   // present while a count is open

  // The server says the count is already saved: the screen drops it and scans start again.
  globalThis.InvScan.numberPad = (opts, cb) => { cb({quantity: 3, about: false}); };
  find(panel, 'Count').handlers.click();
  find(panel, 'Submit count (1 line)').handlers.click();
  await tick();
  writes[1].resolve({ok: false, message: 'This count is already finished.', finished: true});
  await tick();
  assert.equal(messages[messages.length - 1],
    'This count was already saved. Scan where you are counting to count again.');
  assert.ok(texts(panel).includes('Count: scan where you are counting to start.'));
  assert.equal(leaveHold(), false);
  const next = onScan('C-0002');
  await tick();
  assert.equal(writes[2].path, '/start', 'a container scan starts a new count');
  writes[2].resolve(startAnswer);
  await next;

  // Cancel asks first, then clears the unsent count without any server call.
  globalThis.InvScan.numberPad = (opts, cb) => { cb({quantity: 1, about: false}); };
  find(panel, 'Count').handlers.click();
  assert.equal(leaveHold(), true);
  globalThis.confirmAnswer = false;
  find(panel, 'Cancel count').handlers.click();
  assert.equal(leaveHold(), true, 'declining the confirm keeps the count');
  globalThis.confirmAnswer = true;
  find(panel, 'Cancel count').handlers.click();
  assert.equal(leaveHold(), false);
  assert.equal(writes.length, 3, 'cancel sends nothing');
  assert.ok(texts(panel).includes('Count: scan where you are counting to start.'));
  console.log('finished ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""

BROWSE_SCRIPT = PAGE_PRELUDE + r"""
(async () => {
  const started = onScan('C-0001');
  await tick();
  writes[0].resolve(startAnswer);
  await started;
  const heads = () => texts(panel);
  assert.ok(heads().includes('First count: C-0001'), 'blind header names the job');
  assert.ok(find(panel, 'Browse') && find(panel, 'Search'));

  const chosen = [];
  globalThis.InvScan.choose = (opts, pick, note) => {
    panel.textContent = '';
    chosen.push(opts.map((o) => o.label));
    chosen.pick = pick;
  };
  const answers = {
    '{}': {ok: true, categories: [{id: 3, name: 'Markers'}]},
    '{"category_id":3}': {ok: true, items: [{id: 9, name: 'Red marker', pack_size: 12}]}
  };
  globalThis.InvScan.read = (path, body) => {
    reads.push([path, body]);
    return Promise.resolve(answers[JSON.stringify(body)]);
  };
  globalThis.document.currentScript.dataset.browseUrl = '/browse';
  find(panel, 'Browse').handlers.click();
  await tick();
  assert.deepEqual(chosen[0], ['Markers']);
  assert.ok(find(panel, 'Back'));
  chosen.pick({id: 3, name: 'Markers'});
  await tick();
  assert.deepEqual(chosen[1], ['Red marker']);
  find(panel, 'Back').handlers.click();
  await tick();
  assert.deepEqual(chosen[2], ['Markers'], 'Back returns to the categories');
  chosen.pick({id: 3, name: 'Markers'});
  await tick();
  let pad = null;
  globalThis.InvScan.numberPad = (opts, cb) => { pad = opts; };
  chosen.pick({id: 9, name: 'Red marker', pack_size: 12});
  assert.equal(pad.packSize, 12, 'the pad opens as Search does');
  console.log('browse ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""

HEADER_SCRIPT = r"""
const assert = require('assert');
const c = require(process.env.COUNT_JS);
assert.equal(c.headTitle({blind: true, code: 'C-0010', name: 'SupplyOps Pallet 1'}),
  'First count: C-0010 SupplyOps Pallet 1');
assert.equal(c.headTitle({blind: false, code: 'C-0010', name: ''}), 'Recount: C-0010');
const st = c.createCount({count_id: 1, code: 'C-1', blind: false,
  held: [{item_id: 1, name: 'Pens'}], lines: [{item_id: 1, expected: 20, last_counted: 'Nov 7, 9:23 PM'}]});
assert.equal(c.heldNote(st.expectedFor(1), st.lastCountedFor(1)), '20 on Nov 7, 9:23 PM');
assert.equal(c.heldNote(20, null), '20');
assert.equal(c.heldNote(null, null), '');
console.log('header ok');
"""

KEEPALIVE_SCRIPT = r"""
const assert = require('assert');
const c = require(process.env.COUNT_JS);
let t = 0; let sent = 0;
const k = c.createKeepAlive({now: () => t, everyMs: 300000, send: () => { sent += 1; }});
t = 100000;
assert.equal(k.check(), false, 'no tap, no touch: an abandoned screen can expire');
k.tap();
t = 200000;
assert.equal(k.check(), false, 'under five minutes since the start');
t = 700000;
assert.equal(k.check(), true);
assert.equal(sent, 1);
t = 800000;
assert.equal(k.check(), false, 'the tap was spent on the last touch');
k.tap();
t = 900000;
assert.equal(k.check(), false, 'under five minutes since the last touch');
t = 1300000;
assert.equal(k.check(), true);
k.tap();
k.reset();
t = 9000000;
assert.equal(k.check(), false, 'a new count starts untapped');
assert.equal(sent, 2);
console.log('keepalive ok');
"""


def _node(tmp_path, script, name):
    path = tmp_path / name
    path.write_text(script)
    return subprocess.run(
        ["node", str(path)], capture_output=True, text=True,
        env={**os.environ, "COUNT_JS": os.path.abspath(os.path.join(STATIC, "station_count.js"))})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_count_state_logic_under_node(tmp_path):
    """Runs the pure line logic under node; it does not drive a browser."""
    result = _node(tmp_path, FLOW_SCRIPT, "flow_check.js")
    assert result.returncode == 0, result.stderr
    assert "flow ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_everything_else_matches_under_node(tmp_path):
    """Runs the line logic and the page script on a stub DOM under node; no browser is driven."""
    for script, name, ok in ((REST_SCRIPT, "rest_check.js", "rest ok"),
                             (REST_PAGE_SCRIPT, "rest_page_check.js", "rest page ok")):
        result = _node(tmp_path, script, name)
        assert result.returncode == 0, result.stderr
        assert ok in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_scan_and_a_second_tap_during_submit_wait_and_double_nothing(tmp_path):
    """Runs the page script on a stub DOM under node; it does not drive a browser."""
    result = _node(tmp_path, PAGE_SCRIPT, "page_check.js")
    assert result.returncode == 0, result.stderr
    assert "page ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_finished_count_clears_and_cancel_discards_under_node(tmp_path):
    """Runs the page script on a stub DOM under node; it does not drive a browser."""
    result = _node(tmp_path, FINISHED_SCRIPT, "finished_check.js")
    assert result.returncode == 0, result.stderr
    assert "finished ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_keepalive_needs_a_tap_and_five_minutes_under_node(tmp_path):
    result = _node(tmp_path, KEEPALIVE_SCRIPT, "keepalive_check.js")
    assert result.returncode == 0, result.stderr
    assert "keepalive ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browse_then_back_returns_to_the_categories_under_node(tmp_path):
    """Runs the page script on a stub DOM under node; it does not drive a browser."""
    result = _node(tmp_path, BROWSE_SCRIPT, "browse_check.js")
    assert result.returncode == 0, result.stderr
    assert "browse ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_header_and_held_note_text_under_node(tmp_path):
    result = _node(tmp_path, HEADER_SCRIPT, "header_check.js")
    assert result.returncode == 0, result.stderr
    assert "header ok" in result.stdout
