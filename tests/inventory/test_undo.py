"""Undo last: a check or count from this shift taken back with compensating rows."""
import os
import shutil
import subprocess
from datetime import timedelta

import pytest

from app import db
from app.inventory.cards import card_for
from app.inventory.describe import describe_movement
from app.inventory.checks import (
    find_balance_mismatches, finish_check, open_check,
)
from app.inventory.counts import CountLineIn, count_payload, start_count, submit_count
from app.inventory.flags import raise_flag
from app.inventory.ledger import (
    Actor, InventoryError, add_stock, add_unit, move_stock, stock_quantity,
)
from app.inventory.resolver import resolve
from app.inventory.shifts import start_shift
from app.inventory.undo import UNDO_WINDOW, undo_last
from app.models import (
    EventCycle, InvCheck, InvCount, InvCountLine, InvMovement, InvPrepStatus, InvScanRequest,
    InvUnitIdentifier,
)
from tests.inventory.helpers import (
    enrolled_client, latest_prep_status as prep_status, login, make_container,
)
from tests.inventory.test_checks import event, tv_checklist  # noqa: F401  (fixtures)

ADMIN = Actor(user_id="test:invadmin")
UNDO = "/inv/scan/api/undo"
TOO_LATE = "Too late to undo."
CHANGED = "Something changed since; nothing was undone."
_n = [0]


def _rid():
    _n[0] += 1
    return f"undo-{_n[0]}"


@pytest.fixture
def shift(client, inv):
    device = enrolled_client(client, station_code="UNIT")
    shift = start_shift(device, "Sam")
    db.session.commit()
    return shift


def _actor(shift):
    return Actor(shift_id=shift.id, device_id=shift.device_id)


def _fail_and_retire(t, event, actor):
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "NO", t.remote.id: "YES"}, actor,
                 failed_condition="RETIRED")
    db.session.commit()
    return check


def _later(check_or_count, seconds):
    stamp = getattr(check_or_count, "finished_at", None) or check_or_count.submitted_at
    return stamp + timedelta(seconds=seconds)


# ---- checks ----

def test_undo_a_failed_check_restores_condition_and_voids_prep(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "NO", t.remote.id: "YES"}, actor, failed_condition="REPAIR")
    db.session.commit()
    answer = undo_last(shift, now=_later(check, 5))
    assert answer["undone"] == "check"
    assert answer["message"].startswith("Check of TECH00412 undone.")
    assert t.tv.condition == "IN_SERVICE"
    assert prep_status(t.tv) is None
    assert check.status == "VOIDED"
    rows = [r.status for r in InvPrepStatus.query.order_by(InvPrepStatus.id)]
    assert rows == ["FAILED_CHECK", "VOIDED"]


