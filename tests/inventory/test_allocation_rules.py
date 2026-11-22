"""Allocate, move and release: the rules that write allocations and their history."""
import re

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app import db
from app.inventory import allocation
from app.inventory.allocation import (
    allocate, current_allocation, line_lock_query, move, release,
)
from app.inventory.ledger import InventoryError, set_condition
from app.models import InvAllocation, InvAllocationEvent, WorkItem
from app.models.constants import INV_OWNERSHIP_RENTED
from tests.inventory.helpers import make_allocation, make_asset, supply_line, techops_line


def _actions(unit):
    return [e.action for e in db.session.query(InvAllocationEvent)
            .filter_by(unit_id=unit.id).order_by(InvAllocationEvent.id)]


def test_allocate_writes_rows_and_events(alloc):
    line = supply_line(alloc, alloc.alpha, 4, notes="one 4K")
    units = [make_asset(alloc.tv, f"U-000{i}") for i in (1, 2)]
    allocate(units, line.id, "test:techopsadmin")
    rows = db.session.query(InvAllocation).order_by(InvAllocation.unit_id).all()
    assert [(r.work_line_id, r.line_label, r.event_cycle_id) for r in rows] == \
        [(line.id, "one 4K", alloc.event.id)] * 2
    events = db.session.query(InvAllocationEvent).all()
    assert {(e.action, e.performed_by_user_id) for e in events} == {("ALLOCATE", "test:techopsadmin")}


def test_allocate_returns_the_line_with_the_new_rows(alloc):
    line = supply_line(alloc, alloc.alpha, 4, notes="one 4K")
    result = allocate([make_asset(alloc.tv, "U-0001")], line.id, "test:invadmin")
    assert (result.work_line_id, result.allocated, result.left) == (line.id, 1, 3)


def test_over_approved_is_allowed_up_to_requested(alloc):
    line = supply_line(alloc, alloc.alpha, 3, approved=1)
    units = [make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3)]
    result = allocate(units, line.id, "test:invadmin")
    assert result.allocated == 3
    assert result.warnings == ["Over the approved 1"]


def test_a_full_line_refuses_the_next_asset(alloc):
    line = supply_line(alloc, alloc.alpha, 1, notes="one 4K")
    allocate([make_asset(alloc.tv, "U-0001")], line.id, "test:invadmin")
    with pytest.raises(InventoryError, match=re.escape(
            "Alpha Team · one 4K has 0 left, and you picked 1.")):
        allocate([make_asset(alloc.tv, "U-0002")], line.id, "test:invadmin")


def test_a_batch_past_requested_writes_nothing(alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    units = [make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3)]
    with pytest.raises(InventoryError, match=re.escape(
            "Alpha Team · one 4K has 2 left, and you picked 3.")):
        allocate(units, line.id, "test:invadmin")
    assert db.session.query(InvAllocation).count() == 0
    assert db.session.query(InvAllocationEvent).count() == 0


def test_a_batch_with_one_bad_asset_writes_nothing(alloc):
    line = supply_line(alloc, alloc.alpha, 3)
    units = [make_asset(alloc.tv, "U-0001"), make_asset(alloc.tv, "U-0002", condition="LOST")]
    with pytest.raises(InventoryError, match="U-0002 is lost, so it cannot be allocated."):
        allocate(units, line.id, "test:invadmin")
    assert db.session.query(InvAllocation).count() == 0
    assert db.session.query(InvAllocationEvent).count() == 0


def test_the_same_asset_ticked_twice_counts_once(alloc):
    line = supply_line(alloc, alloc.alpha, 1)
    unit = make_asset(alloc.tv, "U-0001")
    assert allocate([unit, unit], line.id, "test:invadmin").allocated == 1


def test_a_cut_line_takes_no_more(alloc):
    line = supply_line(alloc, alloc.alpha, 3, notes="one 4K")
    allocate([make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3)], line.id, "test:invadmin")
    line.supply_detail.quantity_requested = 2
    db.session.commit()
    with pytest.raises(InventoryError, match=re.escape(
            "Alpha Team · one 4K has 0 left, and you picked 1.")):
        allocate([make_asset(alloc.tv, "U-0004")], line.id, "test:invadmin")
    assert db.session.query(InvAllocation).count() == 3


def _in_repair(alloc):
    return [make_asset(alloc.tv, "U-0001", condition="REPAIR")], \
        supply_line(alloc, alloc.alpha, 2).id


def _found(alloc):
    return [make_asset(alloc.tv, "U-0001", condition="FOUND")], \
        supply_line(alloc, alloc.alpha, 2).id


