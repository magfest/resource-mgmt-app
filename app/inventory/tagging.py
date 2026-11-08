"""Attach a pre-printed asset tag to a named unit that has no number.

The Unit station links tags through here and finds unnumbered units by name.
Nothing here commits; the caller does, so a refusal rolls the whole link back.
"""
from __future__ import annotations

from sqlalchemy.orm import selectinload

from app import db
from app.inventory.audit import ACTION_TAG_LINK, audit_who, record_unit_change
from app.inventory.codes import normalize_code
from app.inventory.ledger import NUMBER_KINDS, Actor, InventoryError, add_identifier
from app.inventory.names import name_key
from app.inventory.printing import printer_for
from app.inventory.receive import (
    AlreadyReceived, _active_holder, check_tag, label_answer, label_job,
)
from app.models import (
    InvGearKind, InvTagLink, InvUnit, InvUnitIdentifier, InvUnitValue, SupplyItem,
)
from app.models.constants import INV_CONDITION_RETIRED, INV_IDENT_MAGFEST_TAG, INV_JOB_OWNED_UNIT


def tag_candidates(text: str, limit: int = 10) -> list[InvUnit]:
    """Live units whose name contains the text and that hold no tag or label.

    A unit with an MT tag counts as numbered: MT tags are stored as MAGFEST_TAG.
    """
    key = name_key(text)
    if key is None:
        return []
    escaped = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    numbered = (db.session.query(InvUnitIdentifier.id)
                .filter(InvUnitIdentifier.unit_id == InvUnit.id,
                        InvUnitIdentifier.kind.in_(NUMBER_KINDS),
                        InvUnitIdentifier.retired_at.is_(None)).exists())
    # spec_line and model_number read these for every candidate.
    return (db.session.query(InvUnit)
            .options(selectinload(InvUnit.item).selectinload(SupplyItem.gear_kind)
                     .selectinload(InvGearKind.fields),
                     selectinload(InvUnit.values).selectinload(InvUnitValue.field),
                     selectinload(InvUnit.values).selectinload(InvUnitValue.choice))
            .filter(InvUnit.name_key.like(f"%{escaped}%", escape="\\"),
                    InvUnit.condition != INV_CONDITION_RETIRED, ~numbered)
            .order_by(InvUnit.name_key, InvUnit.id).limit(limit).all())


def _locked_unit(unit_id) -> InvUnit:
    unit = (db.session.query(InvUnit).filter_by(id=unit_id if type(unit_id) is int else -1)
            .with_for_update().populate_existing().one_or_none())
    if unit is None or unit.condition == INV_CONDITION_RETIRED:
        raise InventoryError("That unit is not in use.")
    return unit


def _held_number(unit: InvUnit) -> InvUnitIdentifier | None:
    # Queried after the row lock; unit.identifiers may be loaded and stale.
    return (db.session.query(InvUnitIdentifier)
            .filter(InvUnitIdentifier.unit_id == unit.id,
                    InvUnitIdentifier.kind.in_(NUMBER_KINDS),
                    InvUnitIdentifier.retired_at.is_(None))
            .order_by(InvUnitIdentifier.id).first())


def link_tag(unit_id: int, tag: str, actor: Actor,
             client_request_id: str | None = None, queue_label: bool = True) -> dict:
    """Attach `tag` to a named unit that has no number, record it and queue the label.

    Two scanners can pass the first read together. The row lock serializes them on
    PostgreSQL; the number is read again after the lock, so the second one is refused.

    Args:
        queue_label: False skips the label and the print route check. The Unit
            station passes False because its finish queues the label, and a
            unit retired by that finish must get none.
    """
    tag = (tag or "").strip()
    unit = _locked_unit(unit_id)
    held = _held_number(unit)
    if held is not None:
        raise InventoryError(f"{unit.name or 'This unit'} already has {held.value}.")
    check_tag(tag)
    if queue_label:
        # Checked before anything is written, so a missing route leaves no tag behind.
        printer_for(INV_JOB_OWNED_UNIT)
    try:
        identifier = add_identifier(unit, INV_IDENT_MAGFEST_TAG, tag)
    except InventoryError:
        # Another scanner can take the tag after check_tag; the unique index picks one.
        # The loser gets the same screen as a plain repeat.
        held = _active_holder(INV_IDENT_MAGFEST_TAG, normalize_code(tag))
        if held is not None:
            raise AlreadyReceived(held) from None
        raise
    db.session.add(InvTagLink(
        unit_id=unit.id, identifier_id=identifier.id, shift_id=actor.shift_id,
        device_id=actor.device_id, performed_by_user_id=actor.user_id))
    record_unit_change(unit.id, ACTION_TAG_LINK,
                       {"codes": {"old": [], "new": [identifier.value]}}, audit_who(actor))
    if not queue_label:
        return {"code": identifier.value, "unit_id": unit.id, "name": unit.name}
    job = label_job(unit, actor, client_request_id)
    return {**label_answer(unit, job), "name": unit.name}
