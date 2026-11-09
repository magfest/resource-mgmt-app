"""Allocation rules: which assets may fill which order lines.

This module will hold the lookups and the allocate, move and release rules.
It reads department demand and never writes it. Nothing here commits; the
calling route owns the transaction.
"""
from __future__ import annotations

from app import db
from app.models import InvAllocation, InvUnit


def active_allocation_count(item_id: int) -> int:
    """Count allocations held by assets of one item."""
    return (db.session.query(InvAllocation).join(InvUnit, InvUnit.id == InvAllocation.unit_id)
            .filter(InvUnit.item_id == item_id).count())


def current_allocation(unit_id: int) -> InvAllocation | None:
    """Return the asset's allocation, or None when it is free."""
    return db.session.query(InvAllocation).filter_by(unit_id=unit_id).first()