def test_undo_a_pass_that_cleared_repair_puts_repair_back(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    t.tv.condition = "REPAIR"
    db.session.commit()
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    db.session.commit()
    assert t.tv.condition == "IN_SERVICE"
    undo_last(shift, now=_later(check, 5))
    assert t.tv.condition == "REPAIR" and check.status == "VOIDED"


def test_undo_reactivates_exactly_the_identifiers_the_retirement_took(
        inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    # A serial retired long before this check stays retired.
    from app.inventory.ledger import add_identifier, retire_identifier
    old = add_identifier(t.tv, "MAC", "AA:BB:CC:DD:EE:01")
    retire_identifier(old)
    db.session.commit()
    check = _fail_and_retire(t, event, actor)
    taken = InvUnitIdentifier.query.filter_by(unit_id=t.tv.id).all()
    assert all(i.retired_at is not None for i in taken)
    answer = undo_last(shift, now=_later(check, 5))
    assert t.tv.condition == "IN_SERVICE"
    active = {i.kind for i in InvUnitIdentifier.query.filter_by(unit_id=t.tv.id)
              if i.retired_at is None}
    assert active == {"MAGFEST_TAG", "SERIAL"}
    assert InvUnitIdentifier.query.get(old.id).retired_at is not None
    assert answer["undone"] == "check"


def test_undo_is_refused_whole_when_a_retired_tag_was_taken(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    check = _fail_and_retire(t, event, actor)
    other = add_unit(t.tv_item, t.where, ADMIN, [("MAGFEST_TAG", "TECH00412")])
    db.session.commit()
    with pytest.raises(InventoryError) as refused:
        undo_last(shift, now=_later(check, 5))
    assert str(refused.value) == CHANGED
    db.session.rollback()
    db.session.refresh(t.tv)
    assert t.tv.condition == "RETIRED" and InvCheck.query.get(check.id).status == "FAILED"
    assert not [i for i in InvUnitIdentifier.query.filter_by(unit_id=t.tv.id)
                if i.retired_at is None]
    assert other.id != t.tv.id


def test_undo_says_what_stays_after_a_put_away(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    bin2 = make_container(inv, "C-0002", kind="BIN")
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    from app.inventory.checks import put_away
    assert put_away(t.tv, "C-0002", actor) is None
    db.session.commit()
    answer = undo_last(shift, now=_later(check, 5))
    assert answer["message"] == "Check of TECH00412 undone. It stays in C-0002."
    assert t.tv.current_container_id == bin2.id


def test_look_up_shows_not_checked_after_undo_and_a_new_check_opens(
        client, inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    db.session.commit()
    assert any(x.startswith("Works \u00b7 ") for x in card_for(resolve("TECH00412"), event)["lines"])
    undo_last(shift, now=_later(check, 5))
    db.session.commit()
    assert "Not checked" in card_for(resolve("TECH00412"), event)["lines"]
    answer = client.post("/inv/scan/api/unit/scan", json={
        "client_request_id": _rid(), "code": "TECH00412"}).get_json()
    assert answer["ok"] is True and "already_checked" not in answer
    assert answer["check_id"] != check.id


def test_undo_after_the_window_or_another_write_is_refused(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    db.session.commit()
    with pytest.raises(InventoryError, match=TOO_LATE):
        undo_last(shift, now=_later(check, 31))
    assert UNDO_WINDOW == timedelta(seconds=30)
    # Another write on the shift ends the window even inside 30 seconds.
    open_check(t.tv2, "UNIT", actor, event)
    db.session.commit()
    with pytest.raises(InventoryError, match=TOO_LATE):
        undo_last(shift, now=_later(check, 5))
    assert check.status == "PASSED" and t.tv.condition == "IN_SERVICE"


def test_a_stock_write_on_the_shift_ends_the_window(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    add_stock(inv.pens, t.where, 3, actor)
    db.session.commit()
    with pytest.raises(InventoryError, match=TOO_LATE):
        undo_last(shift, now=_later(check, 5))


def test_a_flag_does_not_end_the_window(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    raise_flag(actor, "UNIT", "DAMAGED", unit=t.tv2)
    db.session.commit()
    assert undo_last(shift, now=_later(check, 5))["undone"] == "check"


def test_undo_refuses_when_nothing_is_there_and_when_already_undone(
        inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    with pytest.raises(InventoryError, match="Nothing to undo."):
        undo_last(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    db.session.commit()
    undo_last(shift, now=_later(check, 5))
    db.session.commit()
    with pytest.raises(InventoryError, match="Nothing to undo."):
        undo_last(shift, now=_later(check, 6))
    assert InvPrepStatus.query.filter_by(status="VOIDED").count() == 1


def test_undo_refuses_when_the_unit_changed_since(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "NO", t.remote.id: "YES"}, actor, failed_condition="REPAIR")
    t.tv.condition = "LOST"
    db.session.commit()
    with pytest.raises(InventoryError) as refused:
        undo_last(shift, now=_later(check, 5))
    assert str(refused.value) == CHANGED


# ---- counts ----

def test_undo_a_count_writes_the_inverse(inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    add_stock(inv.pens, tote, 12, ADMIN)
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=20)], actor)
    db.session.commit()
    assert stock_quantity(inv.pens, tote) == 20
    answer = undo_last(shift, now=_later(count, 5))
    db.session.commit()
    assert answer == {"undone": "count", "message": "Count of C-0010 undone."}
    assert stock_quantity(inv.pens, tote) == 12
    assert find_balance_mismatches() == []
    undo_rows = InvMovement.query.filter_by(note="undo").all()
    assert [(m.action, m.quantity) for m in undo_rows] == [("ADJUST", 8)]


def test_undo_of_a_partial_recount_leaves_unanswered_items_alone(inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    add_stock(inv.pens, tote, 12, ADMIN)
    tape = inv.pens.__class__(category_id=inv.category.id, item_name="Tape", unit="each")
    db.session.add(tape)
    db.session.flush()
    add_stock(tape, tote, 4, ADMIN)
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=20)], actor)
    db.session.commit()
    undo_last(shift, now=_later(count, 5))
    db.session.commit()
    assert stock_quantity(inv.pens, tote) == 12 and stock_quantity(tape, tote) == 4
    assert [(m.item_id, m.action) for m in InvMovement.query.filter_by(note="undo")] == \
        [(inv.pens.id, "ADJUST")]
    lines = {l.item_id: l.outcome for l in InvCountLine.query.filter_by(count_id=count.id)}
    assert lines == {inv.pens.id: "UNDONE", tape.id: None}
    assert find_balance_mismatches() == []


def test_undo_a_count_with_a_discard_returns_every_balance(inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    tape = inv.pens.__class__(category_id=inv.category.id, item_name="Tape", unit="each")
    db.session.add(tape)
    db.session.flush()
    add_stock(inv.pens, tote, 12, ADMIN)
    add_stock(tape, tote, 4, ADMIN)
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [
        CountLineIn(inv.pens.id, kept=20, discarded=5, discard_reason="BROKEN"),
        CountLineIn(tape.id, kept=1)], actor)
    db.session.commit()
    undo_last(shift, now=_later(count, 5))
    db.session.commit()
    assert stock_quantity(inv.pens, tote) == 12 and stock_quantity(tape, tote) == 4
    assert find_balance_mismatches() == []
    assert all(m.note == "undo" and m.action == "ADJUST"
               for m in InvMovement.query.filter(InvMovement.note.isnot(None)))
    assert {l.outcome for l in InvCountLine.query} == {"UNDONE"}


def test_undo_a_count_is_refused_after_a_later_movement_on_its_pair(inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    other = make_container(inv, "C-0011", kind="BIN")
    add_stock(inv.pens, tote, 12, ADMIN)
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=20)], actor)
    # Another scanner moves the stock; this shift wrote nothing since.
    move_stock(inv.pens, tote, other, 5, ADMIN)
    db.session.commit()
    with pytest.raises(InventoryError) as refused:
        undo_last(shift, now=_later(count, 5))
    assert str(refused.value) == CHANGED
    db.session.rollback()
    assert stock_quantity(inv.pens, tote) == 15
    assert InvMovement.query.filter_by(note="undo").count() == 0


def test_an_undone_count_does_not_end_first_count_blindness(inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=7)], actor)
    db.session.commit()
    undo_last(shift, now=_later(count, 5))
    db.session.commit()
    assert count_payload(start_count(tote, actor))["blind"] is True


def test_a_count_cannot_be_undone_twice(client, inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    add_stock(inv.pens, tote, 12, ADMIN)
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=20)], actor)
    db.session.commit()
    undo_last(shift, now=_later(count, 5))
    db.session.commit()
    with pytest.raises(InventoryError, match="Nothing to undo."):
        undo_last(shift, now=_later(count, 6))
    assert stock_quantity(inv.pens, tote) == 12


