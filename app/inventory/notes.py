"""Append-only notes on a unit. Callers commit."""
from __future__ import annotations

from app import db
from app.inventory.ledger import InventoryError
from app.models import InvUnit, InvUnitNote
from app.models.constants import (
    INV_NOTE_ADMIN, INV_NOTE_SOURCE_EARLIER, INV_NOTE_SOURCE_IMPORT, INV_NOTE_SOURCE_PERSON, INV_NOTE_TYPES,
)

NOTE_TYPE_WORDS = {"ADMIN": "Admin", "PUBLIC": "Public", "PICKER": "Picker"}
SOURCE_WORDS = {"IMPORT": "From the old system", "EARLIER": "Earlier note"}
MAX_NOTE_LENGTH = 2000
_SOURCES = (INV_NOTE_SOURCE_PERSON, INV_NOTE_SOURCE_IMPORT, INV_NOTE_SOURCE_EARLIER)


def normalize_note(body: str | None) -> str:
    """Browsers submit textarea line breaks as CRLF; count each as one character."""
    return (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def add_note(unit: InvUnit, note_type: str, body: str | None, user_id: str | None,
             source: str = INV_NOTE_SOURCE_PERSON) -> InvUnitNote:
    """Add a note and flush. Raises InventoryError on bad input; does not commit."""
    if note_type not in INV_NOTE_TYPES:
        raise InventoryError("Unknown note type.")
    if source not in _SOURCES:
        raise InventoryError("Unknown note source.")
    text = normalize_note(body)
    if not text:
        raise InventoryError("A note cannot be blank.")
    if len(text) > MAX_NOTE_LENGTH:
        raise InventoryError(f"A note can be at most {MAX_NOTE_LENGTH} characters.")
    note = InvUnitNote(unit_id=unit.id, note_type=note_type, source=source,
                       body=text, created_by_user_id=user_id)
    db.session.add(note)
    db.session.flush()
    return note


def import_note(unit: InvUnit, body: str | None) -> InvUnitNote | None:
    """Add the old system's note once; a blank body or one already imported adds nothing."""
    text = normalize_note(body)
    if not text:
        return None
    held = {normalize_note(n.body) for n in unit_notes(unit.id) if n.source == INV_NOTE_SOURCE_IMPORT}
    if text in held:
        return None
    return add_note(unit, INV_NOTE_ADMIN, text, None, INV_NOTE_SOURCE_IMPORT)


def unit_notes(unit_id: int) -> list[InvUnitNote]:
    """Newest first; id breaks ties between notes made in the same instant."""
    return (InvUnitNote.query.filter_by(unit_id=unit_id)
            .order_by(InvUnitNote.created_at.desc(), InvUnitNote.id.desc()).all())


def latest_note(unit_id: int) -> InvUnitNote | None:
    notes = unit_notes(unit_id)
    return notes[0] if notes else None
