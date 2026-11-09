"""One history timeline for a unit: sources, sentences, order and query count."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import db
from app.inventory.audit import ACTION_NAME, record_unit_change
from app.inventory.flags import raise_flag, resolve_flag
from app.inventory.history import unit_history
from app.inventory.ledger import Actor, add_unit, move_unit
from app.inventory.notes import add_note
from app.inventory.printing import local_when
from app.models import ConfigAuditEvent, InvDevice, InvFlag, InvMovement, InvShift, InvUnitNote
from app.inventory.allocation import allocate, move, release
from app.models import InvAllocationEvent
from tests.inventory.helpers import make_asset, make_container, supply_line

T0 = datetime(2026, 11, 1, 12, 0)


@pytest.fixture
def unit(inv):
    unit = add_unit(inv.laptop, None, Actor(user_id="test:invadmin"),
                    [("FESTOPS_LABEL", "U-0001")])
    db.session.commit()
    return unit


def test_history_merges_sources_newest_first(inv, unit):
    pallet = make_container(inv, "C-9001", name="Pallet 9")
    actor = Actor(user_id="test:invadmin")
    move_unit(unit, pallet, actor)
    flag = raise_flag(actor, "UNIT", "DAMAGED", unit=unit)
    resolve_flag(flag, "test:invadmin", "Replaced the lid")
    add_note(unit, "ADMIN", "Invented note", "test:invadmin")
    movements = db.session.query(InvMovement).order_by(InvMovement.id).all()
    movements[0].created_at = T0
    movements[1].created_at = T0 + timedelta(hours=1)
    flag.created_at = T0 + timedelta(hours=2)
    flag.resolved_at = T0 + timedelta(hours=3)
    db.session.query(InvUnitNote).one().created_at = T0 + timedelta(hours=4)
    db.session.commit()
    assert [line.text for line in unit_history(unit)] == [
        "added an admin note", "resolved a flag: Damaged", "flagged it: Damaged",
        "moved it to Pallet 9", "added it, not placed yet"]


def test_name_change_sentence(inv, unit):
    record_unit_change(unit.id, ACTION_NAME, {"name": {"old": "TV 3", "new": "Invented TV"}},
                       "test:invadmin")
    db.session.commit()
    texts = [line.text for line in unit_history(unit)]
    assert "changed the name from TV 3 to Invented TV" in texts


def test_shift_actor_reads_as_scanner(inv, unit):
    device = InvDevice(name="Scanner 1", station_code="LOOKUP", token_hash="x" * 8,
                      enrolled_by_user_id="test:invadmin")
    db.session.add(device)
    db.session.flush()
    shift = InvShift(device_id=device.id, person_name="Invented Person")
    db.session.add(shift)
    db.session.flush()
    pallet = make_container(inv, "C-9002", name="Pallet 2")
    move_unit(unit, pallet, Actor(shift_id=shift.id, device_id=device.id))
    db.session.commit()
    moved = [line for line in unit_history(unit) if line.text.startswith("moved")]
    assert moved[0].who == "Invented Person (scanner)"


def test_show_all_is_not_capped(inv, unit):
    for i in range(60):
        record_unit_change(unit.id, ACTION_NAME, {"name": {"old": f"A{i}", "new": f"A{i + 1}"}},
                           "test:invadmin")
    db.session.commit()
    assert len(unit_history(unit)) >= 60


def _selects(fn):
    seen = [0]

    def count(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            seen[0] += 1

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    return seen[0]


def _allocation_events(unit, alloc, count):
    for i in range(count):
        db.session.add(InvAllocationEvent(
            unit_id=unit.id, action="ALLOCATE", event_cycle_id=alloc.event.id,
            department_id=alloc.alpha.id, line_label=f"words {i}",
            performed_by_user_id="test:invadmin"))


def test_allocation_changes_show_in_history(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    allocate([unit], supply_line(alloc, alloc.alpha, 1, notes="one 4K").id, "test:invadmin")
    move(unit, supply_line(alloc, alloc.beta, 1, notes="any").id, "test:invadmin")
    release(unit, "test:invadmin")
    # stamp the three events at now + 1, 2 and 3 hours, so they sort above the ADD movement
    events = db.session.query(InvAllocationEvent).order_by(InvAllocationEvent.id).all()
    for hours, ev in enumerate(events, start=1):
        ev.created_at = datetime.utcnow() + timedelta(hours=hours)
    db.session.commit()
    texts = [l.text for l in unit_history(unit)][:3]
    assert texts == ["released it from Beta Team for TST27: any",
                     "moved its allocation to Beta Team for TST27: any",
                     "allocated it to Alpha Team for TST27: one 4K"]


def test_history_query_count_is_fixed(inv, alloc):
    actor = Actor(user_id="test:invadmin")
    small = add_unit(inv.laptop, None, actor, [("FESTOPS_LABEL", "U-0002")])
    big = add_unit(inv.laptop, None, actor, [("FESTOPS_LABEL", "U-0003")])
    containers = [make_container(inv, f"C-91{i:02d}", name=f"Place {i}") for i in range(20)]
    for container in containers[:2]:
        move_unit(small, container, actor)
    for container in containers:
        move_unit(big, container, actor)
    _allocation_events(big, alloc, 5)
    _allocation_events(small, alloc, 1)
    db.session.commit()
    db.session.expire_all()
    first = _selects(lambda: unit_history(small))
    db.session.expire_all()
    second = _selects(lambda: unit_history(big))
    assert first == second


def test_local_when_format():
    assert local_when(datetime(2026, 11, 7, 22, 53)) == "Nov 7 at 5:53 PM"