def test_an_unchanged_count_is_undone_without_movements(inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    add_stock(inv.pens, tote, 12, ADMIN)
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=12)], actor)
    db.session.commit()
    before = InvMovement.query.count()
    assert undo_last(shift, now=_later(count, 5))["undone"] == "count"
    assert InvMovement.query.count() == before
    assert InvCount.query.get(count.id).lines[0].outcome == "UNDONE"


# ---- the endpoint ----

def _post(client, **body):
    return client.post(UNDO, json={"client_request_id": _rid(), **body})


def test_the_endpoint_undoes_the_last_count_and_a_repeat_id_writes_once(client, inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    add_stock(inv.pens, tote, 12, ADMIN)
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=20)], actor)
    db.session.commit()
    first = _post(client, client_request_id="same-id").get_json()
    second = _post(client, client_request_id="same-id").get_json()
    assert first["ok"] is True and first["undone"] == "count"
    assert first == second
    assert InvMovement.query.filter_by(note="undo").count() == 1
    assert InvScanRequest.query.filter_by(client_request_id="same-id").count() == 1


def test_the_endpoint_refuses_late_and_says_so(client, inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0010", kind="BIN")
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=3)], actor)
    count.submitted_at = count.submitted_at - timedelta(seconds=60)
    db.session.commit()
    answer = _post(client).get_json()
    assert answer == {"ok": False, "message": TOO_LATE}
    assert stock_quantity(inv.pens, tote) == 3


