"""Flags: a problem a scanner volunteer reports for a lead to fix at a laptop.

A flag names one asset or one container. Nothing here commits; the caller does.
"""
from __future__ import annotations

from datetime import datetime

from app import db
from app.inventory.ledger import Actor, InventoryError
from app.inventory.resolver import (
    KIND_AMBIGUOUS, KIND_CONTAINER, KIND_UNIT, Resolution, resolve,
)
from app.models import InvContainer, InvFlag, InvUnit
from app.models.constants import INV_FLAG_REASONS

# Look up cards and the scanner flag screen both read these words.
FLAG_WORDS = {"WRONG_DATA": "Wrong details", "DAMAGED": "Damaged",
              "UNKNOWN_ITEM": "Item not in the system", "OTHER": "Other"}

NOT_FLAGGABLE = "Flag the container it is in instead. Scan the container label."
ALREADY_FLAGGED = "That is already flagged."
NOTE_NEEDED = "Say what is wrong."
PICK_UNIT = "Two assets match. Scan again and tap the one in your hand."

NOTE_LENGTH = InvFlag.note.type.length


def clean_note(text) -> str | None:
    """Normalize line endings, trim, and cap at the column length. Blank becomes None."""
    if text is None:
        return None
    if not isinstance(text, str):
        raise InventoryError("A note is text.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:NOTE_LENGTH].strip() or None


def raise_flag(actor: Actor, station_code: str, reason: str, unit: InvUnit | None = None,
               container: InvContainer | None = None, note: str | None = None) -> InvFlag:
    """Open a flag on exactly one of a unit or a container.

    Refuses an unknown reason, an OTHER with no note, and a second open flag with
    the same subject and reason.
    """
    if (unit is None) == (container is None):
        raise InventoryError("Flag one asset or one container.")
    if reason not in INV_FLAG_REASONS:
        raise InventoryError("Pick a reason.")
    note = clean_note(note)
    if reason == "OTHER" and note is None:
        raise InventoryError(NOTE_NEEDED)
    subject = (InvFlag.unit_id == unit.id) if unit is not None \
        else (InvFlag.container_id == container.id)
    if db.session.query(InvFlag.id).filter(
            subject, InvFlag.reason == reason, InvFlag.resolved_at.is_(None)).first():
        raise InventoryError(ALREADY_FLAGGED)
    flag = InvFlag(unit_id=unit.id if unit else None,
                   container_id=container.id if container else None,
                   station_code=station_code, reason=reason, note=note,
                   performed_by_user_id=actor.user_id, shift_id=actor.shift_id,
                   device_id=actor.device_id)
    db.session.add(flag)
    db.session.flush()
    return flag


def resolve_flag(flag: InvFlag, user_id: str, note: str) -> None:
    """Close an open flag with the lead's note."""
    if flag.resolved_at is not None:
        raise InventoryError("That flag is already resolved.")
    note = clean_note(note)
    if note is None:
        raise InventoryError("Say what was done.")
    flag.resolved_at = datetime.utcnow()
    flag.resolved_by_user_id = user_id
    flag.resolution_note = note
    db.session.flush()


def subject_for(resolution: Resolution) -> tuple[InvUnit | None, InvContainer | None]:
    """The unit or container a scan names, or an InventoryError saying what to scan instead.

    Retired units and inactive containers still resolve, so they can be flagged.
    A scanned item, an unused label or an unknown code cannot; the flag goes on the
    container the thing is in.
    """
    if resolution.kind == KIND_UNIT:
        return resolution.unit, None
    if resolution.kind == KIND_CONTAINER:
        return None, resolution.container
    if resolution.kind == KIND_AMBIGUOUS and all(isinstance(c, InvUnit)
                                                 for c in resolution.candidates):
        raise InventoryError(PICK_UNIT)
    raise InventoryError(NOT_FLAGGABLE)


def find_subject(code, unit_id) -> tuple[InvUnit | None, InvContainer | None]:
    """Resolve the subject a scanner sent: a unit id wins over a code."""
    if unit_id is not None:
        unit = db.session.get(InvUnit, unit_id) if type(unit_id) is int else None
        if unit is None:
            raise InventoryError("That asset was not found. Scan it again.")
        return unit, None
    return subject_for(resolve(code if isinstance(code, str) else None))
