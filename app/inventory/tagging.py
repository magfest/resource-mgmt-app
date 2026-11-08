"""Tag station: attach a pre-printed FEST tag to a named unit and print its combined label.

Nothing here commits; the caller does, so a refusal rolls the whole link back.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_

from app import db
from app.inventory.audit import ACTION_TAG_LINK, ACTION_TAG_UNDO, audit_who, record_unit_change
from app.inventory.ledger import (
    NUMBER_KINDS, Actor, InventoryError, add_identifier, retire_identifier,
)
from app.inventory.names import name_key
from app.inventory.printing import cancel_job, printer_for
from app.inventory.receive import check_tag, label_answer, label_job, undo_message
from app.inventory.undo import TOO_LATE, UNDO_WINDOW
from app.models import InvPrintJob, InvTagLink, InvUnit, InvUnitIdentifier
from app.models.constants import (
    INV_CONDITION_RETIRED, INV_IDENT_MAGFEST_TAG, INV_JOB_OWNED_UNIT, INV_JOB_QUEUED,
)

NOTHING_TO_UNDO = "Only the last unit this scanner tagged can be undone."


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
    return (db.session.query(InvUnit)
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
             client_request_id: str | None = None) -> dict:
    """Attach `tag` to a named unit that has no number, record it and queue the label.

    Two scanners can pass the first read together. The row lock serializes them on
    PostgreSQL; the number is read again after the lock, so the second one is refused.
    """
    tag = (tag or "").strip()
    unit = _locked_unit(unit_id)
    held = _held_number(unit)
    if held is not None:
        raise InventoryError(f"{unit.name or 'This unit'} already has {held.value}.")
    check_tag(tag)
    # Checked before anything is written, so a missing route leaves no tag behind.
    printer_for(INV_JOB_OWNED_UNIT)
    identifier = add_identifier(unit, INV_IDENT_MAGFEST_TAG, tag)
    db.session.add(InvTagLink(
        unit_id=unit.id, identifier_id=identifier.id, shift_id=actor.shift_id,
        device_id=actor.device_id, performed_by_user_id=actor.user_id))
    record_unit_change(unit.id, ACTION_TAG_LINK,
                       {"codes": {"old": [], "new": [identifier.value]}}, audit_who(actor))
    job = label_job(unit, actor, client_request_id)
    return {**label_answer(unit, job), "name": unit.name}


def _newest_link(actor: Actor) -> InvTagLink | None:
    query = db.session.query(InvTagLink).filter(InvTagLink.undone_at.is_(None))
    if actor.shift_id is not None:
        query = query.filter(InvTagLink.shift_id == actor.shift_id,
                             InvTagLink.device_id == actor.device_id)
    else:
        query = query.filter(InvTagLink.performed_by_user_id == actor.user_id,
                             InvTagLink.shift_id.is_(None))
    return query.order_by(InvTagLink.id.desc()).first()


def undo_tag_link(unit_id: int, actor: Actor) -> str:
    """Retire the tag this actor linked last, cancel its queued label and say what to do.

    Only the actor's newest link can be undone, and only inside the receive undo window.
    A label already collected cannot be recalled; the message tells the person to discard it.
    """
    link = _newest_link(actor)
    if link is None or link.unit_id != unit_id:
        raise InventoryError(NOTHING_TO_UNDO)
    unit = _locked_unit(unit_id)
    link = (db.session.query(InvTagLink).filter_by(id=link.id)
            .with_for_update().populate_existing().one())
    if link.undone_at is not None:
        raise InventoryError(NOTHING_TO_UNDO)
    if datetime.utcnow() - link.created_at > UNDO_WINDOW:
        raise InventoryError(TOO_LATE)
    identifier = db.session.get(InvUnitIdentifier, link.identifier_id)
    retire_identifier(identifier)
    link.undone_at = datetime.utcnow()
    jobs = (db.session.query(InvPrintJob)
            .filter(and_(InvPrintJob.unit_id == unit.id,
                         InvPrintJob.created_at >= link.created_at))
            .with_for_update().populate_existing().all())
    for job in jobs:
        if job.status == INV_JOB_QUEUED:
            cancel_job(job)
    record_unit_change(unit.id, ACTION_TAG_UNDO,
                       {"codes": {"old": [identifier.value], "new": []}}, audit_who(actor))
    db.session.expire(unit, ["identifiers"])
    return undo_message(unit.id)