def _already_allocated(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    allocate([unit], supply_line(alloc, alloc.alpha, 2, notes="one 4K").id, "test:invadmin")
    return [unit], supply_line(alloc, alloc.beta, 2).id


def _already_allocated_to_a_deleted_line(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    line = supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    allocate([unit], line.id, "test:invadmin")
    db.session.commit()
    db.session.delete(line)
    db.session.commit()
    return [unit], supply_line(alloc, alloc.beta, 2).id


def _wrong_line(alloc):
    return [make_asset(alloc.printer, "U-0001")], supply_line(alloc, alloc.alpha, 2).id


def _switch_off(alloc):
    alloc.tv.needs_pre_allocation = False
    return [make_asset(alloc.tv, "U-0001")], supply_line(alloc, alloc.alpha, 2).id


def _untracked(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    alloc.tv.is_serialized = False
    return [unit], supply_line(alloc, alloc.alpha, 2).id


def _draft(alloc):
    return [make_asset(alloc.tv, "U-0001")], \
        supply_line(alloc, alloc.alpha, 2, status="DRAFT").id


def _rejected(alloc):
    return [make_asset(alloc.tv, "U-0001")], \
        supply_line(alloc, alloc.alpha, 2, line_status="REJECTED").id


def _archived(alloc):
    line = supply_line(alloc, alloc.alpha, 2)
    db.session.get(WorkItem, line.work_item_id).is_archived = True
    return [make_asset(alloc.tv, "U-0001")], line.id


def _deleted(alloc):
    return [make_asset(alloc.tv, "U-0001")], 999_999


def _nothing_ticked(alloc):
    return [], supply_line(alloc, alloc.alpha, 2).id


NOT_OPEN = "That line is not open for allocation. Reload the page."


@pytest.mark.parametrize("build, message", [
    (_in_repair, "U-0001 is in repair, so it cannot be allocated."),
    (_found, "U-0001 is found but not checked, so it cannot be allocated."),
    (_already_allocated, "U-0001 is already allocated to Alpha Team · one 4K."),
    (_already_allocated_to_a_deleted_line,
     "U-0001 is already allocated to Alpha Team · one 4K."),
    (_wrong_line, "U-0001 does not fill that line."),
    (_switch_off, "Test TV is not set up for pre-allocation."),
    (_untracked, "Test TV is not set up for pre-allocation."),
    (_draft, NOT_OPEN),
    (_rejected, NOT_OPEN),
    (_archived, NOT_OPEN),
    (_deleted, NOT_OPEN),
    (_nothing_ticked, "Tick at least one asset."),
], ids=lambda value: getattr(value, "__name__", "")[1:] or None)
def test_refusals(alloc, build, message):
    units, line_id = build(alloc)
    db.session.flush()
    before = db.session.query(InvAllocationEvent).count()
    with pytest.raises(InventoryError, match=re.escape(message)):
        allocate(units, line_id, "test:invadmin")
    assert db.session.query(InvAllocationEvent).count() == before


def test_a_rented_asset_can_be_allocated(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    unit.ownership = INV_OWNERSHIP_RENTED
    allocate([unit], supply_line(alloc, alloc.alpha, 1).id, "test:invadmin")
    assert current_allocation(unit.id) is not None


def test_a_techops_line_takes_an_item_that_fills_its_service(alloc):
    line = techops_line(alloc, alloc.alpha)
    unit = make_asset(alloc.printer, "U-0001")
    allocate([unit], line.id, "test:invadmin")
    assert current_allocation(unit.id).line_label == "Printer"


def test_a_raced_insert_is_refused_not_raised(alloc, monkeypatch):
    unit = make_asset(alloc.tv, "U-0001")
    make_allocation(unit, supply_line(alloc, alloc.beta, 1))
    monkeypatch.setattr(allocation, "current_allocation", lambda unit_id: None)
    with pytest.raises(InventoryError, match="Someone allocated one of those assets just now."):
        allocate([unit], supply_line(alloc, alloc.alpha, 1).id, "test:invadmin")
    # The route rolls back after a refusal; the session must still take that rollback.
    db.session.rollback()
    assert db.session.query(InvAllocation).count() == 0


def test_the_line_lock_is_no_key_update_on_postgres(app):
    sql = str(line_lock_query(1).statement.compile(dialect=postgresql.dialect()))
    assert "FOR NO KEY UPDATE" in sql


def test_the_cap_reads_the_line_fresh_under_the_lock(alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    allocate([make_asset(alloc.tv, "U-0001")], line.id, "test:invadmin")
    second = make_asset(alloc.tv, "U-0002")
    # Held, as a route that read the line would; the session keeps only weak references.
    detail = line.supply_detail
    assert detail.quantity_requested == 2
    # Another request cut the line; the ORM copy in this session still says 2.
    db.session.execute(text("UPDATE supply_order_line_details SET quantity_requested = 1"
                            " WHERE work_line_id = :id"), {"id": line.id})
    with pytest.raises(InventoryError, match=re.escape(
            "Alpha Team · one 4K has 0 left, and you picked 1.")):
        allocate([second], line.id, "test:invadmin")


def test_move_changes_the_line_and_appends_a_move_event(alloc):
    unit = make_asset(alloc.printer, "U-0001")
    allocate([unit], supply_line(alloc, alloc.alpha, 1, item=alloc.printer).id, "test:invadmin")
    target = techops_line(alloc, alloc.beta, location="Main stage")
    result = move(unit, target.id, "test:techopsadmin")
    row = current_allocation(unit.id)
    assert (row.work_line_id, row.department_id, row.line_label) == \
        (target.id, alloc.beta.id, "Main stage")
    assert (result.work_line_id, result.allocated) == (target.id, 1)
    assert _actions(unit) == ["ALLOCATE", "MOVE"]
    event = db.session.query(InvAllocationEvent).filter_by(action="MOVE").one()
    assert (event.work_line_id, event.department_id, event.line_label,
            event.performed_by_user_id) == (target.id, alloc.beta.id, "Main stage",
                                            "test:techopsadmin")


def test_move_refusals(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    line = supply_line(alloc, alloc.alpha, 2)
    with pytest.raises(InventoryError, match="U-0001 is not allocated."):
        move(unit, line.id, "test:invadmin")
    allocate([unit], line.id, "test:invadmin")
    with pytest.raises(InventoryError, match="U-0001 is already on that line."):
        move(unit, line.id, "test:invadmin")
    with pytest.raises(InventoryError, match="U-0001 does not fill that line."):
        move(unit, techops_line(alloc, alloc.beta).id, "test:invadmin")
    with pytest.raises(InventoryError, match=re.escape(NOT_OPEN)):
        move(unit, supply_line(alloc, alloc.beta, 2, status="DRAFT").id, "test:invadmin")
    full = supply_line(alloc, alloc.beta, 1, notes="spare")
    allocate([make_asset(alloc.tv, "U-0002")], full.id, "test:invadmin")
    with pytest.raises(InventoryError, match=re.escape(
            "Beta Team · spare has 0 left, and you picked 1.")):
        move(unit, full.id, "test:invadmin")
    assert current_allocation(unit.id).work_line_id == line.id
    assert _actions(unit) == ["ALLOCATE"]


def test_an_asset_in_repair_keeps_its_allocation_but_cannot_move(alloc):
    unit = make_asset(alloc.tv, "U-0003")
    allocate([unit], supply_line(alloc, alloc.alpha, 2).id, "test:invadmin")
    set_condition(unit, "REPAIR")
    with pytest.raises(InventoryError, match="U-0003 is in repair, so it cannot be allocated."):
        move(unit, supply_line(alloc, alloc.beta, 2).id, "test:invadmin")
    assert current_allocation(unit.id) is not None


def test_move_off_a_deleted_line(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    line = supply_line(alloc, alloc.alpha, 2)
    allocate([unit], line.id, "test:invadmin")
    db.session.commit()
    db.session.delete(line)
    db.session.commit()
    target = supply_line(alloc, alloc.alpha, 2, notes="replacement")
    move(unit, target.id, "test:invadmin")
    assert (current_allocation(unit.id).work_line_id,
            current_allocation(unit.id).line_label) == (target.id, "replacement")


def test_release_deletes_the_row_and_keeps_the_history(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    line = supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    allocate([unit], line.id, "test:invadmin")
    released = release(unit, "test:invadmin")
    assert released.unit_id == unit.id
    assert current_allocation(unit.id) is None
    assert _actions(unit) == ["ALLOCATE", "RELEASE"]
    event = db.session.query(InvAllocationEvent).filter_by(action="RELEASE").one()
    assert (event.work_line_id, event.line_label) == (line.id, "one 4K")
    with pytest.raises(InventoryError, match="U-0001 is not allocated."):
        release(unit, "test:invadmin")
    assert _actions(unit) == ["ALLOCATE", "RELEASE"]


def test_release_after_the_line_is_deleted_uses_the_snapshot(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    line = supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    allocate([unit], line.id, "test:invadmin")
    db.session.commit()
    db.session.delete(line)
    db.session.commit()
    release(unit, "test:invadmin")
    event = db.session.query(InvAllocationEvent).filter_by(action="RELEASE").one()
    assert (event.work_line_id, event.line_label, event.department_id) == \
        (None, "one 4K", alloc.alpha.id)


def test_a_dangling_line_id_falls_back_to_the_snapshot(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    line = supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    row = make_allocation(unit, line, words="snapshot words")
    db.session.commit()
    # Dev SQLite does not enforce foreign keys, so a deleted line can leave its id behind.
    db.session.execute(text("PRAGMA foreign_keys=OFF"))
    db.session.execute(text("UPDATE inv_allocations SET work_line_id = 999999"))
    db.session.commit()
    db.session.refresh(row)
    assert allocation._live_words(row) == "snapshot words"
    release(unit, "test:invadmin")
    event = db.session.query(InvAllocationEvent).filter_by(action="RELEASE").one()
    assert event.line_label == "snapshot words"