def test_the_endpoint_needs_json_a_shift_and_a_scanner(client, inv):
    device = enrolled_client(client, station_code="UNIT")
    db.session.commit()
    assert client.post(UNDO, data="x", content_type="text/plain").status_code == 415
    answer = _post(client).get_json()
    assert answer["ok"] is False and answer.get("need_name") is True
    device.station_code = "PRINT"
    db.session.commit()
    refused = _post(client)
    assert refused.status_code == 403 and refused.get_json()["ok"] is False


def test_an_operator_undoes_only_their_own_work(client, inv):
    tote = make_container(inv, "C-0010", kind="BIN")
    db.session.commit()
    mine, theirs = Actor(user_id="test:operator"), Actor(user_id="test:invadmin")
    count = start_count(tote, theirs)
    submit_count(count, [CountLineIn(inv.pens.id, kept=3)], theirs)
    db.session.commit()
    login(client, "test:operator")
    answer = _post(client).get_json()
    assert answer == {"ok": False, "message": "Nothing to undo."}
    count = start_count(tote, mine)
    submit_count(count, [CountLineIn(inv.pens.id, kept=5)], mine)
    db.session.commit()
    answer = _post(client).get_json()
    assert answer["ok"] is True and answer["undone"] == "count"
    assert stock_quantity(inv.pens, tote) == 3


# ---- the shell, under node; no browser is driven ----

STATIC = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")

