"""The allocation tables hold one row per active allocation and outlive their line."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import InvAllocation, SupplyItem
from tests.inventory.helpers import make_allocation, make_asset, supply_line, techops_line


def test_one_asset_cannot_hold_two_allocations(alloc):
    unit = make_asset(alloc.tv, "U-0001")
    make_allocation(unit, supply_line(alloc, alloc.alpha, 2))
    db.session.commit()
    with pytest.raises(IntegrityError):
        make_allocation(unit, supply_line(alloc, alloc.beta, 2))
        db.session.flush()


def test_deleting_a_line_keeps_its_allocation(alloc):
    line = techops_line(alloc, alloc.alpha)
    row = make_allocation(make_asset(alloc.printer, "U-0002"), line, words="front desk")
    db.session.commit()
    db.session.delete(line)
    db.session.commit()
    db.session.expire_all()
    kept = db.session.get(InvAllocation, row.id)
    assert kept.work_line_id is None and kept.line_label == "front desk"


def test_a_raw_item_insert_starts_without_pre_allocation(alloc):
    db.session.execute(text(
        "INSERT INTO supply_items (category_id, item_name, unit, is_serialized, is_orderable,"
        " is_expendable, is_limited, is_popular, notes_required, needs_asset_tag, created_at, updated_at, is_active)"
        " VALUES (:c, 'Raw Item', 'each', 0, 1, 1, 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)"),
        {"c": alloc.tv.category_id})
    db.session.commit()
    row = SupplyItem.query.filter_by(item_name="Raw Item").one()
    assert row.needs_pre_allocation is False and row.techops_service_type_id is None