NODE_SCRIPT = r"""
const assert = require('assert');
const {createUndoControl} = require(process.env.SCAN_JS);
const tick = () => new Promise(r => setImmediate(r));

(async () => {
  let t = 0;
  const sent = [];
  let settle = null;
  const ctl = createUndoControl({
    now: () => t, windowMs: 30000,
    send: (id) => { sent.push(id); return new Promise(r => { settle = r; }); }
  });
  assert.equal(ctl.view().visible, false);
  assert.equal(ctl.scan(), null, 'a scan with no offer waits for nothing');

  ctl.offer({kind: 'check', name: 'FEST10001'});
  assert.deepEqual([ctl.view().visible, ctl.view().text], [true, 'Undo check of FEST10001']);
  t = 29999;
  assert.equal(ctl.view().visible, true);
  t = 30000;
  assert.equal(ctl.view().visible, false, 'hidden at 30 seconds');
  assert.equal(await ctl.tap(), null, 'a tap after the window sends nothing');
  assert.equal(sent.length, 0);

  // The hardest path: a scan arrives while the undo write is in flight.
  t = 0;
  ctl.offer({kind: 'count', name: 'C-0010'});
  assert.equal(ctl.view().text, 'Undo count of C-0010');
  const first = ctl.tap();
  const second = ctl.tap();
  assert.strictEqual(first, second, 'a repeat tap shares the one request');
  assert.equal(sent.length, 1);
  assert.equal(ctl.view().busy, true);
  let scanReleased = false;
  const held = ctl.scan();
  assert.ok(held, 'the scan waits for the undo');
  held.then(() => { scanReleased = true; });
  await tick();
  assert.equal(scanReleased, false, 'the scan does not run before the undo settles');
  assert.equal(ctl.view().visible, false, 'the scan ended the offer');
  settle({ok: true, message: 'Count of C-0010 undone.'});
  const done = await first;
  assert.deepEqual([done.ok, done.message], [true, 'Count of C-0010 undone.']);
  await held;
  assert.equal(scanReleased, true);
  assert.equal(ctl.view().visible, false);

  // A cancelled save keeps the offer and reuses the request id.
  ctl.offer({kind: 'check', name: 'FEST10002'});
  const a = ctl.tap();
  settle({ok: false, cancelled: true, message: 'Not saved.'});
  assert.equal((await a).ok, false);
  assert.equal(ctl.view().visible, true);
  const b = ctl.tap();
  assert.equal(sent[sent.length - 1], sent[sent.length - 2], 'same request id after a cancel');
  settle({ok: false, message: 'Too late to undo.'});
  const refused = await b;
  assert.equal(refused.message, 'Too late to undo.');
  assert.equal(ctl.view().visible, false, 'a refusal hides the button');

  // Any other write hides the offer; a send that rejects still settles.
  ctl.offer({kind: 'check', name: 'FEST10003'});
  ctl.write();
  assert.equal(ctl.view().visible, false);
  ctl.offer({kind: 'check', name: 'FEST10004'});
  const rejecting = createUndoControl({now: () => 0, windowMs: 30000,
    send: () => Promise.reject(new Error('offline'))});
  rejecting.offer({kind: 'check', name: 'X'});
  const r = await rejecting.tap();
  assert.equal(r.ok, false);
  assert.equal(rejecting.view().visible, true);
  console.log('undo control ok');
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_undo_control_under_node(tmp_path):
    """Runs the pure undo control under node; it does not drive a browser."""
    path = tmp_path / "undo_check.js"
    path.write_text(NODE_SCRIPT)
    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True,
        env={**os.environ, "SCAN_JS": os.path.abspath(os.path.join(STATIC, "scan.js"))})
    assert result.returncode == 0, result.stderr + result.stdout
    assert "undo control ok" in result.stdout


# ---- review fixes ----

@pytest.mark.parametrize("kind,value", [("SERIAL", "SN4471"), ("MAC", "AA:BB:CC:DD:EE:09")])
def test_undo_is_refused_when_another_unit_holds_the_serial_or_mac(
        inv, tv_checklist, event, shift, kind, value):
    from app.inventory.ledger import add_identifier
    t, actor = tv_checklist, _actor(shift)
    if kind == "MAC":
        add_identifier(t.tv, "MAC", value)
        db.session.commit()
    check = _fail_and_retire(t, event, actor)
    add_identifier(t.tv2, kind, value)
    db.session.commit()
    with pytest.raises(InventoryError) as refused:
        undo_last(shift, now=_later(check, 5))
    assert str(refused.value) == f"{value} is now on another asset; nothing was undone."
    db.session.rollback()
    db.session.refresh(t.tv)
    assert t.tv.condition == "RETIRED" and InvCheck.query.get(check.id).status == "FAILED"
    assert not [i for i in InvUnitIdentifier.query.filter_by(unit_id=t.tv.id)
                if i.retired_at is None]


def test_undo_of_a_repair_check_is_refused_after_someone_retires_the_unit(
        inv, tv_checklist, event, shift):
    from app.inventory.ledger import set_condition
    t, actor = tv_checklist, _actor(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "NO", t.remote.id: "YES"}, actor, failed_condition="REPAIR")
    db.session.commit()
    set_condition(t.tv, "RETIRED")
    db.session.commit()
    with pytest.raises(InventoryError) as refused:
        undo_last(shift, now=_later(check, 5))
    assert str(refused.value) == CHANGED
    assert t.tv.condition == "RETIRED"


def test_undoing_a_recheck_keeps_the_earlier_pass(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    first = open_check(t.tv, "UNIT", actor, event)
    finish_check(first, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    db.session.commit()
    second = open_check(t.tv, "UNIT", actor, event)
    finish_check(second, {t.power.id: "NO", t.remote.id: "YES"}, actor,
                 failed_condition="REPAIR")
    db.session.commit()
    assert prep_status(t.tv) == "FAILED_CHECK"
    undo_last(shift, now=_later(second, 5))
    assert prep_status(t.tv) == "CHECKED"
    assert any(x.startswith("Works \u00b7 ") for x in card_for(resolve("TECH00412"), event)["lines"])
    from app.inventory.checks import last_result
    assert last_result(t.tv)["result"] == "Works"


def test_undo_refuses_when_a_newer_check_exists(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    other = EventCycle(code="MWEST27", name="MAGWest 2027", is_active=True)
    db.session.add(other)
    db.session.flush()
    first = open_check(t.tv, "UNIT", actor, event)
    finish_check(first, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    second = open_check(t.tv, "UNIT", ADMIN, other)
    finish_check(second, {t.power.id: "YES", t.remote.id: "YES"}, ADMIN)
    db.session.commit()
    with pytest.raises(InventoryError) as refused:
        undo_last(shift, now=_later(first, 5))
    assert str(refused.value) == "A newer check of this asset exists."


def test_undoing_the_only_check_leaves_the_unit_unchecked(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    check = open_check(t.tv, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    db.session.commit()
    undo_last(shift, now=_later(check, 5))
    from app.inventory.checks import last_result
    assert prep_status(t.tv) is None and last_result(t.tv) is None


def test_the_movements_log_names_an_undo(inv, shift):
    actor = _actor(shift)
    tote = make_container(inv, "C-0011", kind="BIN")
    add_stock(inv.pens, tote, 12, ADMIN)
    db.session.commit()
    count = start_count(tote, actor)
    submit_count(count, [CountLineIn(inv.pens.id, kept=20)], actor)
    db.session.commit()
    undo_last(shift, now=_later(count, 5))
    db.session.commit()
    texts = [describe_movement(m) for m in
             InvMovement.query.filter_by(note="undo")]
    assert texts == [f"Undo: count removed 8 {inv.pens.item_name} from C-0011"]


def test_the_finish_payload_names_a_retired_unit_by_its_number(client, inv, tv_checklist, event):
    t = tv_checklist
    enrolled_client(client, station_code="UNIT")
    start_shift(db.session.query(__import__("app.models", fromlist=["x"]).InvDevice).one(), "Sam")
    db.session.commit()
    opened = client.post("/inv/scan/api/unit/scan",
                         json={"client_request_id": _rid(), "code": "TECH00412"}).get_json()
    out = client.post("/inv/scan/api/unit/finish", json={
        "client_request_id": _rid(), "check_id": opened["check_id"],
        "answers": {str(t.power.id): "NO", str(t.remote.id): "YES"},
        "failed_condition": "RETIRED"}).get_json()
    assert out["condition"] == "RETIRED" and out["code"] == "TECH00412"


def _bound_unit(t, serial, label_actor):
    from app.inventory import labels
    from app.inventory.ledger import add_identifier
    unit = add_unit(t.tv_item, None, ADMIN, [("SERIAL", serial)])
    code = labels.allocate(labels.PREFIX_UNIT, 1)[0]
    db.session.commit()
    add_identifier(unit, "FESTOPS_LABEL", code)
    db.session.commit()
    return unit


def test_undo_reports_a_label_this_shift_bound(inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    unit = _bound_unit(t, "NEW1", actor)
    check = open_check(unit, "UNIT", actor, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    db.session.commit()
    assert "The label stays on it." in undo_last(shift, now=_later(check, 5))["message"]


def test_a_label_bound_before_this_check_is_not_reported_as_staying(
        inv, tv_checklist, event, shift):
    t, actor = tv_checklist, _actor(shift)
    unit = _bound_unit(t, "NEW2", ADMIN)
    open_check(unit, "UNIT", ADMIN, event)
    db.session.commit()
    mine = open_check(unit, "UNIT", actor, event)
    finish_check(mine, {t.power.id: "YES", t.remote.id: "YES"}, actor)
    db.session.commit()
    assert "label stays" not in undo_last(shift, now=_later(mine, 5))["message"]


def test_undo_names_a_unit_with_no_number_by_its_name(inv, tv_checklist, event, shift):
    from app.inventory.ledger import add_unit
    t, actor = tv_checklist, _actor(shift)
    bare = add_unit(t.tv_item, t.where, ADMIN, [], name="Lobby TV")
    db.session.commit()
    check = open_check(bare, "UNIT", actor, event)
    finish_check(check, {t.power.id: "NO", t.remote.id: "YES"}, actor, failed_condition="REPAIR")
    db.session.commit()
    message = undo_last(shift, now=_later(check, 5))["message"]
    assert message.startswith("Check of Lobby TV undone.")
    assert f"unit {bare.id}" not in message


def test_movement_text_for_a_unit_with_no_number_never_uses_the_id(inv):
    from app.inventory.ledger import add_unit
    unit = add_unit(inv.laptop, None, ADMIN, [], name="Lobby Laptop")
    db.session.commit()
    text = describe_movement(InvMovement.query.filter_by(unit_id=unit.id).one())
    assert text == "Added Laptop Lobby Laptop, not placed yet"
